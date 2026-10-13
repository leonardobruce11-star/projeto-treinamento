"""Página real e agregações em bancos isolados; nunca usa .env."""
from datetime import datetime
from pathlib import Path
import unittest
from flask import template_rendered
from flask_login import LoginManager
from jinja2 import FileSystemLoader, StrictUndefined
from sqlalchemy.dialects import postgresql
import test_painel_colaboradores as fixture
from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento
from app.models.categoria_treinamento import CategoriaTreinamento
from app.models.treinamento import Treinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.feedback_treinamento import FeedbackTreinamento as F
from app.treinamentos.routes import treinamentos_bp
from app.colaboradores.routes import colaboradores_bp
from app.utils.feedback_treinamento import CAMPOS_NOTAS
from app.utils.tempo import formatar_tempo_ativo
from app.utils.metricas_feedback import (
    consulta_metricas_feedback, indicadores_feedback, comentarios_feedback,
)


class MetricasFeedbackTest(unittest.TestCase):
    postgres = False

    def setUp(self):
        fixture.PainelTest.setUp(self)
        self.app.config.update(TESTING=True, SECRET_KEY='isolated', LOGIN_DISABLED=True)
        LoginManager(self.app).user_loader(lambda uid: None)
        self.app.register_blueprint(treinamentos_bp)
        self.app.register_blueprint(colaboradores_bp)
        self.app.jinja_loader = FileSystemLoader(str(Path(__file__).resolve().parents[1]/'app/templates'))
        self.app.jinja_env.undefined = StrictUndefined
        self.app.jinja_env.filters['tempo_ativo'] = formatar_tempo_ativo
        self.app.jinja_env.globals['csrf_token'] = lambda: 'test'
        for name in ['dashboard.index', 'campanhas.listar', 'departamentos.listar',
                     'grupos.listar', 'auth.logout', 'categorias_treinamento.listar',
                     'cenarios.listar', 'templates_email.listar', 'landing_pages.listar',
                     'integracoes_gophish.index']:
            self.app.add_url_rule('/_layout/'+name, endpoint=name, view_func=lambda: 'layout')
        self.client = self.app.test_client()
        self.contexts = []
        self.capture = lambda sender, template, context, **kwargs: self.contexts.append(context)
        template_rendered.connect(self.capture, self.app, weak=False)
        d = Departamento(nome='DEPARTAMENTO_IDENTIFICAVEL')
        cat = CategoriaTreinamento(nome='Categoria')
        db.session.add_all([d, cat]); db.session.flush()
        c = Colaborador(nome='NOME_IDENTIFICAVEL', email='identificavel@example.invalid', departamento_id=d.id)
        self.a = Treinamento(titulo='A', categoria_id=cat.id)
        self.b = Treinamento(titulo='B')
        db.session.add_all([c, self.a, self.b]); db.session.commit()
        self.cid = c.id; self.catid = cat.id; self.sequence = 0

    def tearDown(self):
        if hasattr(self, 'capture'):
            template_rendered.disconnect(self.capture, self.app)
        fixture.PainelTest.tearDown(self)

    def add(self, t=None, nota=3, comentario=None, feedback=True, concluida=True, notas=None):
        self.sequence += 1
        p = P(token=str(self.sequence), colaborador_id=self.cid,
              treinamento_id=(t or self.a).id,
              concluido_em=datetime(2026, 1, 1) if concluida else None)
        db.session.add(p); db.session.flush()
        if feedback:
            db.session.add(F(participacao_id=p.id, comentario=comentario,
                criado_em=datetime(2026, 1, 2), **(notas or dict.fromkeys(CAMPOS_NOTAS, nota))))
        db.session.commit()
        return p.id

    def rows(self, **kwargs):
        return list(db.session.execute(consulta_metricas_feedback(**kwargs)).mappings())

    def get(self, query=''):
        r = self.client.get('/treinamentos/feedback'+query)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_data(as_text=True), self.contexts[-1]

    def test_no_feedback_and_completed_without_feedback(self):
        html, ctx = self.get()
        self.assertEqual(ctx['indicadores']['quantidade'], 0)
        self.assertIsNone(ctx['indicadores']['taxa_resposta'])
        self.add(feedback=False)
        row = self.rows()[0]
        self.assertEqual(row['concluidas'], 1)
        self.assertEqual(row['taxa_resposta'], 0)
        self.assertEqual(row['quantidade'], 0)
        self.assertIsNone(row['media_avaliacao_geral'])

    def test_one_and_four_suppressed_in_sql_and_page(self):
        for n in range(4):
            self.add(nota=5, comentario='SEGREDO_PEQUENO')
            row = self.rows()[0]
            self.assertEqual(row['quantidade'], n+1)
            self.assertFalse(row['permitido'])
            for campo in CAMPOS_NOTAS:
                self.assertIsNone(row['media_'+campo])
                for nota in range(1, 6): self.assertIsNone(row[f'{campo}_{nota}'])
            html, ctx = self.get(f'?treinamento_id={self.a.id}')
            self.assertNotIn('SEGREDO_PEQUENO', html)
            self.assertEqual(ctx['comentarios']['itens'], [])

    def test_five_four_means_distributions_and_rate(self):
        for n in range(1, 6):
            self.add(notas=dict(zip(CAMPOS_NOTAS, [n, 6-n, 4, 2])))
        self.add(feedback=False)
        self.add(feedback=False, concluida=False)
        row = self.rows()[0]
        self.assertTrue(row['permitido'])
        self.assertEqual(row['quantidade'], 5)
        self.assertEqual(row['concluidas'], 6)
        self.assertAlmostEqual(row['taxa_resposta'], 100*5/6)
        for campo, media in zip(CAMPOS_NOTAS, [3, 3, 4, 2]):
            self.assertEqual(row['media_'+campo], media)
            self.assertEqual(sum(row[f'{campo}_{n}'] for n in range(1, 6)), 5)
        for campo in CAMPOS_NOTAS[:2]:
            self.assertEqual([row[f'{campo}_{n}'] for n in range(1, 6)], [1]*5)
        self.assertEqual(row['avaliacao_clareza_4'], 5)
        self.assertEqual(row['avaliacao_utilidade_2'], 5)
        html, _ = self.get(f'?treinamento_id={self.a.id}')
        self.assertIn('Quantidade por nota', html)
        self.assertIn('83.3%', html)

    def test_multiple_trainings_order_filters_and_small_group_no_bypass(self):
        for n in range(5): self.add(nota=1, comentario='Permitido')
        self.add(self.b, nota=5, comentario='SEGREDO_B')
        rows = self.rows()
        self.assertEqual([r['titulo'] for r in rows], ['A', 'B'])
        self.assertEqual([r['quantidade'] for r in rows], [5, 1])
        html, ctx = self.get()
        self.assertNotIn('SEGREDO_B', html)
        self.assertEqual(ctx['indicadores']['quantidade'], 6)
        self.assertEqual(len(self.rows(categoria_id=self.catid)), 1)
        self.assertEqual(len(self.rows(treinamento_id=self.b.id)), 1)
        html, _ = self.get(f'?treinamento_id={self.b.id}')
        self.assertNotIn('SEGREDO_B', html)

    def test_comments_empty_escaped_and_no_identity(self):
        for comment in [None, '', '   ', '\t\n ', '<script>alert("x")</script>']:
            self.add(comentario=comment)
        html, ctx = self.get()
        self.assertEqual(ctx['comentarios']['total'], 1)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert', html)
        for identity in ['NOME_IDENTIFICAVEL', 'identificavel@example.invalid', 'DEPARTAMENTO_IDENTIFICAVEL']:
            self.assertNotIn(identity, html)
        self.assertNotIn('anônimo', html.lower())
        self.assertEqual(set(ctx['comentarios']['itens'][0]), {'comentario', 'criado_em', 'titulo'})
        self.assertIn('02/01/2026', html)

    def test_pagination_stable_dates_and_filter_preserved(self):
        for n in range(23): self.add(comentario=f'Comentário {n}')
        pages = []
        for page, size in [(1, 10), (2, 10), (3, 3)]:
            html, ctx = self.get(f'?treinamento_id={self.a.id}&categoria_id={self.catid}&page={page}')
            result = ctx['comentarios']
            self.assertEqual(result['total'], 23)
            self.assertEqual(result['paginas'], 3)
            self.assertEqual(len(result['itens']), size)
            pages.extend(c['comentario'] for c in result['itens'])
            if page < 3:
                self.assertIn(f'page={page+1}', html)
                self.assertIn(f'treinamento_id={self.a.id}', html)
                self.assertIn(f'categoria_id={self.catid}', html)
        self.assertEqual(pages, [f'Comentário {n}' for n in reversed(range(23))])
        for page in ['0', '-1', 'abc', '999999999999999999999']:
            self.get('?page='+page)

    def test_individual_history_only_status_and_date(self):
        self.add(nota=5, comentario='COMENTARIO_INDIVIDUAL_SECRETO')
        self.add(feedback=False)
        self.statements.clear()
        r = self.client.get(f'/colaboradores/{self.cid}')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('Avaliação enviada', html)
        self.assertIn('Sem avaliação', html)
        self.assertIn('02/01/2026', html)
        self.assertNotIn('COMENTARIO_INDIVIDUAL_SECRETO', html)
        self.assertNotIn('avaliacao_geral', html)
        sql = [s for s in self.statements if 'feedbacks_treinamento' in s]
        self.assertEqual(len(sql), 1)
        self.assertNotIn('comentario', sql[0])
        self.assertNotIn('avaliacao_geral', sql[0])

    def test_fixed_queries_and_no_identity_in_sql(self):
        counts = []
        for size in [5, 20]:
            for n in range(size): self.add(comentario='texto')
            self.statements.clear(); self.get()
            counts.append(len(self.statements))
            for sql in self.statements:
                self.assertNotIn('colaboradores', sql)
                self.assertNotIn('departamentos', sql)
            self.assertTrue(any('LIMIT' in s and 'OFFSET' in s for s in self.statements))
        self.assertEqual(counts, [6, 6])

    def test_login_required(self):
        self.app.config['LOGIN_DISABLED'] = False
        self.statements.clear()
        self.assertEqual(self.client.get('/treinamentos/feedback').status_code, 401)
        self.assertEqual(self.statements, [])

    def test_unknown_training_and_postgres_compilation(self):
        self.assertEqual(self.client.get('/treinamentos/feedback?treinamento_id=12345').status_code, 404)
        sql = str(consulta_metricas_feedback().compile(dialect=postgresql.dialect()))
        self.assertIn('CASE WHEN', sql)
        self.assertIn('nullif', sql)
        self.assertNotIn('colaboradores', sql)


class MetricasFeedbackPostgresTest(MetricasFeedbackTest):
    postgres = True
