"""Validação de quiz/conclusão; usa somente bancos isolados de testes."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from sqlalchemy import select
from app.models.submissao_quiz import SubmissaoQuiz
from app.utils.submissoes_quiz import ErroQuiz
import app.simulacoes.preparacao
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import db
from app.models.questao import Questao
from app.models.alternativa import Alternativa
from app.models.resultado_quiz import ResultadoQuiz
from app.models.participacao_treinamento import ParticipacaoTreinamento
import test_portal_treinamento_progresso as progresso_fixture


class QuizTest(unittest.TestCase):
    postgres = False
    setUpClass = progresso_fixture.ProgressoTest.__dict__['setUpClass']
    tearDownClass = progresso_fixture.ProgressoTest.__dict__['tearDownClass']
    request = progresso_fixture.ProgressoTest.request
    state = progresso_fixture.ProgressoTest.state

    def setUp(self):
        progresso_fixture.ProgressoTest.setUp(self)
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            questions = [Questao(treinamento_id=p.treinamento_id, enunciado=f'Q{i}', ordem=i) for i in (1, 2)]
            other = Questao(treinamento_id=db.session.get(progresso_fixture.ModuloTreinamento, self.foreign).treinamento_id,
                           enunciado='Outra', ordem=1)
            db.session.add_all(questions + [other])
            db.session.flush()
            options = [Alternativa(questao_id=q.id, texto='Correta', correta=True) for q in questions]
            wrong = Alternativa(questao_id=questions[1].id, texto='Incorreta', correta=False)
            foreign = Alternativa(questao_id=other.id, texto='Estrangeira', correta=True)
            db.session.add_all(options + [wrong, foreign])
            db.session.commit()
            self.qids = [q.id for q in questions]
            self.answers = {f'questao_{q.id}': str(a.id) for q, a in zip(questions, options)}
            self.wrong = str(wrong.id)
            self.foreign_answer = str(foreign.id)
            self.foreign_question = other.id
        self.quiz_url = '/aprender/t/principal/quiz'
        self.conclusion_url = '/aprender/t/principal/conclusao'

    def finish_modules(self):
        for index in range(3):
            self.assertEqual(self.request(index).status_code, 302)

    def without_quiz(self):
        with self.app.app_context():
            for qid in self.qids:
                db.session.delete(db.session.get(Questao, qid))
            db.session.commit()

    def post_quiz(self, answers=None):
        # Test clients explicitly prepare once and keep their original form identity for retries.
        data = MultiDict(answers or {})
        with self.app.app_context():
            has_quiz = bool(Questao.query.filter_by(treinamento_id=db.session.get(ParticipacaoTreinamento,self.pid).treinamento_id).count())
        if has_quiz:
            identity = getattr(self, 'form_identity', None)
            if identity is None:
                prepared = self.client.post(self.quiz_url+'/preparar', headers=self.headers)
                if prepared.status_code != 303:
                    return prepared
                with self.app.app_context():
                    identity = str(db.session.execute(select(SubmissaoQuiz.id).where(
                        SubmissaoQuiz.participacao_id == self.pid)).scalar_one())
                self.form_identity = identity
            data['submissao_id'] = identity
        return self.client.post(self.quiz_url, data=data, headers=self.headers)

    def snapshot(self):
        state = self.state()
        with self.app.app_context():
            results = [(r.id, r.total_questoes, r.total_acertos, r.percentual, r.concluido_em)
                       for r in ResultadoQuiz.query.all()]
        return state, results

    def test_get_before_modules(self):
        before = self.snapshot()
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(f'/modulo/{self.ids[0]}'))
        self.assertEqual(self.snapshot(), before)

    def test_post_before_modules(self):
        before = self.snapshot()
        self.assertEqual(self.post_quiz(self.answers).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_partial_progress_blocks_both_methods(self):
        self.request()
        before = self.snapshot()
        response = self.client.get(self.quiz_url)
        self.assertTrue(response.location.endswith(f'/modulo/{self.ids[1]}'))
        self.assertEqual(self.post_quiz(self.answers).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_get_eligible_does_not_conclude(self):
        self.finish_modules()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_empty_answers(self):
        self.finish_modules()
        before = self.snapshot()
        response = self.post_quiz()
        self.assertEqual(response.status_code, 400)
        self.assertIn('Responda todas as questões', response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)

    def test_partial_answers(self):
        self.finish_modules()
        before = self.snapshot()
        key = next(iter(self.answers))
        self.assertEqual(self.post_quiz({key: self.answers[key]}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_complete_answers(self):
        self.finish_modules()
        before = self.state()
        self.assertEqual(self.post_quiz(self.answers).status_code, 303)
        with self.app.app_context():
            result = ResultadoQuiz.query.one()
            self.assertEqual((result.total_questoes, result.total_acertos, result.percentual), (2, 2, 100.0))
        after = self.state()
        self.assertEqual(after[:2], before[:2])
        self.assertEqual(after[3], before[3])
        self.assertIsNotNone(after[2])

    def test_score_unchanged_no_minimum(self):
        self.finish_modules()
        answers = dict(self.answers)
        answers[f'questao_{self.qids[1]}'] = self.wrong
        self.assertEqual(self.post_quiz(answers).status_code, 303)
        with self.app.app_context():
            result = ResultadoQuiz.query.one()
            self.assertEqual((result.total_acertos, result.percentual), (1, 50.0))
        self.assertIsNotNone(self.state()[2])

    def test_invalid_or_other_question_option(self):
        self.finish_modules()
        before = self.snapshot()
        for value in ('invalida', '', '999999', self.foreign_answer, self.answers[f'questao_{self.qids[1]}']):
            answers = dict(self.answers)
            answers[f'questao_{self.qids[0]}'] = value
            self.assertEqual(self.post_quiz(answers).status_code, 400)
            self.assertEqual(self.snapshot(), before)

    def test_foreign_question_field(self):
        self.finish_modules()
        before = self.snapshot()
        answers = dict(self.answers, **{f'questao_{self.foreign_question}': self.foreign_answer})
        self.assertEqual(self.post_quiz(answers).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_field_rejected(self):
        self.finish_modules()
        before = self.snapshot()
        answers = MultiDict(self.answers)
        key = next(iter(self.answers))
        answers.add(key, self.answers[key])
        self.assertEqual(self.post_quiz(answers).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_retry_same_quiz(self):
        self.finish_modules()
        self.post_quiz(self.answers)
        before = self.snapshot()
        for answers in (self.answers, {}, {'questao_invalida': '0'}):
            self.assertEqual(self.post_quiz(answers).status_code, 303)
            self.assertEqual(self.snapshot(), before)

    def test_no_quiz_incomplete(self):
        self.without_quiz()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 302)
        self.assertEqual(self.post_quiz().status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_no_quiz_complete_explicit_post(self):
        self.without_quiz()
        self.finish_modules()
        before = self.snapshot()
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Concluir treinamento', response.get_data(as_text=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.post_quiz().status_code, 302)
        self.assertIsNotNone(self.state()[2])
        self.assertEqual(self.snapshot()[1], [])
        concluded = self.snapshot()
        self.assertEqual(self.post_quiz().status_code, 302)
        self.assertEqual(self.snapshot(), concluded)

    def test_conclusion_without_timestamp(self):
        before = self.snapshot()
        response = self.client.get(self.conclusion_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(f'/modulo/{self.ids[0]}'))
        self.assertEqual(self.snapshot(), before)
        self.finish_modules()
        response = self.client.get(self.conclusion_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('/quiz'))
        self.assertIsNone(self.state()[2])

    def test_conclusion_after_valid_quiz(self):
        self.finish_modules()
        self.post_quiz(self.answers)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.conclusion_url).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_historical_completed_participation(self):
        self.finish_modules()
        self.post_quiz(self.answers)
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            p.ultimo_modulo_id = self.foreign
            db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.conclusion_url).status_code, 200)
        self.assertEqual(self.client.get(self.quiz_url).status_code, 200)
        self.assertEqual(self.post_quiz().status_code, 303)
        self.assertEqual(self.snapshot(), before)

    def test_historical_completed_without_result(self):
        self.without_quiz()
        self.finish_modules()
        self.post_quiz()
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            p.ultimo_modulo_id = None
            # Fixture de legado anterior ao congelamento; SQL Core apenas para simular histórico.
            db.session.flush()
            db.session.connection().execute(Questao.__table__.insert().values(
                treinamento_id=p.treinamento_id, enunciado='Nova', ordem=1))
            db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.conclusion_url).status_code, 200)
        self.assertEqual(self.client.get(self.quiz_url).status_code, 302)
        self.assertEqual(self.client.post(self.quiz_url, headers=self.headers).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_result_without_conclusion_is_not_repaired(self):
        self.finish_modules()
        with self.app.app_context():
            db.session.add(ResultadoQuiz(participacao_id=self.pid, total_questoes=2,
                                         total_acertos=1, percentual=50))
            db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 409)
        self.assertEqual(self.post_quiz(self.answers).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_inconsistent_cursor(self):
        with self.app.app_context():
            db.session.get(ParticipacaoTreinamento, self.pid).ultimo_modulo_id = self.foreign
            db.session.commit()
        before = self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url).status_code, 409)
        self.assertEqual(self.post_quiz(self.answers).status_code, 409)
        self.assertEqual(self.client.get(self.conclusion_url).status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_empty_training(self):
        before = self.snapshot()
        self.assertEqual(self.client.get('/aprender/t/vazio/quiz').status_code, 302)
        self.assertEqual(self.client.post('/aprender/t/vazio/quiz', headers=self.headers).status_code, 403)
        with self.app.app_context():
            self.assertIsNone(ParticipacaoTreinamento.query.filter_by(token='vazio').one().concluido_em)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_token_and_csrf(self):
        before = self.snapshot()
        self.assertEqual(self.client.get('/aprender/t/invalido/quiz').status_code, 404)
        self.assertEqual(self.client.post('/aprender/t/invalido/quiz', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post(self.quiz_url, data=self.answers).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_commit_failure_is_atomic(self):
        self.finish_modules()
        before = self.snapshot()
        self.client.post(self.quiz_url+'/preparar', headers=self.headers)
        with patch('app.portal_treinamento.routes.corrigir_submissao', side_effect=ErroQuiz('falha_banco')):
            self.assertEqual(self.post_quiz(self.answers).status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_templates_compile(self):
        for name in ('quiz', 'sem_quiz', 'resultado_quiz', 'conclusao'):
            self.app.jinja_env.get_template(f'portal_treinamento/{name}.html')


class QuizPostgresTest(QuizTest):
    postgres = True

    def concurrent_posts(self, payloads):
        with self.app.app_context():
            has_quiz = bool(Questao.query.filter_by(treinamento_id=db.session.get(ParticipacaoTreinamento,self.pid).treinamento_id).count())
        with self.app.app_context():
            completed = db.session.get(ParticipacaoTreinamento,self.pid).ultimo_modulo_id == self.ids[-1]
        if has_quiz and completed:
            self.client.post(self.quiz_url+'/preparar', headers=self.headers)
            with self.app.app_context():
                identity = str(db.session.execute(select(SubmissaoQuiz.id).where(SubmissaoQuiz.participacao_id == self.pid)).scalar_one())
            payloads = [dict(payload, submissao_id=identity) for payload in payloads]
        barrier = Barrier(len(payloads))
        def worker(payload):
            client = self.app.test_client()
            headers = {'X-CSRFToken': client.get('/_csrf').json['token']}
            barrier.wait(timeout=10)
            return client.post(self.quiz_url, data=payload, headers=headers).status_code
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(worker, payloads))

    def test_concurrent_quiz_submission(self):
        self.finish_modules()
        statuses = self.concurrent_posts([self.answers] * 6)
        self.assertEqual(statuses, [303] * 6)
        self.assertEqual(len(self.snapshot()[1]), 1)
        self.assertIsNotNone(self.state()[2])

    def test_concurrent_no_quiz_submission(self):
        self.without_quiz()
        self.finish_modules()
        self.assertEqual(self.concurrent_posts([{}] * 6), [302] * 6)
        self.assertEqual(self.snapshot()[1], [])
        self.assertIsNotNone(self.state()[2])

    def test_concurrent_early_submission(self):
        before = self.snapshot()
        self.assertEqual(self.concurrent_posts([self.answers] * 6), [403] * 6)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
