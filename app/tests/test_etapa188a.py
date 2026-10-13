"""Regressões dos achados 18.8A em bancos exclusivamente isolados."""
import os
import unittest
from unittest.mock import patch
from flask_login import current_user, login_required
from app import create_app
from app.extensions import db
from app.models.usuario import Usuario
from app.models.questao import Questao
from app.models.alternativa import Alternativa
import test_treinamento_estabilidade as fixture

class AutenticacaoTest(unittest.TestCase):
    def test_sessao_existente_desativada_e_remember_cookie(self):
        with patch('app.load_dotenv'), patch.dict(os.environ, {'SECRET_KEY':'isolated', 'DATABASE_URL':'sqlite://'}, clear=True):
            app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        @app.get('/_protegido')
        @login_required
        def protegido():
            return str(current_user.id)
        with app.app_context():
            db.create_all()
            user=Usuario(nome='Admin', email='admin@example.invalid', senha_hash='test')
            db.session.add(user);db.session.commit();uid=user.id
        client=app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id']=str(uid);sess['_fresh']=True
        self.assertEqual(client.get('/_protegido').status_code,200)
        with app.app_context():
            db.session.get(Usuario,uid).ativo=False;db.session.commit()
        self.assertEqual(client.get('/_protegido').status_code,302)
        from flask_login.utils import encode_cookie
        with app.app_context(): cookie=encode_cookie(str(uid))
        remembered=app.test_client();remembered.set_cookie('remember_token',cookie)
        self.assertEqual(remembered.get('/_protegido').status_code,302)
        with client.session_transaction() as sess: sess['_user_id']='inválido'
        self.assertEqual(client.get('/_protegido').status_code,302)
        with app.app_context(): db.session.remove();db.engine.dispose()

class FormulariosTest(fixture.EstabilidadeTest):
    def test_ordens_invalidas_quatro_formularios_sem_mutacao(self):
        with self.app.app_context():
            q=Questao(treinamento_id=self.tid,enunciado='Original',ordem=1)
            db.session.add(q);db.session.flush()
            db.session.add_all([Alternativa(questao_id=q.id,texto=str(i),correta=i==1) for i in range(1,5)])
            db.session.commit();qid=q.id
        data=dict(titulo='Alterado',conteudo='Alterado',enunciado='Alterado',correta='1',**{f'alternativa_{i}':str(i) for i in range(1,5)})
        urls=[self.add_url,self.edit_url,f'/treinamentos/{self.tid}/questoes/nova',f'/treinamentos/questoes/{qid}/editar']
        before=self.module_rows()
        for url in urls:
            for ordem in ('','abc','1.5','0','-1','2147483648','9'*5000):
                with self.subTest(url=url,ordem=ordem[:20]):
                    response=self.client.post(url,data={**data,'ordem':ordem},headers=self.headers)
                    self.assertEqual(response.status_code,302)
                    with self.client.session_transaction() as sess:
                        self.assertEqual(sess['_flashes'][-1],('danger','Informe uma ordem válida.'))
                    self.assertEqual(self.module_rows(),before)
                    with self.app.app_context():
                        self.assertEqual(Questao.query.count(),1)
                        self.assertEqual(db.session.get(Questao,qid).enunciado,'Original')
    def test_ordens_validas_quatro_formularios(self):
        data=dict(titulo='Novo',conteudo='Texto',enunciado='Questão',correta='1',ordem='2',**{f'alternativa_{i}':str(i) for i in range(1,5)})
        for url in (self.add_url,self.edit_url,f'/treinamentos/{self.tid}/questoes/nova'):
            self.assertEqual(self.client.post(url,data=data,headers=self.headers).status_code,302)
        with self.app.app_context(): qid=Questao.query.one().id
        data['ordem']='3'
        self.assertEqual(self.client.post(f'/treinamentos/questoes/{qid}/editar',data=data,headers=self.headers).status_code,302)
        with self.app.app_context(): self.assertEqual(db.session.get(Questao,qid).ordem,3)
    def test_erros_internos_nao_sao_ocultados(self):
        with patch.object(db.session,'commit',side_effect=RuntimeError('erro interno')):
            with self.assertRaisesRegex(RuntimeError,'erro interno'): self.admin_add()

class FormulariosPostgresTest(FormulariosTest):
    postgres=True
