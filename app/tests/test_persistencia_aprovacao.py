"""19.1B: campos nullable e invariantes locais; rotas seguem sem aprovação."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.resultado_quiz import ResultadoQuiz as R
from app.utils.aprovacao_treinamento import avaliar_aprovacao
# Ordem de inicialização exigida pela dependência circular preexistente.
import app.simulacoes.preparacao
import test_portal_treinamento_progresso as progresso
import test_portal_treinamento_quiz as quiz


class PersistenciaAprovacaoTest(unittest.TestCase):
    postgres = False
    setUpClass = progresso.ProgressoTest.__dict__['setUpClass']
    tearDownClass = progresso.ProgressoTest.__dict__['tearDownClass']
    setUp = progresso.ProgressoTest.setUp

    def resultado(self, **campos):
        dados = dict(participacao_id=self.pid, total_questoes=6,
                     total_acertos=4, percentual=66.67)
        dados.update(campos)
        r = R(**dados)
        db.session.add(r)
        db.session.commit()
        return r

    def invalido(self, **campos):
        dados = dict(percentual_minimo_aplicado=Decimal(80),
                     minimo_acertos_aplicado=5, aprovado=False)
        dados.update(campos)
        with self.assertRaises(IntegrityError):
            self.resultado(**dados)
        db.session.rollback()

    def test_participacao_nova_sem_aprovacao(self):
        with self.app.app_context():
            self.assertIsNone(db.session.get(P, self.pid).aprovado_em)

    def test_timestamp_independente_da_conclusao(self):
        with self.app.app_context():
            instante = datetime(2026, 10, 7, 12, 30, 45, 123456, tzinfo=timezone.utc)
            p = db.session.get(P, self.pid)
            p.aprovado_em = instante
            db.session.commit(); db.session.expire_all()
            p = db.session.get(P, self.pid)
            lido = p.aprovado_em
            lido = (lido.astimezone(timezone.utc) if lido.tzinfo is not None
                    else lido.replace(tzinfo=timezone.utc))
            self.assertEqual(lido, instante)
            self.assertIsNone(p.concluido_em)

    def test_resultado_legado_todos_null(self):
        with self.app.app_context():
            r = self.resultado()
            db.session.expire_all()
            self.assertIsNone(r.percentual_minimo_aplicado)
            self.assertIsNone(r.minimo_acertos_aplicado)
            self.assertIsNone(r.aprovado)
            self.assertIsNone(r.participacao.aprovado_em)

    def test_legado_inconsistente_preservado_sem_classificacao(self):
        with self.app.app_context():
            r = self.resultado(total_questoes=0, total_acertos=-1, percentual=500)
            self.assertIsNone(r.aprovado)

    def test_avaliado_reprovado(self):
        with self.app.app_context():
            r = self.resultado(percentual_minimo_aplicado=Decimal('80.00'),
                               minimo_acertos_aplicado=5, aprovado=False)
            db.session.expire_all()
            self.assertEqual(r.percentual_minimo_aplicado, Decimal('80.00'))
            self.assertEqual(r.minimo_acertos_aplicado, 5)
            self.assertIs(r.aprovado, False)

    def test_avaliado_aprovado_sem_escrita_cruzada(self):
        with self.app.app_context():
            r = self.resultado(total_acertos=5, percentual=83.33,
                               percentual_minimo_aplicado=80,
                               minimo_acertos_aplicado=5, aprovado=True)
            self.assertIs(r.aprovado, True)
            self.assertIsNone(r.participacao.aprovado_em)

    def test_snapshot_nao_muda_com_treinamento(self):
        with self.app.app_context():
            r = self.resultado(percentual_minimo_aplicado=80,
                               minimo_acertos_aplicado=5, aprovado=False)
            r.participacao.treinamento.nota_minima_percentual = Decimal('20.00')
            db.session.commit(); db.session.expire_all()
            self.assertEqual(r.percentual_minimo_aplicado, Decimal(80))
            self.assertEqual(r.minimo_acertos_aplicado, 5)
            self.assertIs(r.aprovado, False)

    def test_todas_combinacoes_parciais_invalidas(self):
        with self.app.app_context():
            nomes = ('percentual_minimo_aplicado', 'minimo_acertos_aplicado', 'aprovado')
            valores = (Decimal(80), 5, False)
            for mascara in range(1, 7):
                with self.subTest(mascara=mascara):
                    self.invalido(**{n: v if mascara & (1 << i) else None
                                     for i, (n, v) in enumerate(zip(nomes, valores))})

    def test_percentuais_invalidos(self):
        with self.app.app_context():
            for valor in (-1, 0, Decimal('100.01'), 101):
                with self.subTest(valor=valor):
                    self.invalido(percentual_minimo_aplicado=valor)

    def test_minimos_invalidos(self):
        with self.app.app_context():
            for valor in (-1, 0, 7):
                with self.subTest(valor=valor):
                    self.invalido(minimo_acertos_aplicado=valor)

    def test_minimo_deve_corresponder_ao_percentual(self):
        with self.app.app_context():
            for minimo in (4, 6):
                with self.subTest(minimo=minimo):
                    self.invalido(minimo_acertos_aplicado=minimo,
                                  aprovado=4 >= minimo)

    def test_decisao_deve_corresponder_aos_acertos(self):
        with self.app.app_context():
            self.invalido(aprovado=True)
            self.invalido(total_acertos=5, percentual=83.33, aprovado=False)

    def test_contagens_e_percentual_invalidos_em_avaliado(self):
        with self.app.app_context():
            for dados in ({'total_questoes': 0}, {'total_questoes': -1},
                          {'total_acertos': -1}, {'total_acertos': 7},
                          {'percentual': -1}, {'percentual': 101}):
                with self.subTest(dados=dados):
                    self.invalido(**dados)

    def test_manual_cem_porcento(self):
        with self.app.app_context():
            r = self.resultado(total_acertos=6, percentual=100,
                               percentual_minimo_aplicado=100,
                               minimo_acertos_aplicado=6, aprovado=True)
            self.assertIs(r.aprovado, True)

    def test_constraint_aceita_criterios_do_dominio(self):
        with self.app.app_context():
            for total in (1, 5, 6, 8, 9, 10, 12, 15, 100, 2147483647):
                for manual in (None, '0.01', '66.67', '70.01', '100'):
                    with self.subTest(total=total, manual=manual):
                        a = avaliar_aprovacao(total, 0, manual)
                        r = self.resultado(total_questoes=total, total_acertos=0,
                            percentual=0, percentual_minimo_aplicado=a.percentual_minimo,
                            minimo_acertos_aplicado=a.minimo_acertos, aprovado=False)
                        db.session.delete(r); db.session.commit()

    def test_unicidade_por_participacao_preservada(self):
        with self.app.app_context():
            self.resultado()
            with self.assertRaises(IntegrityError):
                self.resultado()
            db.session.rollback()
            self.assertEqual(R.query.count(), 1)


class PersistenciaAprovacaoPostgresTest(PersistenciaAprovacaoTest):
    postgres = True


class FluxoAtualPersistenciaTest(unittest.TestCase):
    postgres = False
    setUpClass = quiz.QuizTest.__dict__['setUpClass']
    tearDownClass = quiz.QuizTest.__dict__['tearDownClass']
    setUp = quiz.QuizTest.setUp
    request = quiz.QuizTest.request
    state = quiz.QuizTest.state
    finish_modules = quiz.QuizTest.finish_modules
    post_quiz = quiz.QuizTest.post_quiz
    without_quiz = quiz.QuizTest.without_quiz
    snapshot = quiz.QuizTest.snapshot

    def test_quiz_baixa_nota_conclui_reprovado(self):
        self.finish_modules()
        dados = dict(self.answers)
        dados[f'questao_{self.qids[1]}'] = self.wrong
        self.assertEqual(self.post_quiz(dados).status_code, 303)
        with self.app.app_context():
            r = R.query.filter_by(participacao_id=self.pid).one()
            self.assertEqual(r.percentual, 50)
            self.assertIsNotNone(r.participacao.concluido_em)
            self.assertIsNone(r.participacao.aprovado_em)
            self.assertEqual((r.percentual_minimo_aplicado, r.minimo_acertos_aplicado, r.aprovado),
                             (Decimal(80), 2, False))
        antes = self.snapshot()
        self.assertEqual(self.post_quiz(self.answers).status_code, 303)
        self.assertEqual(self.snapshot(), antes)

    def test_quiz_nota_maxima_grava_aprovacao(self):
        self.finish_modules()
        self.assertEqual(self.post_quiz(self.answers).status_code, 303)
        with self.app.app_context():
            r = R.query.filter_by(participacao_id=self.pid).one()
            self.assertEqual(r.percentual, 100)
            self.assertTrue(r.aprovado)
            self.assertIsNotNone(r.participacao.aprovado_em)

    def test_sem_quiz_continua_com_finalizacao_explicita(self):
        self.without_quiz(); self.finish_modules()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 200)
        self.assertIsNone(self.state()[2])
        self.assertEqual(self.post_quiz().status_code, 302)
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            self.assertIsNotNone(p.concluido_em)
            self.assertIsNotNone(p.aprovado_em)
            self.assertEqual(R.query.count(), 0)


class FluxoAtualPersistenciaPostgresTest(FluxoAtualPersistenciaTest):
    postgres = True
