"""Portal de feedback: rotas/templates reais, CSRF e bancos isolados."""
import unittest,re
from datetime import datetime,timezone
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import db
from app.models.feedback_treinamento import FeedbackTreinamento as F
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.resultado_quiz import ResultadoQuiz as Q
from app.models.tempo_treinamento import TempoTreinamento as T
from app.models.campanha import Campanha
from app.models.treinamento import Treinamento
from app.utils.feedback_treinamento import CAMPOS_NOTAS
import test_portal_treinamento_progresso as fixture


def payload(**extra):
    return {**dict.fromkeys(CAMPOS_NOTAS,'4'), **extra}


class PortalFeedbackTest(unittest.TestCase):
    postgres=False
    setUpClass=fixture.ProgressoTest.__dict__['setUpClass']
    tearDownClass=fixture.ProgressoTest.__dict__['tearDownClass']
    setUp=fixture.ProgressoTest.setUp
    url='/aprender/t/principal/feedback'

    def complete(self,quiz=False,legacy=False):
        with self.app.app_context():
            p=db.session.get(P,self.pid)
            p.concluido_em=datetime(2026,1,1,tzinfo=timezone.utc)
            if legacy:
                p.iniciado_em=None;p.ultimo_modulo_id=self.foreign
                for t in list(p.tempos_treinamento):db.session.delete(t)
            else:
                p.iniciado_em=datetime(2025,12,31,tzinfo=timezone.utc);p.ultimo_modulo_id=self.ids[-1]
            if quiz:db.session.add(Q(participacao_id=self.pid,total_questoes=1,total_acertos=1,percentual=100))
            db.session.commit()

    def feedback(self):
        with self.app.app_context():
            return db.session.execute(select(F)).scalar_one_or_none()

    def post(self,data=None,follow=False):
        return self.client.post(self.url,data=payload() if data is None else data,headers=self.headers,follow_redirects=follow)

    def snapshot(self):
        with self.app.app_context():
            return {m.__tablename__:[tuple(row) for row in db.session.execute(select(m.__table__).order_by(m.id))]
                    for m in [P,Q,T,Campanha]}

    def test_get_before_completion(self):
        before=self.snapshot()
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertIsNone(self.feedback());self.assertEqual(self.snapshot(),before)

    def test_post_before_completion(self):
        self.assertEqual(self.post().status_code,403);self.assertIsNone(self.feedback())

    def test_get_completed_no_write_and_no_preselection(self):
        self.complete();before=self.snapshot()
        r=self.client.get(self.url);html=r.get_data(as_text=True)
        self.assertEqual(r.status_code,200)
        self.assertIn('Principal',html);self.assertIn('csrf_token',html)
        self.assertEqual(html.count('type="radio"'),20);self.assertNotIn(' checked',html)
        self.assertIn('Não inclua dados pessoais, informações confidenciais ou detalhes de incidentes.',html)
        self.assertNotIn('recomendaria',html.lower())
        self.assertIsNone(self.feedback());self.assertEqual(self.snapshot(),before)

    def test_with_quiz_invitation_and_return(self):
        self.complete(quiz=True)
        html=self.client.get('/aprender/t/principal/quiz').get_data(as_text=True)
        self.assertIn('href="'+self.url+'"',html)
        html=self.client.get(self.url).get_data(as_text=True)
        self.assertIn('href="/aprender/t/principal/quiz"',html)

    def test_without_quiz_invitation_and_return(self):
        self.complete()
        html=self.client.get('/aprender/t/principal/conclusao').get_data(as_text=True)
        self.assertIn('href="'+self.url+'"',html)
        self.assertIn('href="/aprender/t/principal/conclusao"',self.client.get(self.url).get_data(as_text=True))

    def test_legacy_without_start_time_quiz_and_with_inconsistent_cursor(self):
        self.complete(legacy=True)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.post(follow=True).status_code,200)
        self.assertIsNotNone(self.feedback())

    def test_valid_prg_and_confirmation(self):
        self.complete();r=self.post()
        self.assertEqual(r.status_code,303);self.assertEqual(r.location,self.url)
        html=self.client.get(r.location).get_data(as_text=True)
        self.assertIn('Sua avaliação foi recebida',html);self.assertIn('Avaliação já enviada',html)
        self.assertNotIn('<form',html);self.assertIsNotNone(self.feedback())

    def test_missing_field_preserves_answers(self):
        self.complete();data=payload(comentario='Meu texto');data.pop('avaliacao_utilidade')
        r=self.post(data);html=r.get_data(as_text=True)
        self.assertEqual(r.status_code,400);self.assertIn('Meu texto',html)
        self.assertEqual(html.count('checked'),3);self.assertIsNone(self.feedback())

    def test_invalid_note_and_repeated_fields(self):
        self.complete()
        for value in ['0','6','texto','1.5']:
            self.assertEqual(self.post(payload(avaliacao_geral=value)).status_code,400)
        data=MultiDict(payload());data.add('avaliacao_geral','5')
        self.assertEqual(self.post(data).status_code,400);self.assertIsNone(self.feedback())

    def test_empty_comment_null(self):
        self.complete();self.assertEqual(self.post(payload(comentario='  \n ')).status_code,303)
        self.assertIsNone(self.feedback().comentario)

    def test_comment_at_limit(self):
        self.complete();self.assertEqual(self.post(payload(comentario='é'*2000)).status_code,303)
        self.assertEqual(len(self.feedback().comentario),2000)

    def test_comment_above_limit_preserved(self):
        self.complete();comment='x'*2001;r=self.post(payload(comentario=comment))
        self.assertEqual(r.status_code,400);self.assertIn(comment,r.get_data(as_text=True))
        self.assertIsNone(self.feedback())

    def test_csrf_missing_and_invalid(self):
        self.complete()
        for headers in [{},{'X-CSRFToken':'invalid'}]:
            self.assertEqual(self.client.post(self.url,data=payload(),headers=headers).status_code,400)
        self.assertIsNone(self.feedback())

    def test_actual_hidden_csrf_form_submission(self):
        self.complete();html=self.client.get(self.url).get_data(as_text=True)
        token=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
        self.assertEqual(self.client.post(self.url,data=payload(csrf_token=token)).status_code,303)

    def test_reopen_and_second_post_unchanged(self):
        self.complete();self.post(payload(comentario='Original'))
        first=self.feedback();r=self.client.get(self.url)
        self.assertIn('Avaliação já enviada',r.get_data(as_text=True));self.assertNotIn('<form',r.get_data(as_text=True))
        self.assertEqual(self.post(payload(avaliacao_geral='1',comentario='Substituto')).status_code,303)
        after=self.feedback()
        self.assertEqual((after.id,after.avaliacao_geral,after.comentario,after.criado_em),
                         (first.id,first.avaliacao_geral,first.comentario,first.criado_em))
        self.assertIn('Avaliação já enviada',self.client.get('/aprender/t/principal/conclusao').get_data(as_text=True))

    def test_comment_html_escaped_on_error_and_confirmation(self):
        self.complete();comment='<script>alert(1)</script>\nTexto simples'
        r=self.post(payload(avaliacao_geral='6',comentario=comment))
        html=r.get_data(as_text=True);self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)
        html=self.post(payload(comentario=comment),follow=True).get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertEqual(self.feedback().comentario,comment)

    def test_snapshot_unchanged_with_quiz(self):
        self.complete(quiz=True);before=self.snapshot()
        self.client.get(self.url);self.post(payload(comentario='Avaliação'))
        self.client.get(self.url);self.post(payload(avaliacao_geral='5'))
        self.assertEqual(before,self.snapshot())

    def test_invalid_token_and_inactive_training(self):
        self.complete()
        self.assertEqual(self.client.get('/aprender/t/desconhecido/feedback').status_code,404)
        self.assertEqual(self.client.post('/aprender/t/desconhecido/feedback',data=payload(),headers=self.headers).status_code,404)
        with self.app.app_context():
            p=db.session.get(P,self.pid);p.treinamento.ativo=False;db.session.commit()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.post().status_code,404);self.assertIsNone(self.feedback())

    def test_persistence_failure_keeps_history(self):
        self.complete();before=self.snapshot()
        with patch('app.portal_treinamento.routes.registrar_feedback',side_effect=SQLAlchemyError('test')):
            self.assertEqual(self.post().status_code,500)
        self.assertIsNone(self.feedback());self.assertEqual(before,self.snapshot())


class PortalFeedbackPostgresTest(PortalFeedbackTest):
    postgres=True
