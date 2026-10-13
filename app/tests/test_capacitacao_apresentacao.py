"""SQL real em SQLite em memória, sem create_app(), .env ou banco configurado.

Executar: python -B -m unittest discover -s tests -p test_capacitacao_apresentacao.py -v
"""
from app.utils.resultados_quiz import resultado_representativo, resultados_carregados

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager
from jinja2 import ChoiceLoader, FileSystemLoader, StrictUndefined
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

import app
import app.models
from app.extensions import db
from app.models.categoria_treinamento import CategoriaTreinamento
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.treinamento import Treinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.utils.tempo import formatar_tempo_ativo

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


metricas = load('app.utils.metricas_capacitacao', 'app/utils/metricas_capacitacao.py')
routes = load('dashboard_capacitacao_under_test', 'app/dashboard/routes.py')
colaboradores = load('colaboradores_capacitacao_under_test', 'app/colaboradores/routes.py')


class MetricasCapacitacaoTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='isolated-test',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', LOGIN_DISABLED=True)
        db.init_app(self.app)
        login = LoginManager(self.app)
        login.user_loader(lambda user_id: None)
        self.app.register_blueprint(routes.dashboard_bp)
        self.app.register_blueprint(colaboradores.colaboradores_bp)
        self.app.jinja_env.globals['csrf_token'] = lambda: 'test-csrf'
        self.app.jinja_loader = ChoiceLoader([
            FileSystemLoader(str(ROOT / 'app/templates')),
            FileSystemLoader(str(Path(app.__file__).parent / 'templates')),
        ])
        self.app.jinja_env.undefined = StrictUndefined
        self.app.add_template_filter(formatar_tempo_ativo, 'tempo_ativo')
        self.context = self.app.app_context()
        self.context.push()
        db.session.execute(db.text('PRAGMA foreign_keys = ON'))
        db.create_all()
        self.statements = []
        self.listen = lambda conn, cursor, statement, params, context, many: self.statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', self.listen)

    def tearDown(self):
        event.remove(db.engine, 'before_cursor_execute', self.listen)
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def seed(self, with_times=True):
        self.categories = [CategoriaTreinamento(nome='Segurança'),
                           CategoriaTreinamento(nome='Sem atividade'),
                           CategoriaTreinamento(nome='Categoria vazia'),
                           CategoriaTreinamento(nome='Sem categoria')]
        self.departments = [Departamento(nome='A'), Departamento(nome='B')]
        db.session.add_all(self.categories + self.departments)
        db.session.flush()
        self.people = [Colaborador(nome='Pessoa', email=f'{i}@example.invalid',
            departamento_id=self.departments[0].id if i == 0 else self.departments[1].id)
            for i in range(3)]
        self.trainings = [Treinamento(titulo='T1', categoria_id=self.categories[0].id),
                          Treinamento(titulo='T2', categoria_id=self.categories[0].id),
                          Treinamento(titulo='T3 sem categoria', categoria_id=None, ativo=False),
                          Treinamento(titulo='T4 zerado', categoria_id=self.categories[1].id),
                          Treinamento(titulo='T5 sem participação', categoria_id=self.categories[1].id)]
        db.session.add_all(self.people + self.trainings)
        db.session.flush()
        self.modules = [ModuloTreinamento(treinamento_id=t.id, titulo='M', conteudo='Texto')
                        for t in self.trainings]
        extra = ModuloTreinamento(treinamento_id=self.trainings[0].id, titulo='M2', conteudo='Texto')
        db.session.add_all(self.modules + [extra])
        db.session.flush()
        self.participations = [ParticipacaoTreinamento(token=f'token-{i}',
            colaborador_id=self.people[person].id, treinamento_id=self.trainings[training].id)
            for i, (person, training) in enumerate([(0, 0), (1, 1), (0, 2), (2, 3), (2, 4)])]
        # Datas deliberadamente discrepantes: não podem influenciar os tempos.
        self.participations[1].iniciado_em = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.participations[1].concluido_em = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.participations[4].iniciado_em = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.session.add_all(self.participations)
        db.session.flush()
        if with_times:
            db.session.add_all([TempoTreinamento(participacao_id=self.participations[p].id,
                modulo_id=module.id, segundos_ativos=seconds) for p, module, seconds in [
                    (0, self.modules[0], 45), (0, extra, 705),
                    (1, self.modules[1], 2250), (2, self.modules[2], 3600),
                    (3, self.modules[3], 0)]])
        db.session.commit()
        self.statements.clear()

    @staticmethod
    def by_id(rows):
        return {row['id']: row['segundos'] for row in rows}

    def test_empty_database_four_queries_and_zero_summary(self):
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result, {'total_segundos': 0, 'media_segundos': 0,
            'participacoes_com_tempo': 0, 'colaboradores_com_atividade': 0, 'por_treinamento': [], 'por_categoria': [], 'por_colaborador': []})
        self.assertEqual(len(self.statements), 4)

    def test_positive_participations_average_sums_modules_before_averaging(self):
        self.seed()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['total_segundos'], 6600)
        self.assertEqual(result['participacoes_com_tempo'], 3)
        self.assertEqual(result['media_segundos'], 2200)
        self.assertEqual(formatar_tempo_ativo(result['media_segundos']), '36 min 40 s')
        self.assertEqual(len(self.statements), 4)

    def test_training_category_and_collaborator_totals_reconcile(self):
        self.seed()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(self.by_id(result['por_treinamento']), dict(zip(
            [t.id for t in self.trainings], [750, 2250, 3600, 0, 0])))
        self.assertEqual(self.by_id(result['por_categoria']), dict(zip(
            [c.id for c in self.categories] + [None], [3000, 0, 0, 0, 3600])))
        # Homônimos permanecem separados por ID.
        self.assertEqual(self.by_id(result['por_colaborador']), dict(zip(
            [p.id for p in self.people], [4350, 2250, 0])))
        for key in ['por_treinamento', 'por_categoria', 'por_colaborador']:
            self.assertEqual(sum(row['segundos'] for row in result[key]), 6600)
            self.assertEqual([r['segundos'] for r in result[key]],
                             sorted([r['segundos'] for r in result[key]], reverse=True))

    def test_records_without_time_preserve_zero_rows(self):
        self.seed(with_times=False)
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['participacoes_com_tempo'], 0)
        self.assertEqual(result['total_segundos'], 0)
        self.assertEqual(result['media_segundos'], 0)
        self.assertEqual(len(result['por_treinamento']), 5)
        self.assertEqual(len(result['por_categoria']), 5)
        self.assertEqual(len(result['por_colaborador']), 3)
        self.assertTrue(all(row['segundos'] == 0 for key in ['por_treinamento',
            'por_categoria', 'por_colaborador'] for row in result[key]))

    def test_first_heartbeat_zero_does_not_count_as_activity(self):
        self.seed(with_times=False)
        db.session.add(TempoTreinamento(participacao_id=self.participations[0].id,
            modulo_id=self.modules[0].id, segundos_ativos=0))
        db.session.commit()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['participacoes_com_tempo'], 0)
        self.assertEqual(result['media_segundos'], 0)

    def test_fractional_average_preserved_in_data_format_truncates_fraction(self):
        self.seed(with_times=False)
        db.session.add_all([TempoTreinamento(participacao_id=self.participations[i].id,
            modulo_id=self.modules[i].id, segundos_ativos=i + 1) for i in range(2)])
        db.session.commit()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['media_segundos'], 1.5)
        self.assertEqual(formatar_tempo_ativo(result['media_segundos']), '1 s')

    def test_no_uncategorized_bucket_without_uncategorized_trainings(self):
        category = CategoriaTreinamento(nome='Vazia')
        db.session.add(category)
        db.session.commit()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['por_categoria'], [{'id': category.id, 'nome': 'Vazia', 'segundos': 0}])

    def test_historical_inactive_records_are_included(self):
        self.seed()
        self.people[0].ativo = False
        self.categories[0].ativo = False
        self.trainings[0].ativo = False
        db.session.commit()
        self.assertEqual(metricas.calcular_metricas_capacitacao()['total_segundos'], 6600)

    def test_only_selects_and_no_dates_or_lazy_reads(self):
        self.seed()
        db.session.expire_all()
        self.statements.clear()
        result = metricas.calcular_metricas_capacitacao()
        count = len(self.statements)
        for key in ['por_treinamento', 'por_categoria', 'por_colaborador']:
            for row in result[key]:
                _ = row['nome'], formatar_tempo_ativo(row['segundos'])
        self.assertEqual(len(self.statements), count)
        self.assertEqual(count, 4)
        self.assertTrue(all(s.lstrip().upper().startswith('SELECT') for s in self.statements))
        self.assertTrue(all('iniciado_em' not in s and 'concluido_em' not in s for s in self.statements))
        self.assertFalse(db.session.dirty)

    def test_queries_compile_for_postgresql(self):
        with patch.object(db.session, 'execute', wraps=db.session.execute) as execute:
            metricas.calcular_metricas_capacitacao()
        self.assertEqual(len(execute.call_args_list), 4)
        for call in execute.call_args_list:
            self.assertIn('SELECT', str(call.args[0].compile(dialect=postgresql.dialect())))

    def test_query_count_constant_with_many_participations(self):
        self.seed()
        db.session.add_all([ParticipacaoTreinamento(token=f'extra-{i}',
            colaborador_id=self.people[0].id, treinamento_id=self.trainings[0].id)
            for i in range(100)])
        db.session.flush()
        self.statements.clear()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(len(self.statements), 4)
        self.assertEqual(result['total_segundos'], 6600)
        self.assertEqual(result['participacoes_com_tempo'], 3)

    def test_dashboard_empty_renders_with_existing_and_new_sections(self):
        response = self.app.test_client().get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for text in ['Capacitação e tempo de treinamento', 'Horas totais de capacitação', 'Tempo médio por participação ativa',
            'Participações com atividade registrada', 'Tempo total por treinamento', 'Tempo total por categoria',
            'Colaboradores com atividade de treinamento', '0 min', 'Nenhum treinamento cadastrado',
            'Visão do ambiente', 'Acompanhamento das campanhas', 'Resultados recentes']:
            self.assertIn(text, html)

    def test_dashboard_with_data_keeps_global_count_and_all_departments(self):
        self.seed()
        with self.app.test_request_context(), patch.object(routes, 'render_template',
                side_effect=lambda template, **context: context):
            context = routes.index()
        self.assertEqual(context['total_colaboradores'], 3)
        self.assertEqual(context['total_participacoes'], 5)
        self.assertEqual(context['total_concluidos'], 1)
        self.assertEqual(context['total_em_andamento'], 1)
        self.assertEqual(len(context['indicadores_departamentos']), 2)
        self.assertEqual(context['metricas_capacitacao']['total_segundos'], 6600)
        response = self.app.test_client().get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for text in ['1 h 50 min', '36 min 40 s', 'Sem categoria', 'T4 zerado', '0 min']:
            self.assertIn(text, html)

    def test_empty_departments_still_return_dashboard(self):
        db.session.add_all([Departamento(nome='Vazio A'), Departamento(nome='Vazio B')])
        db.session.commit()
        self.assertEqual(self.app.test_client().get('/dashboard/').status_code, 200)

    def test_dashboard_still_requires_login(self):
        self.app.config['LOGIN_DISABLED'] = False
        self.assertEqual(self.app.test_client().get('/dashboard/').status_code, 401)


    def profile_context(self, person_id):
        with self.app.test_request_context(), patch.object(colaboradores, 'render_template',
                side_effect=lambda template, **context: context):
            return colaboradores.detalhe(person_id)

    def test_distinct_people_with_positive_activity_without_extra_query(self):
        self.seed()
        result = metricas.calcular_metricas_capacitacao()
        self.assertEqual(result['colaboradores_com_atividade'], 2)
        self.assertEqual(result['participacoes_com_tempo'], 3)
        self.assertEqual(len(self.statements), 4)
        # Same person's second activity does not increment collaborator count.
        db.session.add(TempoTreinamento(participacao_id=self.participations[4].id,
            modulo_id=self.modules[4].id, segundos_ativos=1))
        db.session.commit()
        self.statements.clear()
        self.assertEqual(metricas.calcular_metricas_capacitacao()['colaboradores_com_atividade'], 3)
        self.assertEqual(len(self.statements), 4)

    def test_dashboard_section_is_between_departments_and_recent_results(self):
        html = self.app.test_client().get('/dashboard/').get_data(as_text=True)
        departamentos = html.index('Indicadores por departamento')
        capacitacao = html.index('Capacitação e tempo de treinamento')
        recentes = html.index('Resultados recentes')
        self.assertLess(departamentos, capacitacao)
        self.assertLess(capacitacao, recentes)
        self.assertEqual(html.count('id="capacitacao-titulo"'), 1)
        self.assertIn('environment-grid', html)

    def test_profile_accumulation_started_completed_category_quiz_single_history(self):
        self.seed()
        for index in (0, 2):
            self.participations[index].iniciado_em = datetime.now(timezone.utc)
        self.participations[2].concluido_em = datetime.now(timezone.utc)
        db.session.add(ResultadoQuiz(participacao_id=self.participations[0].id,
            total_questoes=4, total_acertos=3, percentual=75))
        db.session.commit()
        person_id = self.people[0].id
        context = self.profile_context(person_id)
        self.assertEqual(context['tempo_total_capacitacao'], 4350)
        self.assertEqual(context['total_iniciados'], 2)
        self.assertEqual(context['total_concluidos'], 1)
        self.assertEqual(len(context['tempos_ativos']), 2)
        response = self.app.test_client().get(f'/colaboradores/{person_id}')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for text in ['Histórico de capacitação', 'Tempo total acumulado', '1 h 12 min',
            'Treinamentos iniciados', 'Treinamentos concluídos', 'Categoria', 'Segurança',
            'Sem categoria', 'Em andamento', 'Concluído', '12 min 30 s', '75.0%',
            'Campanha', 'Progresso', 'Conclusão']:
            self.assertIn(text, html)
        self.assertEqual(html.count('collaborator-history-table'), 1)
        self.assertEqual(html.count('Histórico de capacitação'), 1)

    def test_profile_without_participations_renders_zero_summary(self):
        department = Departamento(nome='D')
        person = Colaborador(nome='Sem histórico', email='empty@example.invalid', departamento=department)
        db.session.add_all([department, person])
        db.session.commit()
        person_id = person.id
        context = self.profile_context(person_id)
        self.assertEqual(context['tempo_total_capacitacao'], 0)
        self.assertEqual(context['total_iniciados'], 0)
        self.assertEqual(context['total_concluidos'], 0)
        self.assertEqual(context['tempos_ativos'], {})
        html = self.app.test_client().get(f'/colaboradores/{person_id}').get_data(as_text=True)
        self.assertIn('0 min', html)
        self.assertIn('Nenhuma participação registrada', html)
        self.assertIn('Treinamentos iniciados', html)

    def test_profile_without_time_and_other_people_isolated(self):
        self.seed()
        context = self.profile_context(self.people[2].id)
        self.assertEqual(context['tempo_total_capacitacao'], 0)
        self.assertEqual(context['total_iniciados'], 1)
        self.assertEqual(set(context['tempos_ativos'].values()), {0})
        html = self.app.test_client().get(f'/colaboradores/{self.people[2].id}').get_data(as_text=True)
        self.assertIn('0 min', html)
        self.assertIn('Não iniciado', html)

    def test_profile_eager_category_and_no_query_per_participation(self):
        self.seed()
        person_id = self.people[0].id
        db.session.expire_all()
        self.statements.clear()
        context = self.profile_context(person_id)
        baseline = len(self.statements)
        for p in context['participacoes']:
            _ = p.treinamento.categoria, p.ultimo_modulo, p.campanha, resultado_representativo(resultados_carregados(p))
        self.assertEqual(len(self.statements), baseline)
        db.session.add_all([ParticipacaoTreinamento(token=f'profile-extra-{i}',
            colaborador_id=person_id, treinamento_id=self.trainings[0].id) for i in range(50)])
        db.session.commit()
        db.session.expire_all()
        self.statements.clear()
        context = self.profile_context(person_id)
        self.assertEqual(len(context['participacoes']), 52)
        self.assertEqual(len(self.statements), baseline)
        for p in context['participacoes']:
            _ = p.treinamento.categoria, p.ultimo_modulo, p.campanha, resultado_representativo(resultados_carregados(p))
        self.assertEqual(len(self.statements), baseline)
        self.assertEqual(context['tempo_total_capacitacao'], 4350)

    def test_profile_get_does_not_change_progress_or_write(self):
        self.seed()
        person_id = self.people[0].id
        self.statements.clear()
        self.profile_context(person_id)
        self.assertFalse(db.session.dirty)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements))

    def test_profile_preserves_login_and_404(self):
        self.assertEqual(self.app.test_client().get('/colaboradores/999').status_code, 404)
        self.app.config['LOGIN_DISABLED'] = False
        self.assertEqual(self.app.test_client().get('/colaboradores/999').status_code, 401)


if __name__ == '__main__':
    unittest.main()
