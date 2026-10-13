"""Camada de dados isolada; sem create_app(), .env ou banco da aplicação."""
import os
import unittest
from datetime import datetime
from uuid import uuid4
from flask import Flask
from sqlalchemy import create_engine, event, insert
from types import SimpleNamespace
from sqlalchemy.schema import CreateSchema, DropSchema
import app.models
from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento
from app.models.treinamento import Treinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.utils.painel_colaboradores import (
    consulta_painel_colaboradores, calcular_indicadores_gerais, consulta_quiz_mais_recente,
)
from app.utils.metricas_capacitacao import calcular_metricas_capacitacao
from app.utils.tempos_treinamento import totais_por_participacao, tempos_da_participacao


class PainelTest(unittest.TestCase):
    postgres = False

    def setUp(self):
        self.app = Flask(__name__)
        url = 'sqlite:///:memory:'
        options = {}
        if self.postgres:
            url = os.environ.get('ONE_PROGRESSO_TEST_DATABASE_URL')
            if not url:
                self.skipTest('PostgreSQL de testes não configurado')
            self.admin = create_engine(url)
            self.schema = 'painel_test_' + uuid4().hex
            with self.admin.begin() as conn:
                conn.execute(CreateSchema(self.schema))
            options = {'connect_args': {'options': '-csearch_path=' + self.schema}}
        self.app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_ENGINE_OPTIONS=options)
        db.init_app(self.app)
        self.context = self.app.app_context(); self.context.push()
        db.create_all()
        self.statements = []
        self.listener = lambda *args: self.statements.append(args[2])
        event.listen(db.engine, 'before_cursor_execute', self.listener)

    def tearDown(self):
        if not hasattr(self, 'context'):
            return
        event.remove(db.engine, 'before_cursor_execute', self.listener)
        db.session.remove(); db.engine.dispose(); self.context.pop()
        if self.postgres:
            with self.admin.begin() as conn:
                conn.execute(DropSchema(self.schema, cascade=True))
            self.admin.dispose()

    def seed(self, treinamento_historico=False):
        d = Departamento(nome='Departamento')
        if treinamento_historico:
            # Revisões anteriores à 19.1A não possuem a coluna opcional nova.
            # INSERT Core especifica somente campos presentes naquela revisão.
            db.session.add(d); db.session.flush()
            tid = db.session.execute(insert(Treinamento.__table__).values(
                titulo='Mesmo treinamento', ativo=True, criado_em=datetime(2026,1,1),
            ).returning(Treinamento.id)).scalar_one()
            t = SimpleNamespace(id=tid)
        else:
            t = Treinamento(titulo='Mesmo treinamento')
            db.session.add_all([d,t]); db.session.flush()
        self.people = [Colaborador(nome=str(i), email=f'{i}@example.invalid',
                        departamento_id=d.id, ativo=i != 5) for i in range(6)]
        self.mods = [ModuloTreinamento(treinamento_id=t.id, titulo=str(i), conteudo='x', ordem=i)
                     for i in range(2)]
        db.session.add_all(self.people+self.mods); db.session.flush()
        # Pessoa 0 sem atribuição; 1 não iniciada; 2 iniciada; 3 concluída;
        # 4 com três atribuições do mesmo treinamento; 5 legado inativo.
        start = datetime(2026,1,1); end = datetime(2026,1,2)
        dados_ps = [dict(token=str(i), colaborador_id=self.people[c].id,
            treinamento_id=t.id, iniciado_em=start if i in [1,2,3,4] else None,
            concluido_em=end if i in [2,3,6] else None)
            for i,c in enumerate([1,2,3,4,4,4,5])]
        def historico(modelo, valores):
            identificador = db.session.execute(insert(modelo.__table__).values(
                **valores).returning(modelo.id)).scalar_one()
            return SimpleNamespace(id=identificador, **valores)
        if treinamento_historico:
            self.ps = [historico(ParticipacaoTreinamento, v) for v in dados_ps]
        else:
            self.ps = [ParticipacaoTreinamento(**v) for v in dados_ps]
            db.session.add_all(self.ps); db.session.flush()
        db.session.add_all([TempoTreinamento(participacao_id=self.ps[p].id,
            modulo_id=self.mods[m].id, segundos_ativos=s, ultima_atividade_em=datetime(2026,1,day))
            for p,m,s,day in [(1,0,0,3),(2,0,60,3),(2,1,120,4),(3,0,300,3),(4,0,120,3)]])
        dados_qs = [dict(participacao_id=self.ps[p].id, total_questoes=10,
            total_acertos=score//10, percentual=score, concluido_em=datetime(2026,1,day))
            for p,score,day in [(3,90,5),(4,40,6),(5,20,6)]]
        if treinamento_historico:
            self.qs = [historico(ResultadoQuiz, v) for v in dados_qs]
        else:
            self.qs = [ResultadoQuiz(**v) for v in dados_qs]
            db.session.add_all(self.qs)
        db.session.commit()
        self.ids=[c.id for c in self.people]; self.pids=[p.id for p in self.ps]
        self.qids=[q.id for q in self.qs]; self.statements.clear()

    def rows(self):
        return {r['colaborador_id']:r for r in db.session.execute(consulta_painel_colaboradores()).mappings()}

    def test_empty(self):
        self.assertEqual(self.rows(), {})
        self.assertEqual(list(db.session.execute(consulta_quiz_mais_recente())), [])
        resumo = calcular_indicadores_gerais()
        self.assertIsNone(resumo['taxa_conclusao'])
        self.assertIsNone(resumo['taxa_aprovacao'])
        self.assertTrue(all(v == 0 for k,v in resumo.items() if not k.startswith('taxa_')))

    def test_participation_states_and_legacy(self):
        self.seed(); rows=self.rows()
        expected=[(0,0,0,0,0,None),(1,0,0,0,1,0),(1,1,0,1,1,0),
                  (1,1,1,0,0,100),(3,2,1,1,2,100/3),(1,0,1,0,0,100)]
        for cid,values in zip(self.ids,expected):
            row=rows[cid]
            for key,value in zip(['atribuidas','iniciadas','concluidas','em_andamento','pendentes','percentual_conclusao'],values):
                if value is None: self.assertIsNone(row[key])
                else: self.assertAlmostEqual(row[key],value)
        self.assertFalse(rows[self.ids[5]]['ativo'])
        self.assertEqual(rows[self.ids[5]]['departamento'],'Departamento')
        self.assertEqual(len(rows),6)

    def test_time_modules_zero_and_general_average(self):
        self.seed(); rows=self.rows()
        self.assertEqual([rows[c]['total_segundos'] for c in self.ids],[0,0,0,180,420,0])
        summary=calcular_indicadores_gerais()
        self.assertEqual({k:summary[k] for k in ('total_colaboradores','com_treinamento_atribuido','com_treinamento_em_andamento','com_treinamentos_concluidos','sem_atividade','com_atividade','total_segundos','media_segundos_por_colaborador_com_atividade')}, dict(total_colaboradores=6, com_treinamento_atribuido=5,
            com_treinamento_em_andamento=2, com_treinamentos_concluidos=3,
            sem_atividade=4, com_atividade=2, total_segundos=600,
            media_segundos_por_colaborador_com_atividade=300))

    def test_latest_activity_all_sources(self):
        self.seed(); rows=self.rows()
        self.assertIsNone(rows[self.ids[0]]['ultima_atividade_em'])
        self.assertEqual(rows[self.ids[2]]['ultima_atividade_em'].day,3) # zero heartbeat
        self.assertEqual(rows[self.ids[3]]['ultima_atividade_em'].day,4) # tempo
        self.assertEqual(rows[self.ids[4]]['ultima_atividade_em'].day,6) # quiz
        self.assertEqual(rows[self.ids[5]]['ultima_atividade_em'].day,2) # conclusão legado
        self.ps[0].iniciado_em=datetime(2026,2,1); db.session.commit()
        self.assertEqual(self.rows()[self.ids[1]]['ultima_atividade_em'].month,2)
        self.ps[2].concluido_em=datetime(2026,3,1); db.session.commit()
        self.assertEqual(self.rows()[self.ids[3]]['ultima_atividade_em'].month,3)

    def test_quiz_date_then_id_not_best_score(self):
        self.seed()
        rows=list(db.session.execute(consulta_quiz_mais_recente()).mappings())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['resultado_quiz_id'],self.qids[2])
        self.assertEqual(rows[0]['percentual'],20)
        self.assertEqual(list(db.session.execute(consulta_quiz_mais_recente([]))),[])
        self.assertEqual(list(db.session.execute(consulta_quiz_mais_recente([self.ids[0]]))),[])
        self.qs[0].concluido_em=datetime(2026,2,1); db.session.commit()
        row=db.session.execute(consulta_quiz_mais_recente([self.ids[4]])).mappings().one()
        self.assertEqual(row['resultado_quiz_id'],self.qids[0])

    def test_existing_metrics_and_time_contract(self):
        self.seed(); old=calcular_metricas_capacitacao()
        self.assertEqual(old['total_segundos'],600)
        self.assertEqual(old['media_segundos'],200) # 3 participações positivas
        self.assertEqual(old['participacoes_com_tempo'],3)
        self.assertEqual(old['colaboradores_com_atividade'],2)
        self.assertEqual(totais_por_participacao(self.pids),dict(zip(self.pids,[0,0,180,300,120,0,0])))
        self.assertEqual(totais_por_participacao([]),{})
        self.assertEqual(tempos_da_participacao(self.pids[2]),(180,{self.mods[0].id:60,self.mods[1].id:120}))

    def test_composable_subset_and_fixed_query_count(self):
        self.seed(); self.statements.clear()
        rows=self.rows()
        self.assertEqual(len(self.statements),1)
        filtered=consulta_painel_colaboradores().where(Colaborador.ativo.is_(False))
        summary=calcular_indicadores_gerais(filtered)
        self.assertEqual(summary['total_colaboradores'],1)
        self.assertEqual(summary['com_treinamentos_concluidos'],1)
        list(db.session.execute(consulta_quiz_mais_recente(self.ids)).mappings())
        self.assertEqual(len(self.statements),3)


class PainelPostgresTest(PainelTest):
    postgres=True
