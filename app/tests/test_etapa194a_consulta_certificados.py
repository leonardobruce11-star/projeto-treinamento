"""Factory real, Flask-Login ativo e SQLite em memória, sem .env ou credenciais ONE."""
import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import patch
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from app import create_app
from app.extensions import db
from app.models import (CertificadoTreinamento as C, Colaborador, Departamento,
                        ParticipacaoTreinamento as P, Treinamento, Usuario)
from app.services.certificados import ErroCertificado, recibo_de_certificado
from app.services.consulta_certificados import obter_certificado_emitido


class ConsultaCertificadosAdminTest(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'teste-isolado', 'DATABASE_URL': 'sqlite://',
                                    'APP_ENV': 'testing', 'MAIL_ENABLED': 'false'}, clear=True), \
                patch('app.load_dotenv') as dotenv:
            self.app = create_app()
            dotenv.assert_called_once_with()
        self.app.config.update(TESTING=True, LOGIN_DISABLED=False)
        with self.app.app_context():
            db.create_all()
            d = Departamento(nome='Isolado')
            db.session.add(d); db.session.flush()
            c = Colaborador(nome='Nome histórico', email='teste@example.invalid', departamento_id=d.id)
            t = Treinamento(titulo='Título histórico')
            users = [Usuario(nome=role, email=f'{i}@example.invalid', perfil=role,
                             ativo=active, senha_hash='hash-ficticio')
                     for i, (role, active) in enumerate([
                         ('administrador', True), ('operador', True), ('administrador', False)])]
            db.session.add_all([c, t] + users); db.session.flush()
            instante = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
            ps = [P(colaborador_id=c.id, treinamento_id=t.id, token=f'TOKEN_PRIVADO_{i}',
                    concluido_em=instante, aprovado_em=instante) for i in range(2)]
            db.session.add_all(ps); db.session.flush()
            db.session.add(C(id=41, participacao_id=ps[0].id, nome_participante=c.nome,
                             titulo_treinamento=t.titulo, codigo_verificacao='CODIGO_TESTE', emitido_em=instante))
            db.session.commit()
            self.uid, self.operador, self.inativo = [u.id for u in users]
            self.pid, self.sem_certificado = [p.id for p in ps]
            self.cid, self.tid = c.id, t.id
            self.engine = db.engine
        self.client = self.app.test_client()
        self.url = '/certificados/41'
        self.statements = []
        self.listener = lambda conn, cursor, sql, params, ctx, many: self.statements.append(sql)
        event.listen(self.engine, 'before_cursor_execute', self.listener)
        self.autenticar(self.uid)

    def tearDown(self):
        event.remove(self.engine, 'before_cursor_execute', self.listener)
        with self.app.app_context():
            db.session.remove(); db.engine.dispose()

    def autenticar(self, uid):
        with self.client.session_transaction() as sess:
            sess.clear()
            if uid is not None:
                sess['_user_id'] = str(uid)
                sess['_fresh'] = True

    def snapshot(self):
        with self.app.app_context(), db.engine.connect() as conn:
            return {t.name: sorted((tuple(row) for row in conn.execute(select(t))), key=repr)
                    for t in db.metadata.sorted_tables}

    def bloqueado(self, uid, status):
        self.autenticar(uid)
        with patch('app.certificados_admin.routes.obter_certificado_emitido') as consultar:
            for method in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=method)
                self.assertEqual(r.status_code, status)
                self.assertNotIn(b'CODIGO_TESTE', r.data)
                self.assertNotIn(b'Nome hist', r.data)
            consultar.assert_not_called()

    def test_administrador_ativo_e_blueprint_registrado(self):
        self.assertFalse(self.app.config['LOGIN_DISABLED'])
        self.assertTrue(self.app.config['WTF_CSRF_CHECK_DEFAULT'])
        self.assertIn('certificados_admin', self.app.blueprints)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json, dict(certificado_id=41, participacao_id=self.pid,
            nome_participante='Nome histórico', titulo_treinamento='Título histórico',
            codigo_verificacao='CODIGO_TESTE', emitido_em='2026-10-15T12:00:00'))
        self.assertNotIn(b'TOKEN_PRIVADO', r.data)
        self.assertNotIn('/aprender/', r.get_data(as_text=True))
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        self.assertEqual(r.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')

    def test_anonimo(self):
        self.bloqueado(None, 302)
        self.assertIn('/login', self.client.get(self.url).location)

    def test_perfil_nao_administrativo(self):
        self.bloqueado(self.operador, 403)

    def test_administrador_inativo(self):
        # O user_loader real não restaura usuários inativos.
        self.bloqueado(self.inativo, 302)

    def test_desativado_apos_acesso(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        with self.app.app_context():
            db.session.get(Usuario, self.uid).ativo = False
            db.session.commit()
        self.bloqueado(self.uid, 302)

    def test_inexistente(self):
        for method in ('GET', 'HEAD'):
            self.assertEqual(self.client.open('/certificados/999', method=method).status_code, 404)
        with self.app.app_context():
            self.assertIsNone(obter_certificado_emitido(999))

    def test_aprovada_sem_certificado(self):
        antes = self.snapshot()
        self.assertEqual(self.client.get(f'/certificados/{self.sem_certificado}').status_code, 404)
        with self.app.app_context():
            self.assertIsNone(obter_certificado_emitido(self.sem_certificado))
        self.assertEqual(self.snapshot(), antes)

    def test_cadastros_inativos(self):
        for model, ident in ((Colaborador, self.cid), (Treinamento, self.tid)):
            with self.subTest(model=model.__name__):
                with self.app.app_context():
                    db.session.get(model, ident).ativo = False
                    db.session.commit()
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_snapshot_historico(self):
        with self.app.app_context():
            db.session.get(Colaborador, self.cid).nome = 'Nome atual'
            db.session.get(Treinamento, self.tid).titulo = 'Título atual'
            db.session.commit()
        antes = self.snapshot()
        r = self.client.get(self.url)
        self.assertEqual(r.json['nome_participante'], 'Nome histórico')
        self.assertEqual(r.json['titulo_treinamento'], 'Título histórico')
        self.assertEqual(self.snapshot(), antes)

    def test_nao_revalida_historico_divergente(self):
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            p.aprovado_em = p.concluido_em = None
            db.session.commit()
        antes = self.snapshot()
        with patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.snapshot(), antes)

    def test_get_head_somente_select_sem_emissao(self):
        antes = self.snapshot()
        self.statements.clear()
        with patch('app.services.certificados.emitir_certificado', side_effect=AssertionError('Emitiu')) as emitir, \
                patch('app.portal_treinamento.routes.emitir_certificado', side_effect=AssertionError('Emitiu')) as portal:
            for method in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=method)
                self.assertEqual(r.status_code, 200)
                if method == 'HEAD':
                    self.assertEqual(r.data, b'')
                self.assertEqual(self.client.open('/certificados/999', method=method).status_code, 404)
            emitir.assert_not_called(); portal.assert_not_called()
        self.assertTrue(self.statements)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements), self.statements)
        self.assertFalse(any('participacoes_treinamento' in sql for sql in self.statements))
        self.assertEqual(self.snapshot(), antes)

    def test_sem_autoflush_do_chamador(self):
        with self.app.app_context():
            c = db.session.get(Colaborador, self.cid)
            c.nome = 'Não persistido'
            self.statements.clear()
            self.assertEqual(obter_certificado_emitido(41).nome_participante, 'Nome histórico')
            self.assertEqual(len(self.statements), 1)
            self.assertTrue(self.statements[0].lstrip().upper().startswith('SELECT'))
            self.assertIn(c, db.session.dirty)
            db.session.rollback()

    def test_conversao_publica(self):
        with self.app.app_context():
            c = db.session.get(C, 41)
            recibo = recibo_de_certificado(c)
            db.session.expunge(c)
            self.assertEqual(recibo_de_certificado(c), recibo)
            self.assertFalse(recibo.criado)
            self.assertFalse(hasattr(recibo, 'token'))
            with self.assertRaises(FrozenInstanceError):
                recibo.nome_participante = 'Outro'
            db.session.delete(c)
            with self.assertRaises(ErroCertificado):
                recibo_de_certificado(c)
            db.session.flush()
            with self.assertRaises(ErroCertificado):
                recibo_de_certificado(c)
            db.session.rollback()
        for obj in (None, object(), C(id=41)):
            with self.subTest(tipo=type(obj).__name__), self.assertRaises(ErroCertificado):
                recibo_de_certificado(obj)

    def test_ids_invalidos(self):
        with self.app.app_context():
            self.statements.clear()
            for ident in (None, True, False, 0, -1, '41', 1.5, 2147483648):
                with self.subTest(ident=ident), self.assertRaises(ErroCertificado):
                    obter_certificado_emitido(ident)
            self.assertEqual(self.statements, [])
        self.assertEqual(self.client.get('/certificados/2147483648').status_code, 404)

    def test_falha_banco(self):
        with patch('app.certificados_admin.routes.obter_certificado_emitido',
                   side_effect=SQLAlchemyError('PARAMETRO_PRIVADO')):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json, {'erro': 'consulta_indisponivel'})
        self.assertNotIn(b'PARAMETRO_PRIVADO', r.data)

    def test_sem_rotas_antecipadas(self):
        regras = [r for r in self.app.url_map.iter_rules() if r.endpoint.startswith('certificados_admin.')]
        self.assertEqual({r.rule for r in regras}, {
            '/certificados/', '/certificados/<int:certificado_id>',
            '/certificados/<int:certificado_id>/detalhes',
        })
        for regra in regras:
            self.assertEqual(regra.methods, {'GET', 'HEAD', 'OPTIONS'})
        for url in (self.url+'/pdf', self.url+'/editar'):
            self.assertEqual(self.client.get(url).status_code, 404)


if __name__ == '__main__':
    unittest.main()
