"""Rota e template reais com bancos isolados; sem .env."""
import unittest
from pathlib import Path
from flask import template_rendered
from flask_login import LoginManager
from jinja2 import FileSystemLoader, StrictUndefined
from sqlalchemy import event
import test_painel_colaboradores as dados
from app.extensions import db
from app.colaboradores.routes import colaboradores_bp
from app.models.colaborador import Colaborador
from app.utils.tempo import formatar_tempo_ativo


class PainelPaginaTest(unittest.TestCase):
    postgres = False
    tearDown = dados.PainelTest.tearDown
    seed = dados.PainelTest.seed

    def setUp(self):
        dados.PainelTest.setUp(self)
        self.app.config.update(TESTING=True, SECRET_KEY='isolated', LOGIN_DISABLED=True)
        LoginManager(self.app).user_loader(lambda uid: None)
        self.app.register_blueprint(colaboradores_bp)
        self.app.jinja_loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'app/templates'))
        self.app.jinja_env.undefined=StrictUndefined
        self.app.jinja_env.filters['tempo_ativo']=formatar_tempo_ativo
        self.app.jinja_env.globals['csrf_token']=lambda:'test'
        # Endpoints usados pelo layout comum; a página sob teste é real.
        for name in ['dashboard.index','campanhas.listar','treinamentos.listar',
                     'departamentos.listar','grupos.listar','auth.logout',
                     'categorias_treinamento.listar','cenarios.listar',
                     'templates_email.listar','landing_pages.listar','integracoes_gophish.index']:
            self.app.add_url_rule('/_layout/'+name, endpoint=name, view_func=lambda:'layout')
        self.client=self.app.test_client()
        self.contexts=[]
        self.capture=lambda sender,template,context,**kwargs:self.contexts.append(context)
        template_rendered.connect(self.capture,self.app,weak=False)

    def get(self,path='/colaboradores/painel'):
        response=self.client.get(path)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        return response.get_data(as_text=True),self.contexts[-1]

    def test_login_required_before_queries(self):
        self.app.config['LOGIN_DISABLED']=False
        self.statements.clear()
        self.assertEqual(self.client.get('/colaboradores/painel').status_code,401)
        self.assertEqual(self.statements,[])

    def test_empty(self):
        html,ctx=self.get()
        self.assertIn('Nenhum colaborador cadastrado',html)
        self.assertEqual(ctx['indicadores']['total_colaboradores'],0)
        self.assertEqual(ctx['pagina'],1)
        self.assertEqual(ctx['total_paginas'],1)
        self.assertIn('0 min',html)

    def test_people_and_metrics_from_data_layer(self):
        self.seed();html,ctx=self.get()
        self.assertEqual(len(ctx['colaboradores']),6)
        row=next(r for r in ctx['colaboradores'] if r['colaborador_id']==self.ids[0])
        self.assertEqual(row['atribuidas'],0)
        self.assertIsNone(row['percentual_conclusao'])
        self.assertIn('Sem registro',html)
        self.assertIn('Sem quiz',html)
        self.assertIn('—',html)
        self.assertIn('Ativo',html);self.assertIn('Inativo',html)
        self.assertIn('33.3%',html)
        self.assertEqual(ctx['indicadores']['total_segundos'],600)

    def test_links_to_history(self):
        self.seed();html,_=self.get()
        for cid in self.ids:self.assertIn(f'href="/colaboradores/{cid}"',html)

    def test_time_format_and_recent_quiz(self):
        self.seed();html,ctx=self.get()
        self.assertIn('10 min',html) # total
        self.assertIn('5 min',html) # média por pessoa
        self.assertIn('3 min',html);self.assertIn('7 min',html)
        self.assertIn('20.0%',html)
        self.assertNotIn('90.0%',html)
        self.assertEqual(ctx['quizzes'][self.ids[4]]['resultado_quiz_id'],self.qids[2])
        self.assertIn('06/01/2026 00:00',html)

    def test_pagination_order_summary_and_navigation(self):
        self.seed()
        db.session.add_all([Colaborador(nome='Mesmo nome', email=f'extra{i}@example.invalid',
            departamento_id=self.people[0].departamento_id) for i in range(47)])
        db.session.commit()
        pages=[]
        for page,size in [(1,25),(2,25),(3,3)]:
            html,ctx=self.get(f'/colaboradores/painel?page={page}')
            self.assertEqual(len(ctx['colaboradores']),size)
            self.assertEqual(ctx['indicadores']['total_colaboradores'],53)
            self.assertEqual(ctx['indicadores']['total_segundos'],600)
            self.assertEqual(ctx['total_paginas'],3)
            rows=ctx['colaboradores'];pages.extend([(r['nome'],r['colaborador_id']) for r in rows])
            if page>1:self.assertIn(f'href="/colaboradores/painel?page={page-1}"',html)
            if page<3:self.assertIn(f'href="/colaboradores/painel?page={page+1}"',html)
        self.assertEqual(len(set(pages)),53)
        self.assertEqual(pages,sorted(pages))
        self.assertEqual(self.contexts[-1]['quizzes'],{})

    def test_invalid_and_out_of_range_pages(self):
        for value in ['0','-1','abc','1.5','']:
            self.assertEqual(self.client.get('/colaboradores/painel?page='+value).status_code,200)
        for value in ['2','99999999999999999999999999']:
            self.assertEqual(self.client.get('/colaboradores/painel?page='+value).status_code,200)

    def test_fixed_query_count_and_sql_limit(self):
        self.seed();self.statements.clear();self.get()
        self.assertEqual(len(self.statements),5)
        self.assertIn('LIMIT',self.statements[3])
        self.assertIn('OFFSET',self.statements[3])
        self.assertIn('row_number()',self.statements[4].lower())

    def test_links_from_directory_and_dashboard(self):
        self.seed()
        response=self.client.get('/colaboradores/')
        self.assertEqual(response.status_code,200)
        self.assertIn('href="/colaboradores/painel"',response.get_data(as_text=True))
        # Renderiza o partial real de capacitação usado pelo dashboard.
        with self.app.test_request_context():
            html=self.app.jinja_env.get_template('dashboard/tempo_capacitacao.html').render(
                metricas_capacitacao={'total_segundos':0,'media_segundos':0,
                'participacoes_com_tempo':0,'colaboradores_com_atividade':0,
                'por_treinamento':[],'por_categoria':[]})
        self.assertIn('href="/colaboradores/painel"',html)


class PainelPaginaPostgresTest(PainelPaginaTest):
    postgres=True
