"""18.7B: Flask/SQLite isolados, HTTP sempre mockado, nenhuma API real."""
import io
import logging
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch
from flask import g, jsonify
from flask_wtf.csrf import generate_csrf
import requests
from app import create_app
from app.extensions import db
from app.integracoes.gophish.config import ConfiguracaoGophish as Config
from app.integracoes.gophish.backends import FakeBackend
from app.integracoes.gophish.content import ConteudoInseguro, validar_conteudo
from app.integracoes.gophish.service import GophishService, servico_gophish
from app.models import (Usuario, Campanha, Grupo, GrupoParticipante, Colaborador,
    Departamento, Cenario, TemplateEmail, LandingPage, Treinamento, ModuloTreinamento, Questao, Auditoria)

KEY = 'CHAVE_SINTETICA_NUNCA_REAL'
TOKEN = 'TOKEN_SINTETICO_NUNCA_REAL'
HTML = '<main><h1>Simulação interna ONE</h1><p>Orientação educativa.</p></main>'


def cfg(**values):
    return Config.do_ambiente(dict(GOPHISH_ENABLED='true', GOPHISH_BACKEND='fake', **values))


class ClienteTest(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Rede proibida')).start()
        self.http_guard = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real proibido')).start()
        self.addCleanup(patch.stopall)
        self.fake = FakeBackend()
        self.service = GophishService(cfg(), self.fake)

    def test_defaults_disabled(self):
        config = Config.do_ambiente({})
        self.assertEqual((config.enabled, config.send_enabled, config.backend), (False, False, 'fake'))
        fake = FakeBackend(); service = GophishService(config, fake)
        for result in (service.listar_grupos(), service.criar_grupo('Teste', []),
                       service.criar_pagina('Teste', HTML), service.verificar_conexao()):
            self.assertEqual(result['status'], 'disabled')
        self.assertEqual(fake.chamadas, [])

    def test_http_incomplete(self):
        for values in ({}, {'GOPHISH_URL':'https://gophish.example.test'}, {'GOPHISH_API_KEY':KEY}):
            config = Config.do_ambiente({'GOPHISH_ENABLED':'true', 'GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http', **values})
            self.assertEqual(GophishService(config).listar_grupos()['status'], 'not_configured')
        self.http_guard.assert_not_called()

    def test_config_rejects_invalid(self):
        for key, value in [('GOPHISH_ENABLED','yes'), ('GOPHISH_BACKEND','outro'),
                           ('GOPHISH_TIMEOUT_SECONDS','nan'), ('GOPHISH_TIMEOUT_SECONDS','0'),
                           ('GOPHISH_TIMEOUT_SECONDS','61'), ('GOPHISH_TIMEOUT_SECONDS','x')]:
            env={'GOPHISH_ENABLED':'true',key:value}
            self.assertEqual(Config.do_ambiente(env).estado(), 'disabled' if key == 'GOPHISH_ENABLED' else 'not_configured')
        for url in ('http://host', 'https://user:pass@host', 'https://host?api_key='+KEY,
                    'https://host/path', 'https://host/#x', 'https://host:invalid', 'https://host\\evil', 'https://host %20' ):
            config=Config.do_ambiente({'GOPHISH_ENABLED':'true', 'GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
                                      'GOPHISH_URL':url, 'GOPHISH_API_KEY':KEY})
            self.assertEqual(config.estado(), 'not_configured')

    def test_send_never_enabled(self):
        config=cfg(GOPHISH_SEND_ENABLED='true')
        self.assertFalse(config.send_enabled)
        service=GophishService(config, self.fake)
        self.assertEqual(service.executar_campanha()['status'],'forbidden')
        self.assertEqual(self.fake.chamadas,[])
        self.assertEqual(GophishService(Config()).executar_campanha()['status'],'disabled')

    def test_fake_crud_and_health_no_http(self):
        for create, obtain, update, listing, args in (
            (self.service.criar_grupo,self.service.obter_grupo,self.service.atualizar_grupo,self.service.listar_grupos,('Teste',[])),
            (self.service.criar_template,self.service.obter_template,self.service.atualizar_template,self.service.listar_templates,('Teste','Assunto',HTML)),
            (self.service.criar_pagina,self.service.obter_pagina,self.service.atualizar_pagina,self.service.listar_paginas,('Teste',HTML))):
            result=create(*args); ident=result['data']['id']
            self.assertEqual(result['status'],'created'); self.assertTrue(obtain(ident)['ok'])
            self.assertEqual(update(ident,*args)['status'],'updated');self.assertEqual(len(listing()['data']),1)
        self.assertEqual(self.service.verificar_conexao()['status'],'connected')
        self.http_guard.assert_not_called();self.network.assert_not_called()
        self.assertNotIn(HTML,str(vars(self.fake)))
        self.assertNotIn(TOKEN,str(vars(self.fake)))
        self.service.criar_grupo('Teste', [{'email':'pessoa@example.test','token':TOKEN}])
        self.assertNotIn(TOKEN,str(vars(self.fake)))
        self.assertNotIn('pessoa@example.test',str(vars(self.fake)))

    def test_fake_status_classification(self):
        for http,status in ((404,'not_found'),(401,'auth_error'),(403,'forbidden'),(500,'server_error'),(302,'invalid_response')):
            self.fake.configurar_resposta('GET','/api/groups/1',http_status=http,data={'detail':KEY})
            result=self.service.obter_grupo(1)
            self.assertEqual(result['status'],status);self.assertIsNone(result['data'])
            self.assertNotIn(KEY,str(result));self.assertEqual(len(self.fake.chamadas),1)
            self.fake.chamadas.clear()
        for status in ('timeout','tls_error','invalid_response'):
            self.fake.configurar_resposta('GET','/api/groups/1',status=status)
            self.assertEqual(self.service.obter_grupo(1)['status'],status)

    def test_missing_and_invalid_id(self):
        for value in ({}, {'id':None}, {'id':True}, {'id':'1'}, {'id':0}, {'id':2}, []):
            self.fake.configurar_resposta('GET','/api/groups/1',http_status=200,data=value)
            self.assertEqual(self.service.obter_grupo(1)['status'],'invalid_response')
        self.fake.configurar_resposta('POST','/api/groups/',http_status=201,data={})
        self.assertEqual(self.service.criar_grupo('Teste',[])['status'],'invalid_response')
        for value in (True, '1', -1, '../../x?token='+TOKEN):
            before=len(self.fake.chamadas)
            self.assertEqual(self.service.obter_grupo(value)['status'],'invalid_request')
            self.assertEqual(len(self.fake.chamadas),before)

    def test_list_response_shape(self):
        for value in ({'id':1}, [None], [{'id':False}], None):
            self.fake.configurar_resposta('GET','/api/groups/',http_status=200,data=value)
            self.assertEqual(self.service.listar_grupos()['status'],'invalid_response')

    def test_pages_require_boolean_flags(self):
        for flags in ({}, {'capture_credentials':'false','capture_passwords':False}):
            self.fake.configurar_resposta('GET','/api/pages/1',http_status=200,data={'id':1,**flags})
            self.assertEqual(self.service.obter_pagina(1)['status'],'invalid_response')

    def test_http_tls_system_ca_and_no_redirect_proxy(self):
        config=Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
                                  'GOPHISH_URL':'https://gophish.example.test','GOPHISH_API_KEY':KEY})
        service=GophishService(config)
        response=Mock(status_code=200);response.json.return_value=[{'id':1,'html':TOKEN,'api_key':KEY}]
        with patch('requests.sessions.Session.request',return_value=response) as request:
            result=service.listar_grupos()
        self.assertEqual(result['data'],[{'id':1}])
        kw=request.call_args.kwargs
        self.assertEqual(kw['verify'], ssl.get_default_verify_paths().cafile)
        self.assertIsNot(kw['verify'],False);self.assertFalse(kw['allow_redirects'])
        self.assertEqual(kw['timeout'],5);self.assertNotIn(KEY,request.call_args.args[1])
        from app.integracoes.gophish.backends import HttpBackend
        with patch('requests.Session') as session:
            instance=session.return_value.__enter__.return_value
            instance.request.return_value=response
            HttpBackend(config).request('GET','/api/groups/')
            self.assertIs(instance.trust_env,False)
        self.network.assert_not_called()

    def test_http_explicit_ca(self):
        with tempfile.NamedTemporaryFile() as ca:
            config=Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
                'GOPHISH_URL':'https://host','GOPHISH_API_KEY':KEY,'GOPHISH_CA_BUNDLE':ca.name})
            response=Mock(status_code=200);response.json.return_value=[]
            with patch('requests.sessions.Session.request',return_value=response) as request:
                self.assertTrue(GophishService(config).listar_grupos()['ok'])
            self.assertEqual(request.call_args.kwargs['verify'],ca.name)
        config=Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
            'GOPHISH_URL':'https://host','GOPHISH_API_KEY':KEY,'GOPHISH_CA_BUNDLE':'/inexistente/ca.pem'})
        self.assertEqual(config.estado(),'not_configured')

    def test_http_errors_sanitized_and_no_logs(self):
        service=GophishService(Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
            'GOPHISH_URL':'https://host','GOPHISH_API_KEY':KEY}))
        stream=io.StringIO();handler=logging.StreamHandler(stream);logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler,handler)
        for error,status in ((requests.exceptions.Timeout(KEY+TOKEN),'timeout'),
                            (requests.exceptions.SSLError(KEY+TOKEN),'tls_error'),
                            (requests.exceptions.ConnectionError(KEY+TOKEN),'connection_error')):
            with patch('requests.sessions.Session.request',side_effect=error):
                result=service.listar_grupos()
            self.assertEqual(result['status'],status)
            self.assertNotIn(KEY,str(result));self.assertNotIn(TOKEN,str(result))
        for http,status in ((404,'not_found'),(401,'auth_error'),(403,'forbidden'),(500,'server_error')):
            response=Mock(status_code=http,text=KEY+TOKEN)
            with patch('requests.sessions.Session.request',return_value=response):
                self.assertEqual(service.listar_grupos()['status'],status)
            response.json.assert_not_called()
        response=Mock(status_code=200);response.json.side_effect=ValueError(KEY+TOKEN)
        with patch('requests.sessions.Session.request',return_value=response):
            self.assertEqual(service.listar_grupos()['status'],'invalid_response')
        self.assertNotIn(KEY,stream.getvalue());self.assertNotIn(TOKEN,stream.getvalue())
        self.assertNotIn(KEY,repr(service.config));self.assertNotIn(KEY,repr(service))

    def test_real_mutations_blocked(self):
        service=GophishService(Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_INSTANCE_ID':'test-http',
            'GOPHISH_URL':'https://host','GOPHISH_API_KEY':KEY}))
        for result in (service.criar_grupo('Teste',[]),service.atualizar_grupo(1,'Teste',[]),
                       service.criar_template('Teste','Assunto',HTML),service.criar_pagina('Teste',HTML)):
            self.assertEqual(result['status'],'forbidden')
        self.http_guard.assert_not_called()

    def test_uncertain_mutation_never_retries(self):
        self.fake.configurar_resposta('POST','/api/groups/',status='timeout')
        result=self.service.criar_grupo('Teste',[])
        self.assertEqual(result['status'],'result_indeterminate')
        self.assertEqual(self.fake.chamadas,[('POST','/api/groups/')])

    def test_fake_cannot_inject_http_backend(self):
        with self.assertRaises(ValueError):GophishService(cfg(),Mock())


class ConteudoTest(unittest.TestCase):
    def test_safe_educational_html(self):
        validar_conteudo(HTML)
        validar_conteudo('<a href="{{.URL}}">Orientação</a>')

    def test_dangerous_html_and_credentials_flags(self):
        contents=['<form><input type="password"></form>', '<input type="email">',
                  '<script src="https://evil.test/a.js"></script>', '<script>alert(1)</script>',
                  '<iframe src="https://evil.test"></iframe>', '<meta http-equiv="refresh" content="0;url=https://evil.test">',
                  '<div onclick="alert(1)">ONE</div>', '<p style="background:url(https://evil.test)">ONE</p>',
                  '<a href="javascript:alert(1)">ONE</a>', '<a href="//evil.test">ONE</a>',
                  '<p>Microsoft Sign in</p>', '<p>Google login</p>', '<p>Banco: entre na sua conta</p>',
                  '<p>lo&#103;in</p>', '<p>lo<span>gin</span></p>']
        for content in contents:
            with self.subTest(content=content),self.assertRaises(ConteudoInseguro):validar_conteudo(content)
        for flag in ('capture_credentials','capture_passwords'):
            for value in (True,'false',0,None):
                with self.assertRaises(ConteudoInseguro):validar_conteudo(HTML,**{flag:value})

    def test_fake_blocks_unsafe_before_transport(self):
        fake=FakeBackend();service=GophishService(cfg(),fake)
        self.assertEqual(service.criar_pagina('Teste','<form></form>')['status'],'invalid_content')
        self.assertEqual(fake.chamadas,[])


class FluxoTest(unittest.TestCase):
    def setUp(self):
        self.guard=patch('requests.sessions.Session.request',side_effect=AssertionError('HTTP real proibido')).start()
        self.network=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida')).start()
        self.addCleanup(patch.stopall)
        with patch('app.load_dotenv'),patch.dict(os.environ,{'SECRET_KEY':'isolated','DATABASE_URL':'sqlite:///:memory:'},clear=True):
            self.app=create_app()
        self.app.config.update(TESTING=True,WTF_CSRF_ENABLED=True)
        @self.app.get('/_csrf')
        def csrf_token():return jsonify(token=generate_csrf())
        self.ctx=self.app.app_context();self.ctx.push();self.addCleanup(self.ctx.pop)
        self.addCleanup(db.session.remove)
        db.create_all()
        creator=Usuario(nome='Criador',email='criador@example.test',senha_hash='synthetic',perfil='operador')
        admin=Usuario(nome='Admin',email='admin@example.test',senha_hash='synthetic',perfil='administrador')
        inactive=Usuario(nome='Inativo',email='inativo@example.test',senha_hash='synthetic',ativo=False)
        department=Departamento(nome='Teste');group=Grupo(nome='Teste');scenario=Cenario(nome='Teste')
        training=Treinamento(titulo='Teste');db.session.add_all([creator,admin,inactive,department,group,scenario,training]);db.session.flush()
        collaborator=Colaborador(nome='Pessoa',email='pessoa@example.test',departamento_id=department.id)
        template=TemplateEmail(nome='Teste',assunto='Orientação',conteudo_html=HTML,cenario_id=scenario.id)
        page=LandingPage(nome='Teste',titulo='Orientação',mensagem='ONE',conteudo_html=HTML)
        db.session.add_all([collaborator,template,page]);db.session.flush()
        db.session.add_all([GrupoParticipante(grupo_id=group.id,colaborador_id=collaborator.id),
            ModuloTreinamento(treinamento_id=training.id,titulo='Teste',conteudo='Texto',ordem=1),
            Questao(treinamento_id=training.id,enunciado='Teste',ordem=1)])
        self.campaign=Campanha(nome='Simulação',tipo='simulacao_phishing',criado_por_id=creator.id,
            grupo_id=group.id,cenario_id=scenario.id,template_email_id=template.id,landing_page_id=page.id,treinamento_id=training.id)
        self.normal=Campanha(nome='Normal',tipo='treinamento',criado_por_id=creator.id,grupo_id=group.id,treinamento_id=training.id)
        db.session.add_all([self.campaign,self.normal]);db.session.commit()
        self.creator=creator.id;self.admin=admin.id;self.inactive=inactive.id
        self.client=self.app.test_client();self.login(self.creator)

    def login(self,uid):
        g.pop('_login_user',None)
        g.pop('csrf_token',None)
        with self.client.session_transaction() as session:
            session.clear()
            if uid is not None:session.update(_user_id=str(uid),_fresh=True)
        self.headers={'X-CSRFToken':self.client.get('/_csrf').json['token']}

    def post(self,action,campaign=None):
        return self.client.post(f'/campanhas/{(campaign or self.campaign).id}/{action}',headers=self.headers)

    def enable(self,backend='fake'):
        config=Config.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':backend,'GOPHISH_INSTANCE_ID':'test-instance',
            'GOPHISH_URL':'https://gophish.example.test','GOPHISH_API_KEY':KEY})
        self.app.extensions['gophish_config']=config
        self.app.extensions['gophish_service']=GophishService(config)
        return self.app.extensions['gophish_service']

    def test_local_request_and_approval_without_external_ids(self):
        service=self.enable()
        self.assertEqual(self.post('solicitar-aprovacao').status_code,302)
        self.assertEqual(self.campaign.status,'aguardando_aprovacao')
        self.login(self.admin);self.assertEqual(self.post('aprovar').status_code,302)
        self.assertEqual(self.campaign.status,'aprovada')
        self.assertIsNone(self.campaign.gophish_campaign_id)
        self.assertEqual(service.backend.chamadas,[]);self.guard.assert_not_called()
        self.assertEqual(Auditoria.query.count(),3)

    def test_disabled_integration_does_not_block_approval(self):
        self.post('solicitar-aprovacao');self.login(self.admin);self.post('aprovar')
        self.assertEqual(self.campaign.status,'aprovada');self.guard.assert_not_called()

    def test_normal_flow_zero_gophish(self):
        service=self.enable('http')
        with patch.object(service,'_request',side_effect=AssertionError('Gophish proibido')):
            self.post('solicitar-aprovacao',self.normal);self.login(self.admin)
            self.post('aprovar',self.normal);self.post('preparar',self.normal)
            self.assertEqual(self.normal.status,'preparada')
        self.guard.assert_not_called()

    def test_draft_read_and_create_import_training(self):
        service=self.enable('http')
        with patch.object(service,'_request',side_effect=AssertionError('Gophish proibido')):
            self.assertEqual(self.client.get('/campanhas/nova').status_code,200)
            self.assertEqual(self.client.get(f'/campanhas/{self.campaign.id}/editar').status_code,200)
            self.assertEqual(self.client.get(f'/campanhas/{self.campaign.id}').status_code,200)
            response=self.client.post('/campanhas/nova',data={'nome':'Nova','tipo':'simulacao_phishing'},headers=self.headers)
            self.assertEqual(response.status_code,302)
            self.assertEqual(Campanha.query.filter_by(nome='Nova').one().status,'rascunho')
        self.guard.assert_not_called()

    def test_self_approval_blocked(self):
        self.campaign.criado_por_id=self.admin;db.session.commit();self.login(self.admin)
        self.post('solicitar-aprovacao');self.post('aprovar')
        self.assertEqual(self.campaign.status,'aguardando_aprovacao')
        self.assertIsNone(self.campaign.aprovado_por_id)
        self.assertIsNotNone(Auditoria.query.filter_by(acao='autoaprovacao_bloqueada').first())

    def test_invalid_local_resources_block_request_and_revalidated_approval(self):
        other=Cenario(nome='Outro');db.session.add(other);db.session.flush()
        self.campaign.template_email.cenario_id=other.id
        db.session.commit()
        self.assertIn('outro cenário',' '.join(__import__('app.campanhas.routes',fromlist=['validar_recursos_locais']).validar_recursos_locais(self.campaign)))
        self.campaign.template_email.cenario_id=self.campaign.cenario_id
        self.campaign.template_email.ativo=False;db.session.commit();self.post('solicitar-aprovacao')
        self.assertEqual(self.campaign.status,'rascunho')
        self.campaign.template_email.ativo=True;db.session.commit();self.post('solicitar-aprovacao')
        self.campaign.landing_page.ativo=False;db.session.commit();self.login(self.admin);self.post('aprovar')
        self.assertEqual(self.campaign.status,'aguardando_aprovacao')

    def test_unsafe_local_content_blocks_approval(self):
        self.campaign.landing_page.conteudo_html='<form><input type="password"></form>'
        db.session.commit();self.post('solicitar-aprovacao')
        self.assertEqual(self.campaign.status,'rascunho')
        self.guard.assert_not_called()

    def test_simulation_preparation_unavailable(self):
        self.enable('http');self.post('solicitar-aprovacao');self.login(self.admin);self.post('aprovar')
        self.post('preparar');self.assertEqual(self.campaign.status,'aprovada');self.assertIsNone(self.campaign.preparada_em)
        self.guard.assert_not_called()

    def test_avulsa_policy_disabled_and_enabled_no_transport(self):
        routes=[f'/grupos/{self.campaign.grupo_id}/sincronizar',f'/templates-email/{self.campaign.template_email_id}/sincronizar',
                f'/landing-pages/{self.campaign.landing_page_id}/sincronizar','/integracoes/gophish/grupo-teste']
        # Usa o prefixo real registrado, sem presumir a grafia.
        from flask import url_for
        with self.app.test_request_context():
            routes[1]=url_for('templates_email.sincronizar',template_id=self.campaign.template_email_id)
        for backend in (None,'fake','http'):
            if backend:self.enable(backend)
            for uid in (None,self.creator,self.inactive,self.admin):
                self.login(uid)
                for route in routes:
                    response=self.client.post(route,headers=self.headers)
                    self.assertEqual(response.status_code,302 if uid in (None, self.inactive) else 409 if uid==self.admin else 403,(route,uid))
            self.guard.assert_not_called()
            service=servico_gophish()
            if isinstance(service.backend,FakeBackend):self.assertEqual(service.backend.chamadas,[])
        self.assertIsNone(self.campaign.grupo.gophish_group_id)
        self.assertIsNone(self.campaign.template_email.gophish_template_id)
        self.assertIsNone(self.campaign.landing_page.gophish_page_id)

    def test_admin_diagnostic_local_and_no_secrets(self):
        self.enable('http')
        for uid in (None,self.creator,self.inactive,self.admin):
            self.login(uid);response=self.client.get('/integracoes/gophish/')
            self.assertEqual(response.status_code,302 if uid in (None, self.inactive) else 200 if uid==self.admin else 403)
            self.assertNotIn(KEY,response.get_data(as_text=True));self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.guard.assert_not_called()

    def test_health_independent_and_no_http(self):
        for backend in (None,'fake','http'):
            if backend:self.enable(backend)
            response=self.client.get('/health')
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'ok')
            self.assertEqual(response.json['gophish']['status'],'disabled' if backend is None else 'ready')
            self.assertNotIn(KEY,str(response.json))
        self.guard.assert_not_called()

    def test_health_database_failure_is_503(self):
        with patch.object(db.session,'execute',side_effect=RuntimeError(KEY)):
            response=self.client.get('/health')
        self.assertEqual(response.status_code,503);self.assertEqual(response.json['status'],'error')
        self.assertNotIn(KEY,str(response.json))

    def test_csrf_blocks_avulsa_even_admin(self):
        self.login(self.admin)
        response=self.client.post('/integracoes/gophish/grupo-teste')
        self.assertEqual(response.status_code,400);self.guard.assert_not_called()


if __name__ == '__main__':unittest.main()
