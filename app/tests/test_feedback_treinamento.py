"""Persistência isolada; PostgreSQL opcional via ONE_PROGRESSO_TEST_DATABASE_URL."""
from sqlalchemy.orm import selectinload
from app.utils.resultados_quiz import resultado_representativo, resultados_carregados

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from sqlalchemy import event, select, insert, update, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.extensions import db, migrate
from app.models.feedback_treinamento import FeedbackTreinamento as F, FeedbackImutavel
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.resultado_quiz import ResultadoQuiz
from app.models.tempo_treinamento import TempoTreinamento
from app.models.campanha import Campanha
from app.utils.feedback_treinamento import (
    CAMPOS_NOTAS, validar_feedback, registrar_feedback,
    FeedbackInvalido, ParticipacaoNaoConcluida, ParticipacaoNaoEncontrada,
)
import test_painel_colaboradores as fixture


def dados(nota=3, **extra):
    return {**dict.fromkeys(CAMPOS_NOTAS, nota), **extra}


class FeedbackTest(unittest.TestCase):
    postgres=False
    tearDown=fixture.PainelTest.tearDown

    def setUp(self):
        fixture.PainelTest.setUp(self)
        fixture.PainelTest.seed(self)
        self.pid=self.pids[2]
        db.session.remove()

    def feedback(self, pid=None):
        db.session.expire_all()
        return db.session.execute(select(F).where(F.participacao_id== (pid or self.pid))).scalar_one_or_none()

    def test_valid_and_server_timestamp(self):
        result=registrar_feedback(self.pid,dados(comentario=' Texto '))
        self.assertTrue(result.criado)
        f=self.feedback();self.assertEqual(f.comentario,'Texto')
        self.assertIsNotNone(f.criado_em)
        if self.postgres:self.assertIsNotNone(f.criado_em.tzinfo)
        self.assertEqual(f.participacao_id,self.pid)
        self.assertEqual(f.id,result.feedback_id)

    def test_each_note_boundaries(self):
        for campo in CAMPOS_NOTAS:
            for value in [1,5,'1','5']:
                with self.subTest(campo=campo,value=value):
                    self.assertEqual(validar_feedback(dados(**{campo:value}))[campo],int(value))

    def test_each_note_invalid_and_missing(self):
        for campo in CAMPOS_NOTAS:
            for value in [0,6,'texto','1.0',1.0,True,None,'',[], '0','6']:
                with self.subTest(campo=campo,value=value):
                    with self.assertRaises(FeedbackInvalido):validar_feedback(dados(**{campo:value}))
            missing=dados();missing.pop(campo)
            with self.assertRaises(FeedbackInvalido):validar_feedback(missing)

    def test_repeated_fields(self):
        for campo in [*CAMPOS_NOTAS,'comentario']:
            payload=MultiDict(dados(comentario='Texto'));payload.add(campo,'5')
            with self.assertRaises(FeedbackInvalido):validar_feedback(payload)

    def test_empty_comment_null(self):
        for value in [None,'','  \n\t ']:
            self.assertIsNone(validar_feedback(dados(comentario=value))['comentario'])
        self.assertIsNone(validar_feedback(dados())['comentario'])
        registrar_feedback(self.pid,dados(comentario='   '));self.assertIsNone(self.feedback().comentario)

    def test_comment_limits(self):
        registrar_feedback(self.pid,dados(comentario='é'*2000))
        self.assertEqual(len(self.feedback().comentario),2000)
        for value in ['x'*2001,' '*2001,123,'nul\x00']:
            with self.assertRaises(FeedbackInvalido):validar_feedback(dados(comentario=value))

    def test_not_completed(self):
        with self.assertRaises(ParticipacaoNaoConcluida):registrar_feedback(self.pids[0],dados())
        self.assertIsNone(self.feedback(self.pids[0]))

    def test_missing_participation(self):
        for pid in [999999,0,-1,True,'1']:
            with self.assertRaises(ParticipacaoNaoEncontrada):registrar_feedback(pid,dados())

    def test_legacy_completed(self):
        result=registrar_feedback(self.pids[6],dados(5))
        self.assertTrue(result.criado)
        p=db.session.execute(db.select(P).options(selectinload(P.resultados_quiz)).where(P.id==self.pids[6])).scalar_one()
        self.assertIsNone(p.iniciado_em);self.assertIsNone(p.ultimo_modulo_id)
        self.assertIsNone(resultado_representativo(resultados_carregados(p)));self.assertEqual(p.tempos_treinamento,[])

    def test_second_submission_preserves_original(self):
        first=registrar_feedback(self.pid,dados(1,comentario='Primeiro'))
        before=self.feedback().criado_em;db.session.remove()
        second=registrar_feedback(self.pid,dados(5,comentario='Segundo'))
        self.assertFalse(second.criado);self.assertEqual(first.feedback_id,second.feedback_id)
        f=self.feedback();self.assertEqual(f.avaliacao_geral,1)
        self.assertEqual(f.comentario,'Primeiro');self.assertEqual(f.criado_em,before)
        # Registro já existente é devolvido mesmo se payload do reenvio for inválido.
        self.assertFalse(registrar_feedback(self.pid,{}).criado)

    def test_orm_edit_rejected(self):
        registrar_feedback(self.pid,dados())
        self.feedback().avaliacao_geral=5
        with self.assertRaises(FeedbackImutavel):db.session.commit()
        db.session.rollback();self.assertEqual(self.feedback().avaliacao_geral,3)

    def snapshot(self):
        return {model.__tablename__:[tuple(r) for r in db.session.execute(select(model.__table__).order_by(model.id))]
                for model in [P,ResultadoQuiz,TempoTreinamento,Campanha]}

    def test_snapshot_unchanged(self):
        before=self.snapshot();db.session.remove()
        registrar_feedback(self.pid,dados())
        self.assertEqual(self.snapshot(),before)

    def test_rollback_after_flush(self):
        def fail(session):raise SQLAlchemyError('Falha simulada antes do commit')
        event.listen(Session,'before_commit',fail)
        try:
            with self.assertRaises(SQLAlchemyError):registrar_feedback(self.pid,dados())
        finally:event.remove(Session,'before_commit',fail)
        self.assertIsNone(self.feedback())
        self.assertTrue(registrar_feedback(self.pid,dados()).criado)

    def test_invalid_payload_does_not_insert(self):
        with self.assertRaises(FeedbackInvalido):registrar_feedback(self.pid,dados(avaliacao_geral=0))
        self.assertIsNone(self.feedback())

    def test_database_constraints(self):
        for campo in CAMPOS_NOTAS:
            for value in [0,6,None]:
                with self.assertRaises(IntegrityError):
                    with db.engine.begin() as c:c.execute(insert(F).values(participacao_id=self.pid,**dados(**{campo:value})))
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as c:c.execute(insert(F).values(participacao_id=self.pid,**dados(comentario='x'*2001)))
        registrar_feedback(self.pid,dados())
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as c:c.execute(insert(F).values(participacao_id=self.pid,**dados()))


class FeedbackPostgresTest(FeedbackTest):
    postgres=True

    def test_two_concurrent_submissions(self):
        gate=Barrier(2)
        def submit(nota):
            with self.app.app_context():
                gate.wait(timeout=10)
                result=registrar_feedback(self.pid,dados(nota,comentario=str(nota)))
                return nota,result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(submit,n) for n in [1,5]]
            results=[f.result(timeout=15) for f in futures]
        self.assertEqual(sum(r.criado for _,r in results),1)
        self.assertEqual(len({r.feedback_id for _,r in results}),1)
        winner=next(n for n,r in results if r.criado)
        f=self.feedback();self.assertEqual(f.avaliacao_geral,winner)
        self.assertEqual(f.comentario,str(winner))

    def test_does_not_commit_pending_caller_changes(self):
        p=db.session.get(P,self.pid);original=p.iniciado_em
        p.iniciado_em=datetime(1999,1,1)
        registrar_feedback(self.pid,dados())
        self.assertTrue(db.session.is_modified(p))
        db.session.rollback();self.assertEqual(db.session.get(P,self.pid).iniciado_em,original)

    def test_lock_reads_latest_conclusion(self):
        # Um objeto antigo da sessão externa não define a elegibilidade.
        p=db.session.get(P,self.pids[0]);self.assertIsNone(p.concluido_em)
        with db.engine.begin() as conn:
            conn.execute(update(P).where(P.id==p.id).values(concluido_em=datetime(2026,1,1)))
        self.assertTrue(registrar_feedback(p.id,dados()).criado)


class MigrationPostgresTest(unittest.TestCase):
    postgres=True
    tearDown=fixture.PainelTest.tearDown

    def setUp(self):
        fixture.PainelTest.setUp(self)
        db.drop_all()
        migrate.init_app(self.app,db)
        root=Path(__file__).resolve().parents[1]
        self.config=Config(str(root/'migrations/alembic.ini'))
        self.config.set_main_option('script_location',str(root/'migrations'))

    def test_full_upgrade_downgrade_constraints_and_immutable_sql(self):
        command.upgrade(self.config,'b182a6d4e901')
        self.assertNotIn('feedbacks_treinamento',inspect(db.engine).get_table_names())
        fixture.PainelTest.seed(self, treinamento_historico=True)
        colunas = [c for c in P.__table__.c if c.name != 'aprovado_em']
        before=list(db.session.execute(select(*colunas).order_by(P.id)))
        db.session.remove()
        command.upgrade(self.config,'c185b7e4a902')
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(),['f192d2e4a911'])
        self.assertEqual(db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),'c185b7e4a902')
        db.session.remove();command.upgrade(self.config,'head')
        result=registrar_feedback(self.pids[2],dados())
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as c:c.execute(update(F).where(F.id==result.feedback_id).values(avaliacao_geral=5))
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as c:c.execute(insert(F).values(participacao_id=self.pids[2],**dados()))
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as c:c.execute(insert(F).values(participacao_id=self.pids[6],**dados(comentario='x'*2001)))
        self.assertEqual(list(db.session.execute(select(*colunas).order_by(P.id))),before)
        db.session.remove()
        command.downgrade(self.config,'b182a6d4e901')
        self.assertNotIn('feedbacks_treinamento',inspect(db.engine).get_table_names())
        self.assertEqual(db.session.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),'b182a6d4e901')
        self.assertEqual(db.session.execute(text("SELECT count(*) FROM pg_proc WHERE proname='impedir_edicao_feedback_treinamento' AND pronamespace=current_schema()::regnamespace")).scalar_one(),0)
        db.session.remove()
        command.upgrade(self.config,'head')
        self.assertIn('feedbacks_treinamento',inspect(db.engine).get_table_names())
