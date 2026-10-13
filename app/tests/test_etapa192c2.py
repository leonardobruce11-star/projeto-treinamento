"""C2: leituras multi-linha em schema PostgreSQL descartável; portal fechado."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import InvalidRequestError, IntegrityError
from reportlab.platypus import Table
from app.extensions import db
from app.models import ParticipacaoTreinamento as P, ResultadoQuiz as R, Campanha, FeedbackTreinamento
from app.utils.resultados_quiz import (
    ultima_tentativa, primeira_aprovacao, resultado_representativo,
    tem_tentativa_aprovada, total_tentativas, resultados_carregados,
    resultados_representativos_sql, resumo_tentativas,
)
from app.utils.painel_colaboradores import (
    consulta_participacoes_painel, consulta_quiz_mais_recente,
    calcular_indicadores_gerais, status_participacao,
)
import test_status_aprovacao_admin as admin_fixture
import test_portal_treinamento_quiz as portal_fixture


class HelpersTentativasTest(unittest.TestCase):
    def row(self, n, aprovado, nota):
        return SimpleNamespace(numero_tentativa=n,id=100+n,aprovado=aprovado,percentual=nota)

    def test_politica_central_sem_usar_maior_nota(self):
        for rows,numero in [([],None),([self.row(1,None,100)],1),
            ([self.row(1,False,70),self.row(2,False,20)],2),
            ([self.row(1,None,100),self.row(2,False,10),self.row(3,None,100)],2),
            ([self.row(1,False,0),self.row(2,True,80),self.row(3,True,100),self.row(4,False,0)],2)]:
            with self.subTest(numero=numero):
                result=resultado_representativo(list(reversed(rows)))
                self.assertEqual(result.numero_tentativa if result else None,numero)
                self.assertEqual(total_tentativas(rows),len(rows))
                self.assertEqual(tem_tentativa_aprovada(rows),any(r.aprovado is True for r in rows))
                self.assertEqual(ultima_tentativa(rows).numero_tentativa if rows else None,len(rows) or None)

    def test_primeira_aprovacao_ignora_maior_nota_e_data(self):
        rows=[self.row(3,True,100),self.row(2,True,80),self.row(1,False,70)]
        self.assertEqual(primeira_aprovacao(rows).numero_tentativa,2)
        self.assertEqual(resumo_tentativas(rows)['primeira_aprovacao'].id,102)
        self.assertEqual([r.numero_tentativa for r in resumo_tentativas(rows)['historico']],[1,2,3])


class ConsumidoresMultiPostgresTest(unittest.TestCase):
    postgres=True
    tearDown=admin_fixture.StatusAprovacaoAdminTest.tearDown
    get=admin_fixture.StatusAprovacaoAdminTest.get
    dashboard=admin_fixture.StatusAprovacaoAdminTest.dashboard
    snapshot=admin_fixture.StatusAprovacaoAdminTest.snapshot

    def setUp(self):
        admin_fixture.StatusAprovacaoAdminTest.setUp(self)
        R.query.delete();db.session.flush()
        patterns={0:[None,None],1:[False],2:[False,False,True,True,False,None],
                  3:[False,False],5:[None,False,None]}
        self.expected={0:'historico',1:'reprovado',2:'aprovado',3:'reprovado',4:'aprovado',5:'reprovado'}
        self.representative={0:2,1:1,2:3,3:2,5:2}
        for i,values in patterns.items():
            p=db.session.get(P,self.pids[i]);p.iniciado_em=datetime(2026,1,1,tzinfo=timezone.utc)
            p.concluido_em=datetime(2026,1,2,tzinfo=timezone.utc);p.aprovado_em=None
            for n,approved in enumerate(values,1):
                at=datetime(2026,1,2+n,tzinfo=timezone.utc)
                hits=6 if i==2 and n==4 else 5 if approved else 4
                r=R(participacao_id=p.id,numero_tentativa=n,total_questoes=6,total_acertos=hits,
                    percentual=hits*100/6,concluido_em=at,aprovado=approved,
                    percentual_minimo_aplicado=80 if approved is not None else None,
                    minimo_acertos_aplicado=5 if approved is not None else None)
                db.session.add(r)
                if approved and p.aprovado_em is None:p.aprovado_em=at
        db.session.commit();db.session.expire_all();self.statements.clear()

    def loaded(self):
        return db.session.execute(select(P).options(selectinload(P.resultados_quiz)).order_by(P.id)).scalars().all()

    def test_python_sql_mesma_selecao_e_status(self):
        objs=self.loaded();rows={r['participacao_id']:r for r in db.session.execute(consulta_participacoes_painel(self.ids)).mappings()}
        reps={r['participacao_id']:r for r in db.session.execute(select(resultados_representativos_sql())).mappings()}
        for i,p in enumerate(objs):
            values=resultados_carregados(p);r=resultado_representativo(values)
            self.assertEqual(status_participacao(p,values),self.expected[i])
            self.assertEqual(rows[p.id]['status'],self.expected[i])
            self.assertEqual(rows[p.id]['total_tentativas'],len(values))
            if r:
                self.assertEqual(reps[p.id]['id'],r.id)
                self.assertEqual(rows[p.id]['numero_tentativa'],self.representative[i])
        self.assertEqual(len(rows),6)

    def test_primeira_aprovacao_coincide_com_timestamp_sem_reescrever(self):
        p=self.loaded()[2];r=primeira_aprovacao(resultados_carregados(p))
        self.assertEqual(r.numero_tentativa,3);self.assertEqual(r.concluido_em,p.aprovado_em)
        self.assertNotEqual(r.percentual,100)
        before=p.aprovado_em
        self.get(f'/colaboradores/{self.ids[2]}')
        self.assertEqual(db.session.get(P,p.id).aprovado_em,before)

    def test_metricas_sem_multiplicar_participacao_ou_tempo(self):
        m=calcular_indicadores_gerais()
        self.assertEqual((m['atribuidas'],m['aprovadas'],m['reprovadas'],m['historicas'],m['total_segundos']),(6,2,3,1,150))
        self.assertEqual(m['taxa_aprovacao'],40)
        for status,count in [('aprovado',2),('reprovado',3),('historico',1)]:
            _,ctx=self.get('/colaboradores/painel?status='+status)
            self.assertEqual(len(ctx['colaboradores']),count)

    def test_painel_nota_criterio_numero_e_total_representativos(self):
        html,ctx=self.get();row=ctx['participacoes_painel'][self.ids[2]][0]
        self.assertEqual(row['numero_tentativa'],3);self.assertEqual(row['total_tentativas'],6)
        self.assertEqual(row['primeira_aprovacao_id'],row['resultado_quiz_id'])
        self.assertAlmostEqual(row['percentual'],500/6)
        self.assertEqual(row['percentual_minimo_aplicado'],80)
        self.assertIn('Tentativas: 6',html)
        recent=list(db.session.execute(consulta_quiz_mais_recente(self.ids)).mappings())
        self.assertEqual(len(recent),5)

    def test_detalhe_historico_ordenado_e_primeira_aprovacao(self):
        html,ctx=self.get(f'/colaboradores/{self.ids[2]}')
        h=ctx['tentativas_por_participacao'][self.pids[2]]
        self.assertEqual([r.numero_tentativa for r in h['historico']],list(range(1,7)))
        self.assertEqual(h['resultado'].numero_tentativa,3)
        self.assertEqual(h['primeira_aprovacao'].numero_tentativa,3)
        self.assertEqual(ctx['estados_participacoes'][self.pids[2]],'aprovado')
        self.assertIn('Tentativas: 6',html)

    def test_dashboard_recentes_medias_contagens_por_participacao(self):
        self.dashboard();html,ctx=self.get('/dashboard/')
        self.assertEqual(ctx['total_quizzes'],5)
        recent=ctx['resultados_recentes'];self.assertEqual(len(recent),5)
        self.assertEqual(len({r.participacao_id for r in recent}),5)
        self.assertEqual(next(r for r in recent if r.participacao_id==self.pids[2]).numero_tentativa,3)
        self.assertAlmostEqual(ctx['media_quiz'],70,places=2)
        self.assertEqual(ctx['indicadores_departamentos'][0]['quizzes'],5)
        self.assertAlmostEqual(ctx['indicadores_departamentos'][0]['media_quiz'],70,places=2)
        self.assertEqual(ctx['metricas_participacao']['reprovadas'],3)

    def test_campanhas_metricas_e_templates_sem_duplicacao(self):
        from app.campanhas.routes import carregar_participacoes_campanha,calcular_metricas_piloto
        c=Campanha(nome='Múltiplas',status='piloto_pronto');db.session.add(c);db.session.flush()
        P.query.update({P.campanha_id:c.id});db.session.commit();db.session.expire_all()
        rows=carregar_participacoes_campanha(c.id);self.assertEqual(len(rows),6)
        _,resultados,m=calcular_metricas_piloto(c);self.assertEqual(len(resultados),5);self.assertEqual(m['quizzes'],5)
        self.assertAlmostEqual(m['media_quiz'],70,places=2)
        from app.utils.painel_colaboradores import contexto_status_capacitacao
        from jinja2 import Environment,FileSystemLoader,ChainableUndefined
        from pathlib import Path
        env=Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1]/'app/templates')),undefined=ChainableUndefined,autoescape=True)
        env.filters['tempo_ativo']=lambda value:'0 min'
        env.globals.update(contexto_status_capacitacao(),url_for=lambda *a,**kw:'/',csrf_token=lambda:'test',get_flashed_messages=lambda **kw:[],current_user=SimpleNamespace(is_authenticated=False))
        for name in ('campanhas/relatorio.html','campanhas/detalhe.html','campanhas/analise_piloto.html'):
            html=env.get_template(name).render(campanha=c,participacoes=rows,tempos_ativos={},indicadores=m,metricas=m,auditorias=[],total_participantes=6,total_nao_iniciados=0,total_em_andamento=0,total_concluidos=6,total_quizzes_campanha=5,media_quiz_campanha=70,taxa_conclusao_campanha=100)
            self.assertIn('Reprovado',html);self.assertIn('histórico/não classificado',html)

    def test_pdf_uma_linha_por_participacao_e_resultado_representativo(self):
        from app.relatorios import campanha_pdf as pdf
        from app.models import Colaborador
        rows=db.session.execute(select(P).options(selectinload(P.resultados_quiz),selectinload(P.colaborador).selectinload(Colaborador.departamento)).order_by(P.id)).scalars().all()
        with patch.object(pdf,'Table',wraps=Table) as tables:
            f=pdf.gerar_relatorio_campanha_pdf(Campanha(nome='Multi',status='piloto_pronto'),rows,{},[],{})
        self.assertTrue(f.getvalue().startswith(b'%PDF-'))
        table=next(c.args[0] for c in tables.call_args_list if 'Tempo ativo' in c.args[0][0])
        self.assertEqual(len(table)-1,6)
        self.assertIn('Aprovado',table[3][2].getPlainText())
        self.assertEqual(table[3][3],f'{500/6}%')

    def test_feedback_unico_preservado_com_tentativas_posteriores(self):
        from app.utils.feedback_treinamento import registrar_feedback
        data={k:'4' for k in ('avaliacao_geral','avaliacao_conteudo','avaliacao_clareza','avaliacao_utilidade')}
        first=registrar_feedback(self.pids[2],dict(data,comentario='Original'))
        before=db.session.get(FeedbackTreinamento,first.feedback_id).criado_em
        self.add_attempts(7)
        again=registrar_feedback(self.pids[2],dict(data,comentario='Novo'))
        self.assertFalse(again.criado);self.assertEqual(first.feedback_id,again.feedback_id)
        f=db.session.get(FeedbackTreinamento,first.feedback_id)
        self.assertEqual(f.comentario,'Original');self.assertEqual(f.criado_em,before)
        self.assertEqual(FeedbackTreinamento.query.count(),1)

    def add_attempts(self, n):
        for pid in self.pids:
            if pid==self.pids[4]:continue
            top=db.session.execute(select(db.func.max(R.numero_tentativa)).where(R.participacao_id==pid)).scalar_one() or 0
            for number in range(top+1,n+1):
                db.session.add(R(participacao_id=pid,numero_tentativa=number,total_questoes=6,total_acertos=4,percentual=400/6,aprovado=False,percentual_minimo_aplicado=80,minimo_acertos_aplicado=5))
        db.session.commit();db.session.expire_all()

    def test_queries_constantes_1_vs_5_tentativas(self):
        # Keep one row per participation, then expand to five. Budget includes templates.
        R.query.filter(R.numero_tentativa>1).delete();db.session.commit();db.session.expire_all()
        self.dashboard();paths=['/colaboradores/painel','/dashboard/',f'/colaboradores/{self.ids[2]}']
        def measure():
            result=[]
            for path in paths:
                db.session.remove();self.statements.clear();self.get(path);result.append(len(self.statements))
            return result
        small=measure();self.add_attempts(5);large=measure()
        self.assertEqual(small,large);self.assertEqual(small[0],5);self.assertLessEqual(small[1],30);self.assertEqual(small[2],8)
        print('C2 QUERY COUNTS 1/5 tentativas: painel/dashboard/detalhe',small,large)

    def test_relacao_nao_dispara_lazy_load(self):
        db.session.remove();p=db.session.get(P,self.pids[2]);self.statements.clear()
        with self.assertRaises(InvalidRequestError):resultados_carregados(p)
        with self.assertRaises(InvalidRequestError):_ = p.resultados_quiz
        self.assertEqual(self.statements,[])

    def test_data_nao_substitui_numeracao_na_selecao(self):
        db.session.execute(db.update(R).where(R.participacao_id==self.pids[2], R.numero_tentativa==4).values(concluido_em=datetime(2020,1,1,tzinfo=timezone.utc)))
        db.session.execute(db.update(R).where(R.participacao_id==self.pids[3], R.numero_tentativa==1).values(concluido_em=datetime(2030,1,1,tzinfo=timezone.utc)))
        db.session.commit();db.session.expire_all()
        rows={r['participacao_id']:r for r in db.session.execute(consulta_participacoes_painel(self.ids)).mappings()}
        self.assertEqual(rows[self.pids[2]]['numero_tentativa'],3)
        self.assertEqual(rows[self.pids[3]]['numero_tentativa'],2)
        self.assertEqual(primeira_aprovacao(resultados_carregados(self.loaded()[2])).numero_tentativa,3)

    def test_timestamp_inconsistente_nao_reclassifica_nem_corrige_historico(self):
        p=db.session.get(P,self.pids[2]);p.aprovado_em=datetime(2020,1,1,tzinfo=timezone.utc)
        db.session.commit();db.session.expire_all();before=self.snapshot()
        html,ctx=self.get(f'/colaboradores/{self.ids[2]}')
        h=ctx['tentativas_por_participacao'][self.pids[2]]
        self.assertEqual(h['primeira_aprovacao'].numero_tentativa,3)
        self.assertNotEqual(ctx['participacoes'][0].aprovado_em,h['primeira_aprovacao'].concluido_em)
        self.assertEqual(before,self.snapshot())

    def test_aprovacao_persistida_sem_conclusao_python_sql(self):
        for i in (2,4):
            p=db.session.get(P,self.pids[i]);p.concluido_em=None
            if i==4:p.iniciado_em=None
        db.session.commit();db.session.expire_all()
        before=self.snapshot()
        objs=self.loaded();rows={r['participacao_id']:r for r in db.session.execute(consulta_participacoes_painel(self.ids)).mappings()}
        for i in (2,4):
            self.assertEqual(status_participacao(objs[i],resultados_carregados(objs[i])),'aprovado')
            self.assertEqual(rows[self.pids[i]]['status'],'aprovado')
        self.assertEqual(calcular_indicadores_gerais()['aprovadas'],2)
        _,ctx=self.get(f'/colaboradores/{self.ids[2]}')
        self.assertEqual(ctx['total_em_andamento'],0)
        self.assertEqual(before,self.snapshot())

    def test_consumidores_somente_leitura(self):
        self.dashboard();before=self.snapshot()
        for path in ('/colaboradores/painel','/dashboard/',f'/colaboradores/{self.ids[2]}'):self.get(path)
        self.assertEqual(before,self.snapshot())


class PortalC2PostgresTest(unittest.TestCase):
    postgres=True
    setUpClass=portal_fixture.QuizTest.__dict__['setUpClass']
    tearDownClass=portal_fixture.QuizTest.__dict__['tearDownClass']
    setUp=portal_fixture.QuizTest.setUp
    tearDown=portal_fixture.QuizTest.tearDown
    request=portal_fixture.QuizTest.request
    finish_modules=portal_fixture.QuizTest.finish_modules
    post_quiz=portal_fixture.QuizTest.post_quiz

    def test_fluxo_real_sem_tentativa_2_e_replay_idempotente(self):
        self.finish_modules();self.assertEqual(self.post_quiz(self.answers).status_code,303)
        with self.app.app_context():
            before=[tuple(r) for r in db.session.execute(select(*R.__table__.c))]
            self.assertEqual(before[0][0],1) # numero_tentativa is first metadata column
        self.assertEqual(self.post_quiz(self.answers).status_code,303)
        with self.app.app_context():
            self.assertEqual(before,[tuple(r) for r in db.session.execute(select(*R.__table__.c))])
            with self.assertRaises(IntegrityError):
                db.session.add(R(participacao_id=self.pid,numero_tentativa=1,total_questoes=2,total_acertos=2,percentual=100));db.session.commit()
            db.session.rollback()

    def test_feedback_portal_multilinha_preserva_registro_e_link(self):
        self.finish_modules();self.post_quiz(self.answers)
        url='/aprender/t/principal/feedback'
        data={k:'4' for k in ('avaliacao_geral','avaliacao_conteudo','avaliacao_clareza','avaliacao_utilidade')}
        self.assertEqual(self.client.post(url,data=dict(data,comentario='Original'),headers=self.headers).status_code,303)
        with self.app.app_context():
            db.session.add(R(participacao_id=self.pid,numero_tentativa=2,total_questoes=2,total_acertos=0,percentual=0))
            db.session.commit();before=[tuple(r) for r in db.session.execute(select(*FeedbackTreinamento.__table__.c))]
        response=self.client.get(url)
        self.assertEqual(response.status_code,200);self.assertIn(self.quiz_url,response.get_data(as_text=True))
        self.assertEqual(self.client.post(url,data=dict(data,comentario='Novo'),headers=self.headers).status_code,303)
        with self.app.app_context():self.assertEqual(before,[tuple(r) for r in db.session.execute(select(*FeedbackTreinamento.__table__.c))])

    def test_get_post_multilinha_sintetica_sem_criar_novo_resultado(self):
        self.finish_modules();self.post_quiz(self.answers)
        with self.app.app_context():
            db.session.add(R(participacao_id=self.pid,numero_tentativa=2,total_questoes=2,total_acertos=0,percentual=0,aprovado=False,percentual_minimo_aplicado=70,minimo_acertos_aplicado=2))
            db.session.commit();before=[tuple(r) for r in db.session.execute(select(*R.__table__.c).order_by(R.id))]
        for response in (self.client.get(self.quiz_url),self.client.get(self.post_quiz(self.answers).location)):
            self.assertEqual(response.status_code,200)
            self.assertIn('100.0%',response.get_data(as_text=True))
        with self.app.app_context():self.assertEqual(before,[tuple(r) for r in db.session.execute(select(*R.__table__.c).order_by(R.id))])
