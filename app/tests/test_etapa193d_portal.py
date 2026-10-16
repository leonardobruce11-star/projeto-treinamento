"""Certificados no portal: somente SQLite em memória, CSRF real e serviços reais."""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import CertificadoTreinamento, ParticipacaoTreinamento
import test_portal_treinamento_quiz as quiz


class CertificadoPortalTest(unittest.TestCase):
    postgres = False
    setUpClass = quiz.QuizTest.__dict__['setUpClass']
    tearDownClass = quiz.QuizTest.__dict__['tearDownClass']
    request = quiz.QuizTest.request
    finish_modules = quiz.QuizTest.finish_modules
    post_quiz = quiz.QuizTest.post_quiz

    def setUp(self):
        quiz.QuizTest.setUp(self)
        self.url = '/aprender/t/principal/certificado'
        if self._testMethodName == 'test_sem_quiz':
            quiz.QuizTest.without_quiz(self)
        self.finish_modules()

    def aprovar(self):
        self.assertEqual(self.post_quiz(self.answers).status_code, 303)

    def emitir(self, **kwargs):
        return self.client.post(self.url, headers=self.headers, **kwargs)

    def historico(self):
        with self.app.app_context(), db.engine.connect() as conn:
            return {table: [dict(row) for row in conn.execute(
                text(f'SELECT * FROM {table} ORDER BY id')).mappings()]
                for table in ('participacoes_treinamento', 'resultados_quiz', 'submissoes_quiz')}

    def certificados(self):
        with self.app.app_context():
            return [(c.id, c.participacao_id, c.codigo_verificacao, c.emitido_em,
                     c.nome_participante, c.titulo_treinamento)
                    for c in db.session.execute(select(CertificadoTreinamento)).scalars()]

    def test_aprovado_get_sem_emissao(self):
        self.aprovar()
        self.assertIn(b'Consultar certificado', self.client.get(self.quiz_url).data)
        antes = self.historico()
        pagina = self.client.get(self.conclusion_url)
        self.assertIn(b'Emitir e baixar certificado PDF', pagina.data)
        self.assertIn(b'name="csrf_token"', pagina.data)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.certificados(), [])
        self.assertEqual(self.historico(), antes)

    def test_emissao_download_repeticao_e_historico(self):
        self.aprovar()
        antes = self.historico()
        resposta = self.emitir(data={'participacao_id': '999', 'certificado_id': '999'})
        self.assertEqual(resposta.status_code, 303)
        self.assertTrue(resposta.location.endswith('/principal/certificado'))
        emitidos = self.certificados()
        self.assertEqual(len(emitidos), 1)
        self.assertEqual(emitidos[0][1], self.pid)
        for _ in range(2):
            pdf = self.client.get(resposta.location)
            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf.mimetype, 'application/pdf')
            self.assertTrue(pdf.data.startswith(b'%PDF-'))
            self.assertIn(b'%%EOF', pdf.data[-1024:])
            self.assertEqual(int(pdf.headers['Content-Length']), len(pdf.data))
            self.assertEqual(pdf.headers['Content-Disposition'], 'attachment; filename=certificado-one.pdf')
            self.assertEqual(pdf.headers['Cache-Control'], 'no-store')
            self.assertEqual(pdf.headers['Referrer-Policy'], 'no-referrer')
            self.assertEqual(pdf.headers['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn('ETag', pdf.headers)
            self.assertEqual(self.emitir().status_code, 303)
        self.assertEqual(self.certificados(), emitidos)
        self.assertEqual(self.historico(), antes)
        self.assertIn(b'Baixar certificado PDF', self.client.get(self.conclusion_url).data)

    def test_reprovado(self):
        respostas = dict(self.answers)
        respostas[f'questao_{self.qids[1]}'] = self.wrong
        self.assertEqual(self.post_quiz(respostas).status_code, 303)
        antes = self.historico()
        for resposta in (self.client.get(self.url), self.emitir()):
            self.assertEqual(resposta.status_code, 403)
        self.assertNotIn(b'Certificado dispon', self.client.get(self.conclusion_url).data)
        self.assertEqual(self.certificados(), [])
        self.assertEqual(self.historico(), antes)

    def test_nao_concluido(self):
        antes = self.historico()
        self.assertEqual(self.emitir().status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotIn(b'Certificado dispon', self.client.get(self.conclusion_url).data)
        self.assertEqual(self.historico(), antes)
        self.assertEqual(self.certificados(), [])

    def test_token_invalido_e_outra_participacao(self):
        self.aprovar()
        self.emitir()
        antes = self.certificados()
        for token, status in (('invalido', 404), ('unico', 403), ('one-session-invalido', 404)):
            url = f'/aprender/t/{token}/certificado'
            self.assertEqual(self.client.get(url, query_string={'participacao_id': self.pid}).status_code, status)
            self.assertEqual(self.client.post(url, data={'participacao_id': self.pid}, headers=self.headers).status_code, status)
        self.assertEqual(self.client.get(self.url+'/1').status_code, 404)
        self.assertEqual(self.certificados(), antes)

    def test_csrf(self):
        self.aprovar()
        antes = self.historico()
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.assertEqual(self.client.post(self.url, data={'csrf_token': 'invalido'}).status_code, 400)
        outro = self.app.test_client()
        self.assertEqual(outro.post(self.url, headers=self.headers).status_code, 400)
        self.assertEqual(self.certificados(), [])
        self.assertEqual(self.historico(), antes)

    def test_historico_inconsistente(self):
        self.aprovar()
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            p.aprovado_em += timedelta(seconds=1)
            db.session.commit()
        antes = self.historico()
        self.assertNotIn(b'Certificado dispon', self.client.get(self.conclusion_url).data)
        self.assertEqual(self.emitir().status_code, 409)
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.historico(), antes)
        self.assertEqual(self.certificados(), [])

    def test_participacao_inativa(self):
        self.aprovar()
        self.emitir()
        with self.app.app_context():
            db.session.get(ParticipacaoTreinamento, self.pid).colaborador.ativo = False
            db.session.commit()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.emitir().status_code, 404)

    def test_sem_quiz(self):
        self.assertEqual(self.client.post(self.quiz_url, headers=self.headers).status_code, 302)
        self.assertEqual(self.emitir().status_code, 303)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_download_existente_nao_chama_emissor(self):
        self.aprovar()
        self.emitir()
        with patch('app.portal_treinamento.routes.emitir_certificado', side_effect=AssertionError('GET emitiu')):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual(self.client.get(self.conclusion_url).status_code, 200)

    def test_participacao_removida(self):
        with self.app.app_context():
            db.session.delete(db.session.get(ParticipacaoTreinamento, self.pid))
            db.session.commit()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.emitir().status_code, 404)

    def test_certificado_historico_preserva_snapshot(self):
        self.aprovar()
        self.emitir()
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            p.treinamento.titulo = 'Título atualizado'
            p.colaborador.nome = 'Nome atualizado'
            db.session.commit()
        antes = self.historico()
        certificados = self.certificados()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.emitir().status_code, 303)
        self.assertEqual(self.certificados(), certificados)
        self.assertEqual(self.historico(), antes)

    def test_certificado_com_dados_incompletos(self):
        self.aprovar()
        self.emitir()
        with self.app.app_context():
            db.session.execute(select(CertificadoTreinamento)).scalar_one().nome_participante = ''
            db.session.commit()
        antes = self.certificados()
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.certificados(), antes)

    def test_falha_banco(self):
        with patch('app.portal_treinamento.routes.obter_participacao', side_effect=SQLAlchemyError()):
            self.assertEqual(self.emitir().status_code, 503)
            self.assertEqual(self.client.get(self.url).status_code, 503)


if __name__ == '__main__':
    unittest.main()
