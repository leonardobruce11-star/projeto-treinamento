"""Testes isolados: SQLite em memória, sem create_app(), .env ou produção.

Executar: python -m unittest discover -s tests -p test_tempo_ativo_exibicao.py -v
"""
from sqlalchemy.orm import selectinload
from app.utils.resultados_quiz import resultado_representativo, resultados_carregados

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import event
from jinja2 import Environment, FileSystemLoader, ChainableUndefined

import app
# Permite testar a proposta antes de aplicar: resolve primeiro o app do patch.
ROOT = Path(__file__).resolve().parents[1]
app.__path__.insert(0, str(ROOT / "app"))
import app.models
from app.extensions import db
from app.utils.progresso_treinamento import calcular_progresso
from app.utils.painel_colaboradores import contexto_status_capacitacao, status_participacao
from app.models.departamento import Departamento
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.models.campanha import Campanha
from app.utils.tempo import formatar_tempo_ativo
from app.utils.tempos_treinamento import totais_por_participacao, tempos_da_participacao


def load_route(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

portal = load_route('portal_display_test', 'app/portal_treinamento/routes.py')
colaboradores = load_route('colaboradores_display_test', 'app/colaboradores/routes.py')
campanhas = load_route('campanhas_display_test', 'app/campanhas/routes.py')
campanha_pdf = load_route('pdf_display_test', 'app/relatorios/campanha_pdf.py')


class FormatTest(unittest.TestCase):
    def test_formats_and_boundaries(self):
        for seconds, expected in [(None, '0 min'), (0, '0 min'), (1, '1 s'),
            (45, '45 s'), (59, '59 s'), (60, '1 min'), (750, '12 min 30 s'),
            (3599, '59 min 59 s'), (3600, '1 h 00 min'), (4080, '1 h 08 min'),
            (4089, '1 h 08 min'), (90000, '25 h 00 min')]:
            with self.subTest(seconds=seconds):
                self.assertEqual(formatar_tempo_ativo(seconds), expected)


class DisplayTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', LOGIN_DISABLED=True)
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        department = Departamento(nome='Testes')
        person = Colaborador(nome='Pessoa', email='teste@example.invalid', departamento=department)
        training = Treinamento(titulo='Treino', ativo=True)
        db.session.add_all([department, person, training])
        db.session.flush()
        self.modules = [ModuloTreinamento(treinamento_id=training.id, titulo='M1', conteudo='Texto'),
                        ModuloTreinamento(treinamento_id=training.id, titulo='M2', conteudo='Texto')]
        db.session.add_all(self.modules)
        db.session.flush()
        self.person = person
        self.participations = [ParticipacaoTreinamento(token=f'token-{i}', colaborador_id=person.id,
            treinamento_id=training.id, campanha_id=1, iniciado_em=datetime.now(timezone.utc))
            for i in range(3)]
        db.session.add_all(self.participations)
        db.session.flush()
        self.ids = [p.id for p in self.participations]
        self.mids = [m.id for m in self.modules]
        db.session.add_all([
            TempoTreinamento(participacao_id=self.ids[0], modulo_id=self.mids[0], segundos_ativos=45),
            TempoTreinamento(participacao_id=self.ids[0], modulo_id=self.mids[1], segundos_ativos=705),
            TempoTreinamento(participacao_id=self.ids[1], modulo_id=self.mids[0], segundos_ativos=4080),
        ])
        db.session.commit()
        self.statements = []
        self.listen = lambda conn, cursor, statement, parameters, context, many: self.statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', self.listen)

    def tearDown(self):
        event.remove(db.engine, 'before_cursor_execute', self.listen)
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def test_sql_sum_isolated_and_missing_zero_one_query(self):
        self.assertEqual(totais_por_participacao(self.ids + [999]),
            {self.ids[0]: 750, self.ids[1]: 4080, self.ids[2]: 0, 999: 0})
        self.assertEqual(len(self.statements), 1)
        self.assertIn('sum(', self.statements[0].lower())
        self.assertEqual(totais_por_participacao([]), {})
        self.assertEqual(len(self.statements), 1)

    def test_modules_and_total_without_writes(self):
        total, modules = tempos_da_participacao(self.ids[0])
        self.assertEqual(total, 750)
        self.assertEqual(modules, {self.mids[0]: 45, self.mids[1]: 705})
        self.assertEqual(len(self.statements), 2)
        self.assertEqual(tempos_da_participacao(self.ids[2]), (0, {}))
        self.assertTrue(all(s.lstrip().upper().startswith('SELECT') for s in self.statements))
        self.assertFalse(db.session.dirty)

    def test_campaign_loader_eager_relationships_no_row_queries(self):
        db.session.expire_all()
        rows = campanhas.carregar_participacoes_campanha(1)
        count = len(self.statements)
        for p in rows:
            _ = p.colaborador.departamento, resultado_representativo(resultados_carregados(p)), p.ultimo_modulo
        self.assertEqual(len(self.statements), count)
        self.assertLessEqual(count, 5)
        self.assertEqual(len(rows), 3)

    def test_campaign_detail_with_and_without_quiz_returns_and_preserves_metrics(self):
        campaign = SimpleNamespace(id=1)
        for with_quiz in (False, True):
            if with_quiz:
                db.session.add(ResultadoQuiz(participacao_id=self.ids[0], total_questoes=1,
                    total_acertos=1, percentual=100))
                db.session.commit()
                db.session.expire_all()
            with self.app.test_request_context(), patch.object(campanhas.db.session, 'get', return_value=campaign), \
                    patch.object(campanhas, 'render_template', side_effect=lambda name, **kw: kw):
                result = campanhas.detalhe(1)
            self.assertEqual(result['tempos_ativos'][self.ids[0]], 750)
            self.assertEqual(result['total_participantes'], 3)
            self.assertEqual(result['total_em_andamento'], 3)
            self.assertEqual(result['total_quizzes_campanha'], int(with_quiz))
            self.assertEqual(result['media_quiz_campanha'], 100 if with_quiz else 0)

    def test_report_route_receives_totals_and_keeps_indicators(self):
        campaign = SimpleNamespace(id=1)
        with self.app.test_request_context(), patch.object(campanhas.db.session, 'get', return_value=campaign), \
                patch.object(campanhas, 'render_template', side_effect=lambda name, **kw: kw):
            result = campanhas.relatorio(1)
        self.assertEqual(result['tempos_ativos'], dict(zip(self.ids, [750, 4080, 0])))
        self.assertEqual(result['indicadores']['participantes'], 3)
        self.assertEqual(result['indicadores']['em_andamento'], 3)
        self.assertEqual(result['indicadores']['quizzes'], 0)

    def test_bulk_sum_query_count_independent_of_participant_count(self):
        totals = totais_por_participacao(self.ids + list(range(100, 200)))
        self.assertEqual(len(self.statements), 1)
        self.assertEqual(len(totals), 103)
        self.assertEqual(totals[199], 0)

    def test_collaborator_history_receives_totals(self):
        with self.app.test_request_context(), patch.object(colaboradores.db.session, 'get', return_value=self.person), \
                patch.object(colaboradores, 'render_template', side_effect=lambda name, **kw: kw):
            result = colaboradores.detalhe(self.person.id)
        self.assertEqual(result['tempos_ativos'], dict(zip(self.ids, [750, 4080, 0])))
        self.assertEqual(result['total_em_andamento'], 3)

    def test_portal_only_valid_access_and_progress_unchanged(self):
        p = db.session.get(ParticipacaoTreinamento, self.ids[0])
        before = (p.iniciado_em, p.concluido_em, p.ultimo_modulo_id)
        with self.app.test_request_context(), patch.object(portal, 'obter_participacao', return_value=p), \
                patch.object(portal, 'render_template', side_effect=lambda name, **kw: kw):
            result = portal.modulo(p.token, self.mids[0])
        self.assertEqual(result['tempo_total_ativo'], 750)
        self.assertEqual(result['tempo_modulo_ativo'], 45)
        self.assertEqual(before, (p.iniciado_em, p.concluido_em, p.ultimo_modulo_id))
        self.assertFalse(db.session.dirty)
        with self.app.test_request_context(), patch.object(portal, 'obter_participacao', return_value=None), \
                patch.object(portal, 'tempos_da_participacao') as query, \
                patch.object(portal, 'render_template', return_value='indisponivel'):
            self.assertEqual(portal.modulo('bad', self.mids[0]), ('indisponivel', 404))
            query.assert_not_called()

    def test_html_template_columns_render_with_zero_fallback(self):
        env = Environment(loader=FileSystemLoader([str(ROOT / 'app/templates'),
            str(Path(app.__file__).parent / 'templates')]), undefined=ChainableUndefined, autoescape=True)
        env.filters['tempo_ativo'] = formatar_tempo_ativo
        env.globals.update(contexto_status_capacitacao())
        env.globals.update(url_for=lambda *a, **kw: '/', csrf_token=lambda: 'csrf',
            get_flashed_messages=lambda **kw: [], current_user=SimpleNamespace(is_authenticated=False))
        self.participations=db.session.execute(db.select(ParticipacaoTreinamento).options(selectinload(ParticipacaoTreinamento.resultados_quiz)).order_by(ParticipacaoTreinamento.id)).scalars().all()
        campaign = SimpleNamespace(id=1, nome='Campanha', status='piloto_pronto',
            participacoes_treinamento=self.participations)
        context = dict(campanha=campaign, participacoes=self.participations,
            estados_participacoes={p.id: status_participacao(p, resultado_representativo(resultados_carregados(p))) for p in self.participations},
            tempos_ativos=totais_por_participacao(self.ids), indicadores={'participantes': 3, 'quizzes': 0}, colaborador=self.person,
            grupos=[], total_participacoes=3, total_nao_iniciados=0, total_em_andamento=3,
            total_concluidos=0, total_participantes=3, total_quizzes_campanha=0,
            media_quiz_campanha=0, taxa_conclusao_campanha=0, media_quiz=None)
        for name in ['colaboradores/detalhe.html', 'campanhas/detalhe.html', 'campanhas/relatorio.html']:
            with self.subTest(template=name):
                html = env.get_template(name).render(**context)
                self.assertIn('Tempo ativo', html)
                self.assertIn('12 min 30 s', html)
                self.assertIn('1 h 08 min', html)
                self.assertIn('0 min', html)
        html = env.get_template('portal_treinamento/modulo.html').render(
            treinamento=self.participations[0].treinamento, modulo=self.modules[0],
            participacao=self.participations[0], proximo_modulo=self.modules[1],
            tempo_total_ativo=750, tempo_modulo_ativo=45, numero_modulo=1, total_modulos=2, progresso=0,
            estado_progresso=calcular_progresso(self.modules[:2], None))
        self.assertIn('Neste módulo: 45 s', html)
        self.assertIn('12 min 30 s', html)
        self.assertIn('data-one-heartbeat-url', html)
        self.assertIn('type="submit"', html)

    def test_pdf_generates_with_times_and_empty_participants(self):
        # Mesmo contrato da rota de exportação: relações carregadas em lote.
        self.participations = campanhas.carregar_participacoes_campanha(1)
        campaign = Campanha(nome='Campanha', status='piloto_pronto')
        from reportlab.platypus import Table
        for participants in (self.participations, []):
            with patch.object(campanha_pdf, 'Table', wraps=Table) as tables:
                pdf = campanha_pdf.gerar_relatorio_campanha_pdf(campaign, participants, {}, [],
                    tempos_ativos=totais_por_participacao(self.ids))
            self.assertTrue(pdf.getvalue().startswith(b'%PDF-'))
            data = next(call.args[0] for call in tables.call_args_list if 'Tempo ativo' in call.args[0][0])
            self.assertTrue(all(len(row) == 5 for row in data))
            self.assertIn('0 min', str(data))
            if participants:
                self.assertIn('12 min 30 s', str(data))
                self.assertIn('1 h 08 min', str(data))


if __name__ == '__main__':
    unittest.main()
