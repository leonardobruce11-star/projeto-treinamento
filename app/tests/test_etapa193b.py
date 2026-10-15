"""19.3-B: emissão de certificados em PostgreSQL isolado."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.extensions import db
from app.models import (
    CertificadoTreinamento as Certificado,
    ParticipacaoTreinamento as Participacao,
    ResultadoQuiz,
)
from app.services.certificados import (
    ErroCertificado,
    emitir_certificado,
)

import test_portal_treinamento_quiz as quiz
from app.utils import submissoes_quiz


class CertificadosPostgresTest(unittest.TestCase):
    postgres = True

    @classmethod
    def setUpClass(cls):
        raw = os.environ.get(
            "ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL"
        )
        if not raw:
            raise unittest.SkipTest(
                "Configure um banco PostgreSQL descartável para os testes"
            )

        url = make_url(raw)
        if (
            not url.drivername.startswith("postgresql")
            or not url.database
            or url.database == "phishing_awareness"
            or url.host not in ("127.0.0.1", "localhost")
        ):
            raise RuntimeError(
                "Banco de testes inseguro: use PostgreSQL local descartável"
            )

        # Reutiliza a infraestrutura existente de schemas isolados.
        quiz.QuizTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        quiz.QuizTest.tearDownClass.__func__(cls)

    def setUp(self):
        quiz.QuizTest.setUp(self)
        if self._testMethodName == "test_05_conclusao_sem_quiz":
            quiz.QuizTest.without_quiz(self)
        self.finish_modules()

    request = quiz.QuizTest.request
    finish_modules = quiz.QuizTest.finish_modules

    def emitir(self):
        with self.app.app_context():
            return emitir_certificado(self.pid)

    def preparar_e_corrigir(self, respostas):
        with self.app.app_context():
            submissao = submissoes_quiz.preparar_submissao(
                "principal",
                self.ultima_tentativa_id()
            )
            return submissoes_quiz.corrigir_submissao(
                "principal",
                submissao.submissao_id,
                respostas,
            )

    def ultima_tentativa_id(self):
        with self.app.app_context():
            return db.session.execute(
                select(ResultadoQuiz.id)
                .where(ResultadoQuiz.participacao_id == self.pid)
                .order_by(
                    ResultadoQuiz.numero_tentativa.desc(),
                    ResultadoQuiz.id.desc(),
                )
                .limit(1)
            ).scalar_one_or_none()

    def rejeitado(self, codigo):
        with self.assertRaises(ErroCertificado) as contexto:
            self.emitir()
        self.assertEqual(contexto.exception.codigo, codigo)

    def snapshot_historico(self):
        with self.app.app_context(), db.engine.connect() as conexao:
            return {
                tabela: [
                    dict(linha)
                    for linha in conexao.execute(
                        text(
                            f"SELECT * FROM {tabela} ORDER BY id"
                        )
                    ).mappings()
                ]
                for tabela in (
                    "participacoes_treinamento",
                    "resultados_quiz",
                    "submissoes_quiz",
                )
            }

    def quantidade_certificados(self):
        with self.app.app_context():
            return db.session.execute(
                select(db.func.count(Certificado.id))
            ).scalar_one()

    def test_01_nao_concluida(self):
        self.rejeitado("nao_elegivel")
        self.assertEqual(self.quantidade_certificados(), 0)

    def test_02_reprovada(self):
        respostas = dict(self.answers)
        respostas[f"questao_{self.qids[1]}"] = self.wrong
        resultado = self.preparar_e_corrigir(respostas)
        self.assertFalse(resultado.aprovado)

        self.rejeitado("nao_elegivel")
        self.assertEqual(self.quantidade_certificados(), 0)

    def test_03_aprovada_primeira_tentativa(self):
        resultado = self.preparar_e_corrigir(self.answers)
        self.assertTrue(resultado.aprovado)

        antes = self.snapshot_historico()
        certificado = self.emitir()
        depois = self.snapshot_historico()

        self.assertTrue(certificado.criado)
        self.assertEqual(certificado.participacao_id, self.pid)
        self.assertGreaterEqual(len(certificado.codigo_verificacao), 32)
        self.assertEqual(self.quantidade_certificados(), 1)
        self.assertEqual(antes, depois)

    def test_04_aprovada_apos_refacao(self):
        respostas = dict(self.answers)
        respostas[f"questao_{self.qids[1]}"] = self.wrong

        primeira = self.preparar_e_corrigir(respostas)
        segunda = self.preparar_e_corrigir(self.answers)

        self.assertFalse(primeira.aprovado)
        self.assertTrue(segunda.aprovado)
        self.assertEqual(segunda.numero_tentativa, 2)

        antes = self.snapshot_historico()
        certificado = self.emitir()

        self.assertTrue(certificado.criado)
        self.assertEqual(self.snapshot_historico(), antes)
        self.assertEqual(self.quantidade_certificados(), 1)

    def test_05_conclusao_sem_quiz(self):
        resposta = self.client.post(
            self.quiz_url,
            headers=self.headers,
        )
        self.assertEqual(resposta.status_code, 302)

        antes = self.snapshot_historico()
        certificado = self.emitir()

        self.assertTrue(certificado.criado)
        self.assertEqual(self.snapshot_historico(), antes)
        self.assertEqual(self.quantidade_certificados(), 1)

    def test_06_idempotencia(self):
        self.preparar_e_corrigir(self.answers)

        primeiro = self.emitir()
        segundo = self.emitir()

        self.assertTrue(primeiro.criado)
        self.assertFalse(segundo.criado)
        self.assertEqual(
            primeiro.certificado_id,
            segundo.certificado_id,
        )
        self.assertEqual(
            primeiro.codigo_verificacao,
            segundo.codigo_verificacao,
        )
        self.assertEqual(self.quantidade_certificados(), 1)

    def test_07_historico_inconsistente(self):
        self.preparar_e_corrigir(self.answers)

        with self.app.app_context():
            participacao = db.session.get(
                Participacao,
                self.pid,
            )
            participacao.aprovado_em += timedelta(seconds=1)
            db.session.commit()

        self.rejeitado("historico_inconsistente")
        self.assertEqual(self.quantidade_certificados(), 0)

    def test_08_concorrencia(self):
        self.preparar_e_corrigir(self.answers)
        antes = self.snapshot_historico()

        def tarefa(_):
            with self.app.app_context():
                return emitir_certificado(self.pid)

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(tarefa, range(2)))

        self.assertEqual(
            resultados[0].certificado_id,
            resultados[1].certificado_id,
        )
        self.assertEqual(
            resultados[0].codigo_verificacao,
            resultados[1].codigo_verificacao,
        )
        self.assertEqual(
            sum(recibo.criado for recibo in resultados),
            1,
        )
        self.assertEqual(self.quantidade_certificados(), 1)
        self.assertEqual(self.snapshot_historico(), antes)


if __name__ == "__main__":
    unittest.main()
