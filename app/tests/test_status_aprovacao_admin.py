"""19.1D: leitura administrativa, status, agregados, filtros e desempenho."""
from app.utils.resultados_quiz import resultado_representativo, resultados_carregados

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from reportlab.platypus import Table
from app.extensions import db
from app.models import Departamento, Colaborador, Treinamento, ModuloTreinamento
from app.models import ParticipacaoTreinamento as P, ResultadoQuiz as R, TempoTreinamento
from app.models.campanha import Campanha
from app.utils.painel_colaboradores import (
    calcular_indicadores_gerais, consulta_painel_colaboradores,
    consulta_participacoes_painel, status_participacao, contexto_status_capacitacao,
)
import app.simulacoes.preparacao
import test_painel_colaboradores_pagina as fixture


class StatusAprovacaoAdminTest(unittest.TestCase):
    postgres = False
    tearDown = fixture.PainelPaginaTest.tearDown
    get = fixture.PainelPaginaTest.get

    def setUp(self):
        fixture.PainelPaginaTest.setUp(self)
        self.ps=[];self.people=[]
        self.d=Departamento(nome='Setor');self.t=Treinamento(titulo='Com quiz',nota_minima_percentual=Decimal(100))
        self.sem=Treinamento(titulo='Sem quiz')
        db.session.add_all([self.d,self.t,self.sem]);db.session.flush()
        self.m=ModuloTreinamento(treinamento_id=self.t.id,titulo='M',conteudo='x')
        self.ms=ModuloTreinamento(treinamento_id=self.sem.id,titulo='M',conteudo='x')
        db.session.add_all([self.m,self.ms]);db.session.flush()
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=datetime(2026,1,2,tzinfo=timezone.utc)
        for i in range(6):
            c=Colaborador(nome=f'Pessoa {i}',email=f'p{i}@example.invalid',departamento_id=self.d.id)
            db.session.add(c);db.session.flush();self.people.append(c)
            p=P(token=f'admin{i}',colaborador_id=c.id,treinamento_id=self.sem.id if i==4 else self.t.id,
                iniciado_em=None if i in (0,5) else start,concluido_em=end if i>=2 else None,
                aprovado_em=end if i in (2,4) else None)
            db.session.add(p);db.session.flush();self.ps.append(p)
            if i in (2,3,5):
                db.session.add(R(participacao_id=p.id,total_questoes=6,total_acertos=5 if i in (2,5) else 4,
                    percentual=83.33 if i in (2,5) else 66.67,concluido_em=end,
                    aprovado=True if i==2 else False if i==3 else None,
                    percentual_minimo_aplicado=Decimal(80) if i in (2,3) else None,
                    minimo_acertos_aplicado=5 if i in (2,3) else None))
            db.session.add(TempoTreinamento(participacao_id=p.id,modulo_id=self.ms.id if i==4 else self.m.id,segundos_ativos=10*i))
        db.session.commit();self.ids=[c.id for c in self.people];self.pids=[p.id for p in self.ps];self.statements.clear()

    def snapshot(self):
        return {t:[tuple(row) for row in db.session.execute(select(*t.__table__.c).order_by(t.id))]
                for t in (P,R,Treinamento,TempoTreinamento)}

    def test_contagens_e_taxas(self):
        s=calcular_indicadores_gerais()
        expected=dict(atribuidas=6,iniciadas=4,concluidas=4,aprovadas=2,reprovadas=1,
                      historicas=1,em_andamento=1,nao_iniciadas=1,pendentes=2,total_segundos=150)
        for k,v in expected.items():self.assertEqual(s[k],v,k)
        self.assertAlmostEqual(s['taxa_conclusao'],100*4/6)
        self.assertAlmostEqual(s['taxa_aprovacao'],100*2/3)

    def test_status_python_e_sql_equivalentes(self):
        expected=['nao_iniciado','em_andamento','aprovado','reprovado','aprovado','historico']
        objs=db.session.execute(select(P).options(selectinload(P.resultados_quiz)).order_by(P.id)).scalars().all()
        self.assertEqual([status_participacao(p,resultado_representativo(resultados_carregados(p))) for p in objs],expected)
        rows=list(db.session.execute(consulta_participacoes_painel(self.ids)).mappings())
        self.assertEqual([r['status'] for r in rows],expected)

    def test_nota_corrente_nao_classifica_snapshot_ou_historico(self):
        self.t.nota_minima_percentual=Decimal('90.00');db.session.commit()
        html,ctx=self.get();self.assertEqual(ctx['indicadores']['aprovadas'],2);self.assertEqual(ctx['indicadores']['historicas'],1)
        self.assertIn('Mínimo aplicado: 80.00%',html)
        self.assertNotIn('Mínimo aplicado: 90.00%',html)
        self.assertIn('Concluído — histórico/não classificado',html)

    def test_denominadores_zero_e_somente_historico(self):
        q=consulta_painel_colaboradores().where(Colaborador.id==self.ids[5]);s=calcular_indicadores_gerais(q)
        self.assertEqual(s['taxa_conclusao'],100);self.assertIsNone(s['taxa_aprovacao']);self.assertEqual(s['historicas'],1)
        s=calcular_indicadores_gerais(consulta_painel_colaboradores().where(Colaborador.id==-1))
        self.assertIsNone(s['taxa_conclusao']);self.assertIsNone(s['taxa_aprovacao'])
        html,ctx=self.get('/colaboradores/painel?status=historico');self.assertIsNone(ctx['indicadores']['taxa_aprovacao']);self.assertIn('Sem classificados: —.',html)

    def test_zero_real_somente_reprovado(self):
        s=calcular_indicadores_gerais(consulta_painel_colaboradores().where(Colaborador.id==self.ids[3]))
        self.assertEqual(s['taxa_aprovacao'],0);self.assertEqual(s['reprovadas'],1)
        self.assertEqual(s['concluidas'],1);self.assertEqual(s['aprovadas'],0)

    def test_sem_quiz_historico_nao_inferido(self):
        self.ps[4].aprovado_em=None;db.session.commit()
        html,ctx=self.get('/colaboradores/painel?treinamento='+str(self.sem.id))
        self.assertEqual(ctx['indicadores']['aprovadas'],0);self.assertEqual(ctx['indicadores']['historicas'],1)
        self.assertIsNone(ctx['indicadores']['taxa_aprovacao'])
        self.assertEqual(ctx['participacoes_painel'][self.ids[4]][0]['status'],'historico')

    def test_filtros_novos_e_antigos(self):
        groups={'aprovado':[2,4],'reprovado':[3],'historico':[5],
                'concluido':[2,3,4,5],'em_andamento':[1],'nao_iniciado':[0]}
        for status,indices in groups.items():
            html,ctx=self.get('/colaboradores/painel?status='+status)
            self.assertEqual({r['colaborador_id'] for r in ctx['colaboradores']},{self.ids[i] for i in indices})

    def test_filtros_aprovacao_sao_presenca_nao_estado_global(self):
        db.session.add(P(token='nova-repro',colaborador_id=self.ids[2],treinamento_id=self.t.id,
                         concluido_em=datetime(2026,1,3,tzinfo=timezone.utc)))
        db.session.flush();p=P.query.filter_by(token='nova-repro').one()
        db.session.add(R(participacao_id=p.id,total_questoes=6,total_acertos=4,percentual=66.67,
                         aprovado=False,percentual_minimo_aplicado=80,minimo_acertos_aplicado=5));db.session.commit()
        for status in ('aprovado','reprovado'):
            _,ctx=self.get('/colaboradores/painel?status='+status)
            self.assertIn(self.ids[2],{r['colaborador_id'] for r in ctx['colaboradores']})
        _,ctx=self.get('/colaboradores/painel?status=reprovado&treinamento='+str(self.sem.id))
        self.assertEqual(ctx['indicadores']['total_colaboradores'],0)

    def test_painel_sem_quiz_nao_inventa_percentual(self):
        html,ctx=self.get('/colaboradores/painel?treinamento='+str(self.sem.id))
        p=ctx['participacoes_painel'][self.ids[4]][0]
        self.assertEqual(p['status'],'aprovado');self.assertIsNone(p['percentual']);self.assertIsNone(p['resultado_quiz_id'])
        self.assertIn('Conclusão sem quiz · nota não aplicável',html)
        self.assertEqual(p['concluido_em'],p['aprovado_em']);self.assertEqual(p['total_segundos'],40)

    def test_detalhe_aprovado_reprovado_sem_quiz_e_historico(self):
        for i,label in [(2,'Aprovado'),(3,'Reprovado'),(4,'Conclusão sem quiz · nota não aplicável'),(5,'Resultado histórico — não classificado pela regra atual de aprovação.')]:
            html,ctx=self.get(f'/colaboradores/{self.ids[i]}');self.assertIn(label,html)
            if i in (2,3):self.assertIn('Mínimo aplicado: 80.00%',html);self.assertIn('5 acertos',html)
            if i==4:self.assertEqual(ctx['participacoes'][0].resultados_quiz,[])
        self.assertEqual(ctx['total_nao_iniciados'],0)

    def dashboard(self):
        from app.dashboard.routes import dashboard_bp
        self.app.view_functions.pop('dashboard.index')
        self.app.register_blueprint(dashboard_bp)

    def test_dashboard_real(self):
        self.dashboard();html,ctx=self.get('/dashboard/')
        s=ctx['metricas_participacao'];self.assertEqual((s['aprovadas'],s['reprovadas'],s['historicas']),(2,1,1))
        self.assertIn('Taxa de aprovação',html);self.assertIn('66.7%',html)
        self.assertEqual(ctx['total_nao_iniciados'],1)
        self.assertIn('Reprovado',html);self.assertIn('Concluído — histórico/não classificado',html)
        self.assertIn('score-high',html);self.assertIn('score-low',html)

    def test_dashboard_sem_classificados(self):
        R.query.delete();P.query.update({P.aprovado_em:None});db.session.commit()
        self.dashboard();html,ctx=self.get('/dashboard/')
        self.assertIsNone(ctx['metricas_participacao']['taxa_aprovacao']);self.assertIn('Sem classificados: —.',html)

    def test_paginas_somente_leitura(self):
        self.dashboard();antes=self.snapshot()
        for path in ['/colaboradores/painel','/colaboradores/painel?status=reprovado',f'/colaboradores/{self.ids[3]}','/dashboard/']:self.get(path)
        self.assertEqual(self.snapshot(),antes)
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in self.statements))

    def ampliar(self, n):
        for i in range(n):
            c=Colaborador(nome=f'Extra {i:03d}',email=f'extra{i}@example.invalid',departamento_id=self.d.id)
            db.session.add(c);db.session.flush()
            p=P(token=f'extra{i}',colaborador_id=c.id,treinamento_id=self.t.id,
                iniciado_em=datetime(2026,1,1),concluido_em=datetime(2026,1,2),aprovado_em=datetime(2026,1,2))
            db.session.add(p);db.session.flush();db.session.add(R(participacao_id=p.id,total_questoes=10,
                total_acertos=7,percentual=70,aprovado=True,percentual_minimo_aplicado=70,minimo_acertos_aplicado=7))
        db.session.commit()

    def test_queries_constantes_com_6_e_86_colaboradores(self):
        self.statements.clear();self.get();pequeno=len(self.statements)
        self.ampliar(80);self.statements.clear();html,ctx=self.get();grande=len(self.statements)
        self.assertEqual((pequeno,grande),(5,5));self.assertEqual(len(ctx['colaboradores']),25)
        self.assertEqual(ctx['indicadores']['total_colaboradores'],86);self.assertEqual(ctx['indicadores']['aprovadas'],82)
        self.assertEqual(len(ctx['participacoes_painel']),25)

    def test_dashboard_queries_constantes(self):
        self.dashboard();self.statements.clear();self.get('/dashboard/');small=len(self.statements)
        self.ampliar(80);self.statements.clear();self.get('/dashboard/');large=len(self.statements)
        self.assertEqual(small,large)

    def test_detalhe_queries_constantes(self):
        cid=self.ids[2];db.session.expire_all();self.statements.clear();self.get(f'/colaboradores/{cid}');small=len(self.statements)
        for i in range(40):
            db.session.add(P(token=f'legado_extra{i}',colaborador_id=cid,treinamento_id=self.t.id,concluido_em=datetime(2026,1,2)))
        db.session.commit();self.statements.clear();self.get(f'/colaboradores/{cid}');large=len(self.statements)
        self.assertEqual(small,large)

    def test_paginacao_filtro_novo(self):
        self.ampliar(30)
        _,first=self.get('/colaboradores/painel?status=aprovado')
        html,last=self.get('/colaboradores/painel?status=aprovado&page=2')
        self.assertEqual(len(first['colaboradores']),25);self.assertEqual(len(last['colaboradores']),7)
        self.assertEqual(first['indicadores'],last['indicadores']);self.assertEqual(last['indicadores']['aprovadas'],32)
        self.assertIn('status=aprovado',html)

    def test_pdf_status_congelados(self):
        import app.relatorios.campanha_pdf as pdf
        objs=db.session.execute(select(P).options(selectinload(P.resultados_quiz),
              selectinload(P.colaborador).selectinload(Colaborador.departamento))).scalars().all()
        with patch.object(pdf,'Table',wraps=Table) as tables:
            f=pdf.gerar_relatorio_campanha_pdf(Campanha(nome='Auditoria',status='piloto_pronto'),objs,{},[],{})
        self.assertTrue(f.getvalue().startswith(b'%PDF-'))
        table=next(c.args[0] for c in tables.call_args_list if 'Tempo ativo' in c.args[0][0])
        textos=' '.join(row[2].getPlainText() for row in table[1:])
        for label in ('Aprovado','Reprovado','histórico/não classificado'):self.assertIn(label,textos)

    def test_campanhas_templates_status_nao_confundem_conclusao(self):
        from jinja2 import Environment,FileSystemLoader,ChainableUndefined
        from pathlib import Path
        env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'app/templates')),undefined=ChainableUndefined,autoescape=True)
        env.filters['tempo_ativo']=lambda value:'0 min'
        env.globals.update(contexto_status_capacitacao(),url_for=lambda *a,**kw:'/',csrf_token=lambda:'test',
            get_flashed_messages=lambda **kw:[],current_user=SimpleNamespace(is_authenticated=False))
        objs=db.session.execute(select(P).options(selectinload(P.resultados_quiz))).scalars().all()
        c=SimpleNamespace(id=1,nome='Auditoria',status='piloto_pronto',participacoes_treinamento=objs)
        for name in ('campanhas/relatorio.html','campanhas/detalhe.html','campanhas/analise_piloto.html'):
            html=env.get_template(name).render(campanha=c,participacoes=objs,tempos_ativos={},indicadores={'participantes':6,'quizzes':3},metricas={},auditorias=[],total_participantes=6,total_nao_iniciados=1,total_em_andamento=1,total_concluidos=4,total_quizzes_campanha=3,media_quiz_campanha=77.77,taxa_conclusao_campanha=66.67)
            self.assertIn('Reprovado',html,name);self.assertIn('histórico/não classificado',html,name)

    def test_resultado_aprovado_persistido_prevalece_sem_conclusao(self):
        p=self.ps[2];p.concluido_em=None;p.aprovado_em=None;db.session.commit()
        s=calcular_indicadores_gerais();self.assertEqual(s['aprovadas'],2)
        p=db.session.execute(select(P).options(selectinload(P.resultados_quiz)).where(P.id==p.id)).scalar_one()
        self.assertEqual(status_participacao(p,resultado_representativo(resultados_carregados(p))),'aprovado')
        # C2: aprovação persistida prevalece; não altera a conclusão ausente de legados.


class StatusAprovacaoAdminPostgresTest(StatusAprovacaoAdminTest):
    postgres = True
