"""Solicitações e workers isolados. PostgreSQL exercita concorrência real."""
import unittest, logging, json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select, insert, update, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.extensions import db, migrate
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E, ESTADOS_ENVIO
from app.models.tentativa_envio_convite import TentativaEnvioConvite as T
from app.models.usuario import Usuario
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.auditoria import Auditoria
from app.models.resultado_quiz import ResultadoQuiz
from app.models.tempo_treinamento import TempoTreinamento
from app.models.campanha import Campanha
from app.models.feedback_treinamento import FeedbackTreinamento
from app.utils.convites_treinamento import (
    solicitar_primeiro_envio, solicitar_reenvio, cancelar_envio,
    reservar_proximo_envio, finalizar_envio, processar_proximo_envio,
    ConviteInvalido, ConviteNaoAutorizado, ConfirmacaoConflitante, TransicaoConviteInvalida,
)
from app.utils.transporte_convites import TransporteFalso, ResultadoTransporte
import test_painel_colaboradores as fixture


class ConvitesTest(unittest.TestCase):
    postgres = False
    tearDown = fixture.PainelTest.tearDown

    def setUp(self):
        fixture.PainelTest.setUp(self);fixture.PainelTest.seed(self)
        self.pid=self.pids[0]
        p=db.session.get(P,self.pid);p.token='TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR'
        p.colaborador.email='pessoa@example.com'
        # Outra participação elegível para disputar confirmação/fila.
        self.outro_pid=self.pids[1];db.session.get(P,self.outro_pid).colaborador.email='outra@example.com'
        user=Usuario(nome='Admin',email='admin@example.com',senha_hash='HASH_NAO_REGISTRAR')
        db.session.add(user);db.session.commit();self.uid=user.id
        self.confirmacao=uuid4();db.session.remove()

    def first(self, **kwargs):
        return solicitar_primeiro_envio(kwargs.get('pid',self.pid),kwargs.get('uid',self.uid),kwargs.get('confirmacao',self.confirmacao))

    def read(self, model, ident):
        db.session.expire_all();return db.session.get(model,ident)

    def count(self, model):
        return db.session.execute(select(db.func.count()).select_from(model)).scalar_one()

    def snapshot(self):
        return {m.__tablename__:[tuple(r) for r in db.session.execute(select(m.__table__).order_by(m.id))]
                for m in [P,ResultadoQuiz,TempoTreinamento,Campanha,FeedbackTreinamento]}

    def test_first_sequence_snapshot_email_timestamp(self):
        r=self.first();self.assertTrue(r.criado);e=self.read(E,r.envio_id)
        self.assertEqual((e.sequencia,e.tipo,e.estado),(1,'primeiro_envio','pendente'))
        self.assertEqual(e.destinatario,'pessoa@example.com');self.assertEqual(e.identificador_confirmacao,self.confirmacao)
        self.assertIsNotNone(e.criado_em)
        if self.postgres:self.assertIsNotNone(e.criado_em.tzinfo)
        self.assertIsNone(e.processamento_iniciado_em);self.assertIsNone(e.finalizado_em)

    def test_repeat_same_confirmation_no_duplicate_audit(self):
        a=self.first();b=self.first();self.assertEqual(a.envio_id,b.envio_id);self.assertFalse(b.criado)
        self.assertEqual(self.count(E),1);self.assertEqual(self.count(Auditoria),1)

    def test_repeat_first_different_confirmation_preserves_initial(self):
        a=self.first();b=self.first(confirmacao=uuid4())
        self.assertEqual(a.envio_id,b.envio_id);self.assertFalse(b.criado);self.assertEqual(self.count(E),1)
        processar_proximo_envio(TransporteFalso())
        self.assertFalse(self.first(confirmacao=uuid4()).criado);self.assertEqual(self.count(T),1)

    def test_confirmation_unique_and_bound_to_participation_type_actor(self):
        self.first()
        with self.assertRaises(ConfirmacaoConflitante):self.first(pid=self.outro_pid)
        with self.assertRaises(ConfirmacaoConflitante):solicitar_reenvio(self.pid,self.uid,self.confirmacao)
        other=Usuario(nome='Admin2',email='admin2@example.com',senha_hash='hash')
        db.session.add(other);db.session.commit()
        with self.assertRaises(ConfirmacaoConflitante):self.first(uid=other.id)
        self.assertEqual(self.count(E),1)

    def test_invalid_confirmation_and_ids(self):
        for value in [None, '', 'TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR', 'https://host/aprender/t/TOKEN', True]:
            with self.subTest(value=value),self.assertRaises(ConviteInvalido):self.first(confirmacao=value)
        for value in [0,-1,True,'1',999999]:
            with self.subTest(value=value),self.assertRaises(ConviteInvalido):self.first(pid=value)
        self.assertEqual(self.count(E),0)

    def test_only_active_administrator(self):
        for field,value in [('ativo',False),('perfil','operador')]:
            user=self.read(Usuario,self.uid);setattr(user,field,value);db.session.commit()
            with self.assertRaises(ConviteNaoAutorizado):self.first()
            setattr(user,field,True if field=='ativo' else 'administrador');db.session.commit()
        with self.assertRaises(ConviteNaoAutorizado):self.first(uid=999999)
        self.assertEqual(self.count(E),0)

    def test_inactive_collaborator(self):
        self.read(P,self.pid).colaborador.ativo=False;db.session.commit()
        with self.assertRaises(ConviteInvalido):self.first()
        self.assertEqual(self.count(E),0);self.assertEqual(self.count(Auditoria),0)

    def test_inactive_training(self):
        self.read(P,self.pid).treinamento.ativo=False;db.session.commit()
        with self.assertRaises(ConviteInvalido):self.first()

    def test_completed_participation(self):
        self.read(P,self.pid).concluido_em=datetime.now(timezone.utc);db.session.commit()
        with self.assertRaises(ConviteInvalido):self.first()

    def test_invalid_email_without_dns_or_injection(self):
        for value in ['sem-arroba','x@', 'a@example.com\r\nBcc:secreto@example.com','x@example.invalid']:
            self.read(P,self.pid).colaborador.email=value;db.session.commit()
            with self.assertRaises(ConviteInvalido):self.first()
        self.assertEqual(self.count(E),0)

    def test_reenvio_distinct_sequence_and_snapshot(self):
        first=self.first();processar_proximo_envio(TransporteFalso())
        self.read(P,self.pid).colaborador.email='novo@example.com';db.session.commit()
        conf=uuid4();r=solicitar_reenvio(self.pid,self.uid,conf)
        e=self.read(E,r.envio_id);self.assertEqual((e.sequencia,e.tipo,e.destinatario),(2,'reenvio','novo@example.com'))
        repeat=solicitar_reenvio(self.pid,self.uid,conf);self.assertFalse(repeat.criado)
        self.assertEqual(repeat.envio_id,r.envio_id)
        self.assertEqual(self.read(E,first.envio_id).destinatario,'pessoa@example.com')
        processar_proximo_envio(TransporteFalso())
        third=solicitar_reenvio(self.pid,self.uid,uuid4());self.assertEqual(self.read(E,third.envio_id).sequencia,3)

    def test_reenvio_without_first_or_while_active_blocked(self):
        with self.assertRaises(ConviteInvalido):solicitar_reenvio(self.pid,self.uid,uuid4())
        self.first()
        with self.assertRaises(TransicaoConviteInvalida):solicitar_reenvio(self.pid,self.uid,uuid4())
        reservar_proximo_envio()
        with self.assertRaises(TransicaoConviteInvalida):solicitar_reenvio(self.pid,self.uid,uuid4())

    def test_reenvio_revalidates_eligibility(self):
        self.first();processar_proximo_envio(TransporteFalso())
        self.read(P,self.pid).concluido_em=datetime.now(timezone.utc);db.session.commit()
        with self.assertRaises(ConviteInvalido):solicitar_reenvio(self.pid,self.uid,uuid4())
        self.assertEqual(self.count(E),1)

    def test_reserve_one_attempt_and_no_second_reservation(self):
        e=self.first();r=reservar_proximo_envio();self.assertEqual(r.dados.envio_id,e.envio_id)
        self.assertIsNone(reservar_proximo_envio());self.assertEqual(self.count(T),1)
        t=self.read(T,r.tentativa_id);self.assertEqual(t.numero_tentativa,1)
        self.assertIsNone(t.resultado);self.assertIsNone(t.finalizado_em)
        self.assertEqual(self.read(E,e.envio_id).estado,'em_processamento')

    def test_success_and_idempotent_finalization(self):
        e=self.first();r=reservar_proximo_envio();success=ResultadoTransporte('enviado')
        self.assertTrue(finalizar_envio(r,success));self.assertFalse(finalizar_envio(r,success))
        self.assertEqual(self.read(E,e.envio_id).estado,'enviado')
        t=self.read(T,r.tentativa_id);self.assertEqual(t.resultado,'enviado');self.assertIsNotNone(t.finalizado_em)
        with self.assertRaises(TransicaoConviteInvalida):finalizar_envio(r,ResultadoTransporte('falha','transporte_falso_falha'))
        self.assertEqual(self.read(T,r.tentativa_id).resultado,'enviado')

    def test_controlled_failure(self):
        e=self.first();fake=TransporteFalso('falha');self.assertEqual(processar_proximo_envio(fake),e.envio_id)
        self.assertEqual(self.read(E,e.envio_id).estado,'falha');self.assertEqual(fake.chamadas,1)
        t=db.session.execute(select(T)).scalar_one();self.assertEqual(t.codigo_erro,'transporte_falso_falha')
        self.assertIsNone(processar_proximo_envio(fake));self.assertEqual(fake.chamadas,1)

    def test_indeterminate_no_automatic_retry(self):
        e=self.first();fake=TransporteFalso('resultado_indeterminado');processar_proximo_envio(fake)
        self.assertEqual(self.read(E,e.envio_id).estado,'resultado_indeterminado')
        self.assertIsNone(processar_proximo_envio(fake));self.assertEqual(fake.chamadas,1)
        self.assertTrue(solicitar_reenvio(self.pid,self.uid,uuid4()).criado)

    def test_exception_and_invalid_response_sanitized(self):
        class Broken:
            def enviar(self,dados):raise RuntimeError('SENHA_SECRETA TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR https://host/aprender/t/TOKEN')
        e=self.first()
        with self.assertNoLogs(level=logging.ERROR):processar_proximo_envio(Broken())
        self.assertEqual(self.read(E,e.envio_id).estado,'resultado_indeterminado')
        t=db.session.execute(select(T)).scalar_one();self.assertEqual(t.codigo_erro,'excecao_transporte')
        r=solicitar_reenvio(self.pid,self.uid,uuid4())
        class Invalid:
            def enviar(self,dados):return ResultadoTransporte('falha','SENHA_SECRETA')
        processar_proximo_envio(Invalid())
        t=db.session.execute(select(T).where(T.envio_id==r.envio_id)).scalar_one()
        self.assertEqual(t.codigo_erro,'retorno_invalido')

    def test_cancel_audit_and_repeated_cancel(self):
        e=self.first();self.assertTrue(cancelar_envio(e.envio_id,self.uid))
        self.assertFalse(cancelar_envio(e.envio_id,self.uid));self.assertEqual(self.read(E,e.envio_id).estado,'cancelado')
        self.assertIsNone(reservar_proximo_envio());self.assertEqual(self.count(T),0)
        self.assertEqual(self.count(Auditoria),2)
        self.assertTrue(solicitar_reenvio(self.pid,self.uid,uuid4()).criado)

    def test_cancel_processing_or_sent_forbidden(self):
        e=self.first();r=reservar_proximo_envio()
        with self.assertRaises(TransicaoConviteInvalida):cancelar_envio(e.envio_id,self.uid)
        finalizar_envio(r,ResultadoTransporte('enviado'))
        with self.assertRaises(TransicaoConviteInvalida):cancelar_envio(e.envio_id,self.uid)

    def test_audit_has_actor_action_and_object(self):
        a=self.first();cancelar_envio(a.envio_id,self.uid)
        b=solicitar_reenvio(self.pid,self.uid,uuid4())
        records=list(db.session.execute(select(Auditoria).order_by(Auditoria.id)).scalars())
        self.assertEqual([r.acao for r in records],['convite_primeiro_envio_solicitado','convite_cancelado','convite_reenvio_solicitado'])
        self.assertEqual([r.objeto_id for r in records],[a.envio_id,a.envio_id,b.envio_id])
        self.assertTrue(all(r.usuario_id==self.uid and r.objeto=='envio_convite_treinamento' and r.criado_em for r in records))

    def test_records_have_no_token_url_body_secret(self):
        self.first();processar_proximo_envio(TransporteFalso())
        values=' '.join(str(tuple(row)) for m in [E,T,Auditoria] for row in db.session.execute(select(m.__table__)))
        for secret in ['TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR','https://','/aprender/t/','SENHA_SECRETA','HASH_NAO_REGISTRAR']:
            self.assertNotIn(secret,values)
        for m in [E,T]:
            self.assertTrue(set(m.__table__.c.keys()).isdisjoint({'token','url','corpo','senha','segredo'}))

    def test_fake_never_connects_logs_or_retains_payload(self):
        self.first();r=reservar_proximo_envio();fake=TransporteFalso()
        with patch('socket.create_connection',side_effect=AssertionError('conexão externa')) as connect,self.assertNoLogs(level=logging.DEBUG):
            result=fake.enviar(r.dados)
        connect.assert_not_called();self.assertEqual(result.estado,'enviado')
        self.assertEqual(set(vars(fake)),{'resultado','chamadas'})

    def test_worker_revalidates_email_snapshot(self):
        e=self.first();self.read(P,self.pid).colaborador.email='alterado@example.com';db.session.commit()
        fake=TransporteFalso();self.assertEqual(processar_proximo_envio(fake),e.envio_id)
        self.assertEqual(fake.chamadas,0);self.assertEqual(self.read(E,e.envio_id).estado,'falha')
        self.assertEqual(db.session.execute(select(T.codigo_erro)).scalar_one(),'destinatario_alterado')

    def test_worker_revalidates_inactive_and_completed(self):
        e=self.first();self.read(P,self.pid).colaborador.ativo=False;db.session.commit()
        fake=TransporteFalso();processar_proximo_envio(fake);self.assertEqual(fake.chamadas,0)
        self.assertEqual(self.read(E,e.envio_id).estado,'falha')

    def test_failed_eligibility_does_not_stop_queue(self):
        a=self.first();b=self.first(pid=self.outro_pid,confirmacao=uuid4())
        self.read(P,self.pid).colaborador.ativo=False;db.session.commit()
        fake=TransporteFalso()
        self.assertEqual(processar_proximo_envio(fake),a.envio_id)
        self.assertEqual(processar_proximo_envio(fake),b.envio_id)
        self.assertIsNone(processar_proximo_envio(fake));self.assertEqual(fake.chamadas,1)

    def test_request_and_audit_rollback_together(self):
        with patch('app.utils.convites_treinamento._auditar',side_effect=RuntimeError('falha simulada')):
            with self.assertRaises(RuntimeError):self.first()
        self.assertEqual(self.count(E),0);self.assertEqual(self.count(Auditoria),0)
        self.assertTrue(self.first().criado)

    def test_isolation_no_change_to_training_state(self):
        before=self.snapshot();self.first();processar_proximo_envio(TransporteFalso())
        self.assertEqual(self.snapshot(),before)

    def test_database_unique_sequence_confirmation_and_attempt(self):
        e=self.first();row=self.read(E,e.envio_id)
        base={c.name:getattr(row,c.name) for c in E.__table__.c if c.name!='id'}
        for change in [{'identificador_confirmacao':uuid4()},{'participacao_id':self.outro_pid}]:
            with self.assertRaises(IntegrityError),db.engine.begin() as conn:conn.execute(insert(E).values(**{**base,**change}))
        r=reservar_proximo_envio();t=self.read(T,r.tentativa_id)
        base={c.name:getattr(t,c.name) for c in T.__table__.c if c.name!='id'}
        with self.assertRaises(IntegrityError),db.engine.begin() as conn:conn.execute(insert(T).values(**{**base,'message_id':uuid4()}))
        with self.assertRaises(IntegrityError),db.engine.begin() as conn:conn.execute(insert(T).values(**{**base,'numero_tentativa':2,'message_id':uuid4()}))

    def test_database_states_type_and_timestamps(self):
        e=self.first();base={'estado':'pendente','sequencia':1,'tipo':'primeiro_envio',
                            'processamento_iniciado_em':None,'finalizado_em':None}
        now=datetime.now(timezone.utc)
        for state in ESTADOS_ENVIO:
            values={**base,'estado':state}
            if state!='pendente':values['finalizado_em']=now
            if state in ('enviado','falha','resultado_indeterminado','em_processamento'):values['processamento_iniciado_em']=now
            if state=='em_processamento':values['finalizado_em']=None
            with db.engine.begin() as conn:conn.execute(update(E).where(E.id==e.envio_id).values(**values))
        for values in [{'estado':'desconhecido'},{'sequencia':0},{'tipo':'outro'}, {'tipo':'reenvio','sequencia':1}]:
            with self.assertRaises(IntegrityError),db.engine.begin() as conn:conn.execute(update(E).where(E.id==e.envio_id).values(**values))
        with self.assertRaises(IntegrityError),db.engine.begin() as conn:
            conn.execute(update(E).where(E.id==e.envio_id).values(estado='enviado',processamento_iniciado_em=None))

    def test_database_attempt_result_and_error_constraints(self):
        self.first();r=reservar_proximo_envio()
        for values in [{'resultado':'outro'},{'codigo_erro':'SENHA_SECRETA'}, {'finalizado_em':datetime.now(timezone.utc)}, {'numero_tentativa':0}]:
            with self.assertRaises(IntegrityError),db.engine.begin() as conn:conn.execute(update(T).where(T.id==r.tentativa_id).values(**values))


class ConvitesPostgresTest(ConvitesTest):
    postgres=True

    def concurrent(self, functions):
        gate=Barrier(len(functions))
        def call(fn):
            with self.app.app_context():gate.wait(timeout=10);return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            tasks=[pool.submit(call,fn) for fn in functions]
            return [f.result(timeout=20) for f in tasks]

    def test_concurrent_first_only_one(self):
        results=self.concurrent([self.first,self.first])
        self.assertEqual(sum(r.criado for r in results),1);self.assertEqual(len({r.envio_id for r in results}),1)
        self.assertEqual(self.count(E),1);self.assertEqual(self.count(Auditoria),1)

    def test_concurrent_reenvio_same_confirmation_only_one(self):
        self.first();processar_proximo_envio(TransporteFalso());conf=uuid4()
        fn=lambda:solicitar_reenvio(self.pid,self.uid,conf)
        results=self.concurrent([fn,fn]);self.assertEqual(sum(r.criado for r in results),1)
        self.assertEqual(len({r.envio_id for r in results}),1);self.assertEqual(self.count(E),2)

    def test_concurrent_reenvios_different_confirmations_one_pending(self):
        self.first();processar_proximo_envio(TransporteFalso())
        def fn():
            try:return solicitar_reenvio(self.pid,self.uid,uuid4())
            except TransicaoConviteInvalida:return None
        results=self.concurrent([fn,fn]);self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.count(E),2)

    def test_confirmation_race_across_participations(self):
        def fn(pid):
            try:return self.first(pid=pid)
            except ConfirmacaoConflitante:return None
        results=self.concurrent([lambda:fn(self.pid),lambda:fn(self.outro_pid)])
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(self.count(E),1);self.assertEqual(self.count(Auditoria),1)

    def test_two_workers_single_pending(self):
        self.first();results=self.concurrent([reservar_proximo_envio,reservar_proximo_envio])
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(self.count(T),1)

    def test_two_workers_two_pending_distinct(self):
        self.first();self.first(pid=self.outro_pid,confirmacao=uuid4())
        results=self.concurrent([reservar_proximo_envio,reservar_proximo_envio])
        self.assertTrue(all(results));self.assertEqual(len({r.dados.envio_id for r in results}),2)
        self.assertEqual(self.count(T),2)

    def test_skip_locked_does_not_wait(self):
        e=self.first();other=self.first(pid=self.outro_pid,confirmacao=uuid4())
        with Session(db.engine) as session,session.begin():
            session.execute(select(E).where(E.id==e.envio_id).with_for_update()).scalar_one()
            r=reservar_proximo_envio();self.assertEqual(r.dados.envio_id,other.envio_id)

    def test_transport_runs_without_database_lock(self):
        self.first()
        class InspectLocks:
            def enviar(inner,dados):
                with Session(db.engine) as session,session.begin():
                    session.execute(select(E).where(E.id==dados.envio_id).with_for_update(nowait=True)).scalar_one()
                    session.execute(select(P).where(P.id==dados.participacao_id).with_for_update(nowait=True)).scalar_one()
                return ResultadoTransporte('enviado')
        ident=processar_proximo_envio(InspectLocks());self.assertEqual(self.read(E,ident).estado,'enviado')

    def test_cancel_and_reserve_race_one_outcome(self):
        e=self.first()
        def cancel():
            try:return cancelar_envio(e.envio_id,self.uid)
            except TransicaoConviteInvalida:return False
        cancelled,reserved=self.concurrent([cancel,reservar_proximo_envio])
        self.assertNotEqual(cancelled,reserved is not None)
        self.assertIn(self.read(E,e.envio_id).estado,['cancelado','em_processamento'])

    def test_no_commit_of_caller_pending_changes(self):
        p=db.session.get(P,self.pid);original=p.iniciado_em;p.iniciado_em=datetime(1999,1,1,tzinfo=timezone.utc)
        self.first();self.assertTrue(db.session.is_modified(p));db.session.rollback()
        self.assertEqual(db.session.get(P,self.pid).iniciado_em,original)


class MigrationConvitesPostgresTest(unittest.TestCase):
    postgres=True
    tearDown=fixture.PainelTest.tearDown
    def setUp(self):
        fixture.PainelTest.setUp(self);db.drop_all();migrate.init_app(self.app,db)
        root=Path(__file__).resolve().parents[1]
        self.config=Config(str(root/'migrations/alembic.ini'));self.config.set_main_option('script_location',str(root/'migrations'))

    def test_upgrade_downgrade_preserves_old_data_and_constraints(self):
        command.upgrade(self.config,'c185b7e4a902');fixture.PainelTest.seed(self, treinamento_historico=True)
        user=Usuario(nome='Admin',email='admin@example.com',senha_hash='hash');db.session.add(user)
        self.people[1].email='pessoa@example.com';db.session.commit()
        pid=self.pids[0];uid=user.id
        colunas = [c for c in P.__table__.c if c.name != 'aprovado_em']
        before=list(db.session.execute(select(*colunas).order_by(P.id)));db.session.remove()
        command.upgrade(self.config,'d186b7e4a903')
        self.assertEqual(db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),'d186b7e4a903')
        # Serviços atuais requerem schema atual; a revisão histórica foi verificada acima.
        db.session.remove();command.upgrade(self.config,'head')
        result=solicitar_primeiro_envio(pid,uid,uuid4());processar_proximo_envio(TransporteFalso())
        self.assertEqual(db.session.get(E,result.envio_id).estado,'enviado')
        self.assertEqual(list(db.session.execute(select(*colunas).order_by(P.id))),before)
        with db.engine.connect() as conn:
            diffs=compare_metadata(MigrationContext.configure(conn,opts={'include_object':lambda obj,name,type_,reflected,compare_to: name in [E.__tablename__,T.__tablename__] if type_=='table' else True}),db.metadata)
            self.assertEqual(diffs,[])
        with self.assertRaises(IntegrityError),db.engine.begin() as conn:
            conn.execute(update(E).where(E.id==result.envio_id).values(estado='inválido'))
        self.assertIn('uq_convite_confirmacao',{c['name'] for c in inspect(db.engine).get_unique_constraints(E.__tablename__)})
        db.session.remove();command.downgrade(self.config,'c185b7e4a902')
        tables=inspect(db.engine).get_table_names();self.assertNotIn(E.__tablename__,tables);self.assertNotIn(T.__tablename__,tables)
        self.assertIn('feedbacks_treinamento',tables)
        self.assertEqual(list(db.session.execute(select(*colunas).order_by(P.id))),before)
        db.session.remove();command.upgrade(self.config,'head');self.assertIn(E.__tablename__,inspect(db.engine).get_table_names())
