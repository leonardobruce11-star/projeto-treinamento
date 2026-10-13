"""Filtros reais, cartões e navegação com SQLite/PostgreSQL isolados."""
import unittest
from datetime import datetime
from urllib.parse import urlencode, urlsplit, parse_qs
from html import unescape
import re
import test_painel_colaboradores_pagina as pagina
from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento
from app.models.treinamento import Treinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.resultado_quiz import ResultadoQuiz


class FiltrosTest(unittest.TestCase):
    postgres=False
    tearDown=pagina.PainelPaginaTest.tearDown
    seed=pagina.PainelPaginaTest.seed
    get=pagina.PainelPaginaTest.get

    def setUp(self):
        pagina.PainelPaginaTest.setUp(self)
        self.seed()
        self.dep=self.people[0].departamento_id
        self.tid=self.ps[0].treinamento_id
        d=Departamento(nome='Financeiro',ativo=False)
        db.session.add(d);db.session.flush();self.dep2=d.id
        self.people[3].departamento_id=d.id
        self.people[0].nome='Ana Silva'
        self.people[1].email='Nome.Corporativo@example.invalid'
        c=Colaborador(nome='Parcial',email='parcial@example.invalid',departamento_id=self.dep)
        db.session.add(c);db.session.flush();self.partial=c.id
        db.session.add_all([ParticipacaoTreinamento(token='parcial-finalizado',colaborador_id=c.id,
            treinamento_id=self.tid,concluido_em=datetime(2026,1,2)),
            ParticipacaoTreinamento(token='parcial-pendente',colaborador_id=c.id,treinamento_id=self.tid)])
        db.session.commit();self.statements.clear()

    def filtered(self,**params):
        return self.get('/colaboradores/painel?'+urlencode(params))

    def assert_ids(self,ctx,ids):
        self.assertEqual({c['colaborador_id'] for c in ctx['colaboradores']},set(ids))
        self.assertEqual(ctx['indicadores']['total_colaboradores'],len(ids))

    def test_department_including_inactive_option(self):
        html,ctx=self.filtered(departamento=self.dep2)
        self.assert_ids(ctx,[self.ids[3]])
        self.assertIn('Departamento: Financeiro',html)
        self.assertEqual(ctx['indicadores']['total_segundos'],180)
        self.assertIn('Financeiro',html)

    def test_status_exclusive_and_legacy(self):
        expected={'sem_atribuicao':[self.ids[0]],'nao_iniciado':[self.ids[1]],
            'em_andamento':[self.ids[2],self.ids[4]],'parcialmente_concluido':[self.partial],
            'concluido':[self.ids[3],self.ids[5]]}
        seen=[]
        for status,ids in expected.items():
            _,ctx=self.filtered(status=status);self.assert_ids(ctx,ids);seen.extend(ids)
        self.assertEqual(len(seen),len(set(seen)))
        self.assertEqual(len(seen),7)

    def test_activity_positive_and_zero_heartbeat(self):
        _,ctx=self.filtered(atividade='com_tempo');self.assert_ids(ctx,[self.ids[3],self.ids[4]])
        self.assertEqual(ctx['indicadores']['sem_atividade'],0)
        _,ctx=self.filtered(atividade='sem_tempo')
        self.assert_ids(ctx,[self.ids[0],self.ids[1],self.ids[2],self.ids[5],self.partial])
        self.assertEqual(ctx['indicadores']['total_segundos'],0)
        self.assertEqual(ctx['indicadores']['sem_atividade'],5)

    def test_active_inactive(self):
        _,ctx=self.filtered(situacao='inativos');self.assert_ids(ctx,[self.ids[5]])
        _,ctx=self.filtered(situacao='ativos')
        self.assertEqual(ctx['indicadores']['total_colaboradores'],6)
        self.assertTrue(all(c['ativo'] for c in ctx['colaboradores']))

    def test_search_name_spaces_and_case(self):
        html,ctx=self.filtered(busca='  aNA   SILVA  ');self.assert_ids(ctx,[self.ids[0]])
        self.assertEqual(ctx['filtros']['busca'],'aNA SILVA')
        self.assertIn('Busca: aNA SILVA',html)

    def test_search_email(self):
        _,ctx=self.filtered(busca='CORPORATIVO@EXAMPLE');self.assert_ids(ctx,[self.ids[1]])

    def test_search_empty_result_and_whitespace(self):
        html,ctx=self.filtered(busca='inexistente')
        self.assert_ids(ctx,[]);self.assertEqual(ctx['indicadores']['total_segundos'],0)
        self.assertIn('Nenhum colaborador encontrado para os filtros selecionados',html)
        _,ctx=self.filtered(busca='   \t ');self.assertEqual(ctx['indicadores']['total_colaboradores'],7)

    def test_literal_wildcards_and_safe_markup(self):
        for value in ['%','_',"' OR 1=1 --",'<script>alert(1)</script>']:
            html,ctx=self.filtered(busca=value);self.assert_ids(ctx,[])
            self.assertNotIn('<script>alert(1)</script>',html)
        self.people[0].nome='Ana_100%!';db.session.commit()
        _,ctx=self.filtered(busca='_100%!');self.assert_ids(ctx,[self.ids[0]])

    def test_combination_and_cards(self):
        html,ctx=self.filtered(departamento=self.dep,status='em_andamento',atividade='com_tempo',
                               situacao='ativos',treinamento=self.tid,busca='4')
        self.assert_ids(ctx,[self.ids[4]])
        self.assertEqual(ctx['indicadores']['total_segundos'],420)
        self.assertEqual(ctx['indicadores']['media_segundos_por_colaborador_com_atividade'],420)
        self.assertEqual(ctx['indicadores']['com_treinamentos_concluidos'],1)
        self.assertIn('Treinamento: Mesmo treinamento',html)

    def add_other_training(self):
        t=Treinamento(titulo='Outro treinamento',ativo=False)
        db.session.add(t);db.session.flush()
        m=ModuloTreinamento(treinamento_id=t.id,titulo='M',conteudo='x')
        db.session.add(m);db.session.flush()
        for index,cid in enumerate([self.ids[0],self.ids[4]]):
            p=ParticipacaoTreinamento(token='outro'+str(index),colaborador_id=cid,treinamento_id=t.id,
                iniciado_em=datetime(2026,2,1),concluido_em=datetime(2026,2,2))
            db.session.add(p);db.session.flush()
            db.session.add(TempoTreinamento(participacao_id=p.id,modulo_id=m.id,segundos_ativos=1000,
                ultima_atividade_em=datetime(2026,2,3)))
            db.session.add(ResultadoQuiz(participacao_id=p.id,total_questoes=1,total_acertos=1,
                percentual=100,concluido_em=datetime(2026,2,4)))
        db.session.commit();return t.id

    def test_training_scope_repeat_time_activity_quiz(self):
        other=self.add_other_training()
        html,ctx=self.filtered(treinamento=self.tid)
        self.assertNotIn(self.ids[0],[c['colaborador_id'] for c in ctx['colaboradores']])
        row=next(c for c in ctx['colaboradores'] if c['colaborador_id']==self.ids[4])
        self.assertEqual(row['atribuidas'],3);self.assertEqual(row['total_segundos'],420)
        self.assertEqual(row['ultima_atividade_em'].month,1)
        self.assertEqual(ctx['quizzes'][self.ids[4]]['percentual'],20)
        self.assertEqual(ctx['indicadores']['total_segundos'],600)
        self.assertIn('somente as participações do treinamento selecionado',html)
        _,ctx=self.filtered(treinamento=other);self.assert_ids(ctx,[self.ids[0],self.ids[4]])
        self.assertEqual(ctx['indicadores']['total_segundos'],2000)
        self.assertEqual(ctx['quizzes'][self.ids[4]]['percentual'],100)

    def test_valid_training_without_assignments(self):
        t=Treinamento(titulo='Sem atribuição');db.session.add(t);db.session.commit()
        html,ctx=self.filtered(treinamento=t.id);self.assert_ids(ctx,[])
        self.assertIn('Treinamento: Sem atribuição',html)

    def test_invalid_parameters(self):
        for params in [dict(departamento='999999999999999999999'),dict(treinamento='abc'),
                       dict(departamento='-1',treinamento='999999'),dict(status='errado',atividade='x',situacao='x')]:
            _,ctx=self.filtered(**params);self.assertEqual(ctx['indicadores']['total_colaboradores'],7)
            self.assertEqual(ctx['parametros'],{})
        for page in ['-1','abc','0','999999999999999999999999999']:
            _,ctx=self.filtered(page=page);self.assertEqual(ctx['pagina'],1)

    def test_pagination_preserves_all_filters_and_cards(self):
        for i in range(30):
            c=Colaborador(nome='Turma '+str(i),email=f'turma{i}@example.invalid',departamento_id=self.dep)
            db.session.add(c);db.session.flush()
            p=ParticipacaoTreinamento(token='turma'+str(i),colaborador_id=c.id,treinamento_id=self.tid,
                iniciado_em=datetime(2026,1,1))
            db.session.add(p);db.session.flush()
            db.session.add(TempoTreinamento(participacao_id=p.id,modulo_id=self.mods[0].id,segundos_ativos=10))
        db.session.commit()
        params=dict(departamento=self.dep,treinamento=self.tid,status='em_andamento',atividade='com_tempo',situacao='ativos',busca='Turma')
        html,ctx=self.filtered(**params)
        self.assertEqual(len(ctx['colaboradores']),25)
        self.assertEqual(ctx['indicadores']['total_colaboradores'],30)
        self.assertEqual(ctx['indicadores']['total_segundos'],300)
        href=unescape(re.search(r'rel="next" href="([^"]+)"',html).group(1))
        query=parse_qs(urlsplit(href).query)
        for key,value in params.items():self.assertEqual(query[key],[str(value)])
        html,ctx=self.get(href)
        self.assertEqual(len(ctx['colaboradores']),5)
        self.assertEqual(ctx['indicadores']['total_segundos'],300)
        self.assertIn('rel="prev"',html)
        _,ctx=self.filtered(**params,page='99999999999999999999');self.assertEqual(ctx['pagina'],2)
        self.assertIn('Limpar filtros',html)

    def test_query_count_constant(self):
        self.statements.clear();self.filtered(status='em_andamento')
        self.assertEqual(len(self.statements),5)
        self.statements.clear();self.filtered(busca='ausente')
        self.assertEqual(len(self.statements),4)


class FiltrosPostgresTest(FiltrosTest):
    postgres=True
