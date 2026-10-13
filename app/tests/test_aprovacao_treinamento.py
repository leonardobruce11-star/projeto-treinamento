"""Fundação de aprovação; fluxo vigente permanece sem regra de nota mínima."""
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext
import unittest
from fractions import Fraction
from math import ceil

from app.extensions import db
from app.models.treinamento import Treinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.utils.aprovacao_treinamento import (
    avaliar_aprovacao, validar_nota_minima_percentual,
    ConfiguracaoAprovacaoInvalida,
)
# Inicializa a ponte na mesma ordem das suítes de simulação existentes.
# preparacao/leases têm dependência circular preexistente.
import app.simulacoes.preparacao
import test_portal_treinamento_quiz as quiz_fixture


class RegraAprovacaoTest(unittest.TestCase):
    def test_exemplos_obrigatorios(self):
        for total, minimo in ((5, 4), (6, 5), (8, 7), (9, 8), (10, 7), (12, 9), (15, 11)):
            with self.subTest(total=total):
                r = avaliar_aprovacao(total, minimo)
                self.assertEqual((r.total_questoes, r.minimo_acertos, r.total_acertos),
                                 (total, minimo, minimo))
                self.assertTrue(r.aprovado)
                self.assertFalse(avaliar_aprovacao(total, minimo - 1).aprovado)

    def test_limite_nove_dez(self):
        self.assertEqual(avaliar_aprovacao(9, 7).percentual_minimo, Decimal(80))
        self.assertFalse(avaliar_aprovacao(9, 7).aprovado)
        self.assertEqual(avaliar_aprovacao(10, 7).percentual_minimo, Decimal(70))
        self.assertTrue(avaliar_aprovacao(10, 7).aprovado)

    def test_fracoes(self):
        for total, acertos, percentual, aprovado in (
                (10, 7, '70.00', True), (6, 4, '66.67', False),
                (6, 5, '83.33', True), (6, 3, '50.00', False)):
            with self.subTest(total=total, acertos=acertos):
                r = avaliar_aprovacao(total, acertos)
                self.assertEqual(r.percentual, Decimal(percentual))
                self.assertIs(r.aprovado, aprovado)

    def test_manual_substitui_ambas_faixas(self):
        for total in (9, 10):
            r = avaliar_aprovacao(total, 1, Decimal('10.00'))
            self.assertEqual(r.percentual_minimo, Decimal(10))
            self.assertEqual(r.minimo_acertos, 1)
            self.assertTrue(r.aprovado)

    def test_manual_limite_decimal(self):
        self.assertTrue(avaliar_aprovacao(10, 7, '70.00').aprovado)
        self.assertFalse(avaliar_aprovacao(10, 7, '70.01').aprovado)
        self.assertEqual(avaliar_aprovacao(10, 7, '70.01').minimo_acertos, 8)

    def test_manual_arredondamento_para_cima(self):
        self.assertEqual(avaliar_aprovacao(6, 4, '66.67').minimo_acertos, 5)
        self.assertFalse(avaliar_aprovacao(6, 4, '66.67').aprovado)
        self.assertTrue(avaliar_aprovacao(6, 4, '66.66').aprovado)

    def test_percentual_arredondado_nao_decide(self):
        r = avaliar_aprovacao(30001, 21000, '70.00')
        self.assertEqual(r.percentual, Decimal('70.00'))
        self.assertFalse(r.aprovado)
        self.assertEqual(r.minimo_acertos, 21001)

    def test_cem_porcento(self):
        self.assertTrue(avaliar_aprovacao(6, 6, 100).aprovado)
        self.assertFalse(avaliar_aprovacao(6, 5, 100).aprovado)
        self.assertEqual(avaliar_aprovacao(6, 6, 100).percentual, Decimal('100.00'))

    def test_menor_manual_positivo(self):
        self.assertEqual(avaliar_aprovacao(10, 1, '0.01').minimo_acertos, 1)
        self.assertFalse(avaliar_aprovacao(10, 0, '0.01').aprovado)

    def test_uma_questao_e_zero_acertos(self):
        self.assertEqual(avaliar_aprovacao(1, 0).minimo_acertos, 1)
        self.assertFalse(avaliar_aprovacao(1, 0).aprovado)
        self.assertTrue(avaliar_aprovacao(1, 1).aprovado)
        self.assertEqual(avaliar_aprovacao(1, 0).percentual, Decimal('0.00'))

    def test_configuracoes_invalidas(self):
        for valor in (0, -1, 101, True, False, 70.0, '', 'abc', 'NaN',
                      'Infinity', '-Infinity', '70,5', '70.001', '0.001', object()):
            with self.subTest(valor=repr(valor)):
                with self.assertRaises(ConfiguracaoAprovacaoInvalida):
                    avaliar_aprovacao(10, 7, valor)

    def test_tipos_validos_e_zeros_finais(self):
        for valor in (70, Decimal('70.00'), '70.000', '7E+1'):
            self.assertEqual(validar_nota_minima_percentual(valor), Decimal(70))
        self.assertIsNone(validar_nota_minima_percentual(None))

    def test_contagens_invalidas(self):
        for total, acertos in ((-1, 0), (True, 0), (1.0, 0), ('6', 0),
                              (None, 0), (6, -1), (6, 7), (6, True),
                              (6, 4.0), (6, '4'), (0, 1)):
            with self.subTest(total=total, acertos=acertos):
                with self.assertRaises(ValueError):
                    avaliar_aprovacao(total, acertos)

    def test_sem_quiz_exige_conclusao_explicita(self):
        self.assertFalse(avaliar_aprovacao(0, 0).aprovado)
        r = avaliar_aprovacao(0, 0, conclusao_explicita=True)
        self.assertTrue(r.aprovado)
        self.assertEqual((r.total_questoes, r.total_acertos, r.minimo_acertos), (0, 0, 0))
        self.assertIsNone(r.percentual)
        self.assertIsNone(r.percentual_minimo)

    def test_sem_quiz_nao_usa_nota_manual(self):
        self.assertTrue(avaliar_aprovacao(0, 0, 100, conclusao_explicita=True).aprovado)
        with self.assertRaises(ConfiguracaoAprovacaoInvalida):
            avaliar_aprovacao(0, 0, 0, conclusao_explicita=True)

    def test_flag_conclusao_invalida(self):
        for valor in (1, None, 'true'):
            with self.assertRaises(ValueError):
                avaliar_aprovacao(0, 0, conclusao_explicita=valor)

    def test_deterministico_independente_contexto_decimal(self):
        esperado = avaliar_aprovacao(2147483647, 1503238552, '70.01')
        with localcontext() as contexto:
            contexto.prec = 2
            self.assertEqual(avaliar_aprovacao(2147483647, 1503238552, '70.01'), esperado)

    def test_resultado_imutavel(self):
        r = avaliar_aprovacao(10, 7)
        with self.assertRaises(FrozenInstanceError):
            r.aprovado = False

    def test_arredondamento_percentual_half_up(self):
        self.assertEqual(avaliar_aprovacao(32, 21).percentual, Decimal('65.63'))

    def test_minimos_conferem_com_fracao_exata(self):
        for total in range(1, 101):
            for manual in (None, '0.01', '66.66', '66.67', '70.01', '99.99', '100'):
                nota = manual or ('80' if total <= 9 else '70')
                esperado = ceil(total * Fraction(nota) / 100)
                with self.subTest(total=total, manual=manual):
                    r = avaliar_aprovacao(total, esperado, manual)
                    self.assertEqual(r.minimo_acertos, esperado)
                    self.assertTrue(r.aprovado)
                    self.assertFalse(avaliar_aprovacao(total, esperado - 1, manual).aprovado)

    def test_modelo_valida_sem_banco(self):
        t = Treinamento(titulo='Teste', nota_minima_percentual='80.50')
        self.assertEqual(t.nota_minima_percentual, Decimal('80.50'))
        with self.assertRaises(ConfiguracaoAprovacaoInvalida):
            t.nota_minima_percentual = '100.01'
        self.assertEqual(t.nota_minima_percentual, Decimal('80.50'))
        t.nota_minima_percentual = None
        self.assertIsNone(t.nota_minima_percentual)


class CompatibilidadeAprovacaoTest(unittest.TestCase):
    postgres = False
    setUpClass = quiz_fixture.QuizTest.__dict__['setUpClass']
    tearDownClass = quiz_fixture.QuizTest.__dict__['tearDownClass']
    setUp = quiz_fixture.QuizTest.setUp
    request = quiz_fixture.QuizTest.request
    state = quiz_fixture.QuizTest.state
    finish_modules = quiz_fixture.QuizTest.finish_modules
    post_quiz = quiz_fixture.QuizTest.post_quiz
    without_quiz = quiz_fixture.QuizTest.without_quiz
    snapshot = quiz_fixture.QuizTest.snapshot

    def test_null_padrao_e_coluna_decimal(self):
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            self.assertIsNone(p.treinamento.nota_minima_percentual)
            p.treinamento.nota_minima_percentual = '80.50'
            db.session.commit()
            db.session.expire_all()
            t = db.session.get(Treinamento, p.treinamento_id)
            self.assertEqual(t.nota_minima_percentual, Decimal('80.50'))

    def test_nota_baixa_ainda_finaliza_com_configuracao_manual(self):
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            p.treinamento.nota_minima_percentual = 100
            db.session.commit()
        self.finish_modules()
        respostas = dict(self.answers)
        respostas[f'questao_{self.qids[1]}'] = self.wrong
        self.assertEqual(self.post_quiz(respostas).status_code, 303)
        with self.app.app_context():
            r = ResultadoQuiz.query.filter_by(participacao_id=self.pid).one()
            self.assertEqual(r.percentual, 50)
            self.assertIsNotNone(r.participacao.concluido_em)
        antes = self.snapshot()
        self.assertEqual(self.post_quiz(self.answers).status_code, 303)
        self.assertEqual(self.snapshot(), antes)

    def test_sem_quiz_preserva_finalizacao_explicita(self):
        self.without_quiz()
        self.finish_modules()
        antes = self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 200)
        self.assertEqual(self.snapshot(), antes)
        self.assertIsNone(self.state()[2])
        self.assertEqual(self.post_quiz().status_code, 302)
        self.assertIsNotNone(self.state()[2])
        with self.app.app_context():
            self.assertEqual(ResultadoQuiz.query.count(), 0)


class CompatibilidadeAprovacaoPostgresTest(CompatibilidadeAprovacaoTest):
    postgres = True


if __name__ == '__main__':
    unittest.main()
