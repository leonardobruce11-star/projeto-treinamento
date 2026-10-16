"""Fluxo real de formulários/CSRF e CLI; PostgreSQL isolado para concorrência."""
import html
import logging
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import patch
from flask_login import LoginManager
from jinja2 import FileSystemLoader, StrictUndefined
from sqlalchemy import select, inspect
from sqlalchemy.orm import Session
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.extensions import db, csrf
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
from app.models.tentativa_envio_convite import TentativaEnvioConvite as T
from app.models.confirmacao_convites import ConfirmacaoConvites as R
from app.models.usuario import Usuario
from app.models.auditoria import Auditoria
from app.models.campanha import Campanha
from app.convites.routes import convites_bp, assinador
from app.convites.service import confirmar
from app.convites.cli import registrar_cli, processar_fila
from app.utils.mail_convites import ConfiguracaoMail, MailBloqueado
from app.utils.convites_treinamento import solicitar_primeiro_envio
import test_convites_treinamento as fixture

ENV = dict(BASE_URL='https://treinamentos.example.com', MAIL_ENABLED='true',
           MAIL_FROM_ADDRESS='treinamentos@example.com', MAIL_FROM_NAME='ONE', MAIL_BATCH_SIZE='2')


class AdminConvitesTest(unittest.TestCase):
    postgres = False

    def setUp(self):
        self.logger_anterior = logging.Logger.manager.loggerDict.get('app.utils.mail_convites')
        fixture.ConvitesTest.setUp(self)
        self.app.config.update(TESTING=True, SECRET_KEY='isolated-key', WTF_CSRF_ENABLED=True)
        login = LoginManager(self.app)
        login.login_view = 'auth.login'
        login.user_loader(lambda uid: db.session.get(Usuario, int(uid)))
        csrf.init_app(self.app)
        self.app.register_blueprint(convites_bp)
        registrar_cli(self.app)
        self.app.jinja_loader = FileSystemLoader(str(Path(__file__).resolve().parents[1] / 'app/templates'))
        self.app.jinja_env.undefined = StrictUndefined
        for name in ['dashboard.index', 'campanhas.listar', 'departamentos.listar', 'colaboradores.listar',
                     'grupos.listar', 'auth.logout', 'auth.login', 'categorias_treinamento.listar',
                     'cenarios.listar', 'templates_email.listar', 'landing_pages.listar',
                     'gophish.index', 'treinamentos.listar', 'treinamentos.feedback',
                     'certificados_admin.listar']:
            self.app.add_url_rule('/_layout/' + name, endpoint=name, view_func=lambda: 'layout')
        self.cfg = ConfiguracaoMail.do_ambiente(ENV)
        self.app.extensions['mail_convites_config'] = self.cfg
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.uid)
            sess['_fresh'] = True

    def tearDown(self):
        fixture.ConvitesTest.tearDown(self)
        # Alembic usa fileConfig(disable_existing_loggers=True) nas fixtures
        # antigas. Não deixar um logger criado aqui afetar testes posteriores.
        if self.logger_anterior is None:
            logging.Logger.manager.loggerDict.pop('app.utils.mail_convites', None)

    def count(self, model):
        db.session.expire_all()
        return db.session.execute(select(db.func.count()).select_from(model)).scalar_one()

    def field(self, response, name):
        values = re.findall(r'name="' + name + r'" value="([^"]+)"', response.get_data(as_text=True))
        self.assertTrue(values, response.get_data(as_text=True))
        return html.unescape(values[0])

    def page(self, pid=None, **scope):
        return self.client.get('/convites/', query_string=scope or dict(participacao_id=pid or self.pid))

    def preview(self, ids=None, tipo='primeiro_envio', choices=None):
        pages = [self.page(pid) for pid in (ids or [self.pid])]
        data = dict(csrf_token=self.field(pages[0], 'csrf_token'), tipo=tipo,
                    escolha=choices or [self.field(p, 'escolha') for p in pages])
        if ids and len(ids) > 1 and choices is None:
            # Usar um treinamento para garantir o mesmo escopo do lote.
            tid = db.session.get(P, self.pid).treinamento_id
            page = self.page(treinamento_id=tid)
            signed = re.findall(r'name="escolha" value="([^"]+)"', page.get_data(as_text=True))
            choices = {}
            for value in signed:
                raw = html.unescape(value)
                choices[assinador().loads(raw)['item']['id']] = raw
            data['escolha'] = [choices[i] for i in ids]
        response = self.client.post('/convites/selecionar', data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def confirm(self, preview):
        return self.client.post('/convites/confirmar', data=dict(
            csrf_token=self.field(preview, 'csrf_token'), confirmacao=self.field(preview, 'confirmacao')))

    def mutate(self, fn):
        db.session.expire_all()
        fn(db.session.get(P, self.pid))
        db.session.commit()
        db.session.remove()
        from flask import g
        g.pop('_login_user', None)

    def test_individual_first_status_and_human_preview(self):
        page = self.page()
        self.assertEqual(page.status_code, 200)
        self.assertIn('Enviar convite', page.get_data(as_text=True))
        self.assertIn('Não enviado', page.get_data(as_text=True))
        preview = self.preview()
        text = preview.get_data(as_text=True)
        for expected in ['Selecionadas: 1', 'Elegíveis: 1', 'Excluídas: 0', 'pessoa@example.com',
                         'Mesmo treinamento', 'treinamentos@example.com', 'ONE — Convite de treinamento',
                         '[link individual oculto]']:
            self.assertIn(expected, text)
        self.assertEqual(self.count(E), 0)
        self.assertEqual(self.confirm(preview).status_code, 200)
        self.assertEqual(self.count(E), 1)
        self.assertEqual(self.count(R), 1)
        self.assertIn('Pendente', self.page().get_data(as_text=True))
        self.assertEqual(processar_fila(self.cfg), 1)
        self.assertIn('Enviado', self.page().get_data(as_text=True))

    def test_reenvio_separate_action_history(self):
        self.confirm(self.preview())
        processar_fila(self.cfg, resultado_fake='resultado_indeterminado')
        page = self.page().get_data(as_text=True)
        self.assertIn('Reenviar convite', page)
        self.assertIn('Resultado indeterminado', page)
        initial = self.preview(tipo='primeiro_envio')
        self.assertIn('Já existe envio', initial.get_data(as_text=True))
        self.confirm(initial)
        self.assertEqual(self.count(E), 1)
        preview = self.preview(tipo='reenvio')
        self.assertIn('mensagem duplicada', preview.get_data(as_text=True))
        self.assertEqual(self.confirm(preview).status_code, 200)
        self.assertEqual(self.count(E), 2)
        rows = list(db.session.execute(select(E).order_by(E.sequencia)).scalars())
        self.assertEqual([(e.sequencia, e.tipo) for e in rows], [(1, 'primeiro_envio'), (2, 'reenvio')])
        history = self.page().get_data(as_text=True)
        for expected in ['Primeiro envio', 'Reenvio', 'Destinatário utilizado', 'Solicitado em',
                         'Processado em', 'transporte_falso_indeterminado']:
            self.assertIn(expected, history)

    def test_batch_existing_participations_and_pilot_unchanged(self):
        c = Campanha(nome='Piloto', status='piloto_pronto', treinamento_id=db.session.get(P, self.pid).treinamento_id)
        db.session.add(c); db.session.flush()
        cid = c.id
        for pid in [self.pid, self.outro_pid]:
            db.session.get(P, pid).campanha_id = cid
        db.session.commit()
        before = self.count(P)
        page = self.page(campanha_id=cid)
        choices = [html.unescape(v) for v in re.findall(r'type="checkbox" name="escolha" value="([^"]+)"', page.get_data(as_text=True))]
        preview = self.preview(choices=choices)
        self.assertIn('Selecionadas: 2', preview.get_data(as_text=True))
        self.assertEqual(self.confirm(preview).status_code, 200)
        self.assertEqual(self.count(E), 2)
        self.assertEqual(self.count(P), before)
        self.assertEqual(db.session.get(Campanha, cid).status, 'piloto_pronto')
        self.assertEqual(processar_fila(self.cfg), 2)
        self.assertEqual(self.count(T), 2)

    def test_ineligible_before_preview_and_exclusion_receipt(self):
        page = self.page()
        choice = self.field(page, 'escolha')
        self.mutate(lambda p: setattr(p.colaborador, 'ativo', False))
        preview = self.preview(choices=[choice])
        self.assertIn('Colaborador inativo', preview.get_data(as_text=True))
        self.assertIn('Excluídas: 1', preview.get_data(as_text=True))
        self.confirm(preview)
        self.mutate(lambda p: setattr(p.colaborador, 'ativo', True))
        self.confirm(preview)
        self.assertEqual(self.count(E), 0)
        self.assertEqual(self.count(R), 1)

    def test_email_changes_selection_to_preview(self):
        choice = self.field(self.page(), 'escolha')
        self.mutate(lambda p: setattr(p.colaborador, 'email', 'novo@example.com'))
        preview = self.preview(choices=[choice])
        self.assertIn('E-mail alterado', preview.get_data(as_text=True))
        self.confirm(preview)
        self.assertEqual(self.count(E), 0)

    def test_email_changes_preview_to_confirm(self):
        preview = self.preview()
        self.mutate(lambda p: setattr(p.colaborador, 'email', 'novo@example.com'))
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200)
        self.assertIn('E-mail alterado', response.get_data(as_text=True))
        self.assertEqual(self.count(E), 0)

    def test_all_server_eligibility_checks(self):
        mutations = [lambda p: setattr(p.colaborador, 'ativo', False),
                     lambda p: setattr(p.treinamento, 'ativo', False),
                     lambda p: setattr(p.colaborador, 'email', 'invalido'),
                     lambda p: setattr(p, 'concluido_em', datetime.now(timezone.utc)),
                     lambda p: setattr(p.treinamento, 'titulo', 'Alterado'),
                     lambda p: db.session.delete(p)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                preview = self.preview()
                self.mutate(mutate)
                self.assertEqual(self.confirm(preview).status_code, 200)
                self.assertEqual(self.count(E), 0)
                # Última mutação remove a participação; as anteriores são revertidas.
                if mutate != mutations[-1]:
                    def restore(p):
                        p.colaborador.ativo = True; p.treinamento.ativo = True
                        p.colaborador.email = 'pessoa@example.com'; p.concluido_em = None
                        p.treinamento.titulo = 'Mesmo treinamento'
                    self.mutate(restore)

    def test_double_click_confirmation_after_worker_and_cancellation(self):
        preview = self.preview()
        first = self.confirm(preview)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.confirm(preview).data, first.data)
        processar_fila(self.cfg)
        self.confirm(preview)
        resend = self.preview(tipo='reenvio')
        self.confirm(resend)
        eid = db.session.execute(select(E.id).where(E.sequencia == 2)).scalar_one()
        self.client.post(f'/convites/{eid}/cancelar', data=dict(csrf_token=self.field(resend, 'csrf_token')))
        self.confirm(resend)
        self.assertEqual(self.count(E), 2)
        self.assertEqual(self.count(R), 2)
        self.assertEqual(self.count(Auditoria), 5)

    def test_csrf_all_posts_and_no_get_mutations(self):
        preview = self.preview()
        for url, data in [('/convites/selecionar', {}), ('/convites/confirmar', {'confirmacao': self.field(preview, 'confirmacao')}),
                          ('/convites/1/cancelar', {})]:
            self.assertEqual(self.client.post(url, data=data).status_code, 400)
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.count(E), 0)

    def test_cancel_pending_audit_history_processing_blocked(self):
        preview = self.preview(); self.confirm(preview)
        eid = db.session.execute(select(E.id)).scalar_one()
        csrf_value = self.field(preview, 'csrf_token')
        url = f'/convites/{eid}/cancelar'
        self.assertEqual(self.client.post(url, data=dict(csrf_token=csrf_value)).status_code, 200)
        self.assertEqual(self.client.post(url, data=dict(csrf_token=csrf_value)).status_code, 200)
        self.assertEqual(self.count(E), 1)
        self.assertEqual(self.count(Auditoria), 3)
        self.assertIn('Cancelado', self.page().get_data(as_text=True))
        self.assertEqual(processar_fila(self.cfg), 0)
        self.confirm(self.preview(tipo='reenvio')); processar_fila(self.cfg)
        eid = db.session.execute(select(E.id).where(E.sequencia == 2)).scalar_one()
        self.assertEqual(self.client.post(f'/convites/{eid}/cancelar', data=dict(csrf_token=csrf_value)).status_code, 409)

    def test_worker_empty_one_many_batch_size_limit_and_cli(self):
        runner = self.app.test_cli_runner()
        self.assertIn('0 solicitação', runner.invoke(args=['convites', 'processar']).output)
        self.confirm(self.preview(ids=[self.pid, self.outro_pid]))
        result = runner.invoke(args=['convites', 'processar', '--limite', '1'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('1 solicitação', result.output)
        self.assertEqual(self.count(T), 1)
        self.assertEqual(processar_fila(replace(self.cfg, batch_size=1), limite=999), 1)
        self.assertEqual(processar_fila(self.cfg), 0)
        self.assertNotEqual(runner.invoke(args=['convites', 'processar', '--limite', '0']).exit_code, 0)

    def test_mail_batch_size_with_more_queued_than_limit(self):
        extra = self.pids[4]
        p = db.session.get(P, extra)
        p.colaborador.email = 'terceira@example.com'
        db.session.commit()
        self.confirm(self.preview(ids=[self.pid, self.outro_pid, extra]))
        self.assertEqual(self.count(E), 3)
        self.assertEqual(processar_fila(self.cfg, limite=999), 2)
        self.assertEqual(self.count(T), 2)
        db.session.expire_all()
        self.assertEqual(db.session.execute(select(db.func.count()).select_from(E)
            .where(E.estado == 'pendente')).scalar_one(), 1)
        self.assertEqual(processar_fila(self.cfg), 1)

    def test_partial_batch_revalidation_and_stable_double_click(self):
        preview = self.preview(ids=[self.pid, self.outro_pid])
        self.mutate(lambda p: setattr(p.colaborador, 'email', 'novo@example.com'))
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Excluídas: 1', response.get_data(as_text=True))
        self.assertEqual(self.count(E), 1)
        self.assertEqual(db.session.execute(select(E.participacao_id)).scalar_one(), self.outro_pid)
        self.mutate(lambda p: setattr(p.colaborador, 'email', 'pessoa@example.com'))
        self.assertEqual(self.confirm(preview).data, response.data)
        self.assertEqual(self.count(E), 1)

    def test_atomic_receipt_requests_audit_rollback(self):
        preview = self.preview(ids=[self.pid, self.outro_pid])
        with patch('app.utils.convites_treinamento._auditar', side_effect=RuntimeError('falha controlada')):
            with self.assertRaises(RuntimeError):
                self.confirm(preview)
        self.assertEqual(self.count(E), 0)
        self.assertEqual(self.count(R), 0)
        self.assertEqual(self.count(Auditoria), 0)
        self.assertEqual(self.confirm(preview).status_code, 200)
        self.assertEqual(self.count(E), 2)

    def test_processing_status_blocks_cancel_and_resend(self):
        from app.utils.convites_treinamento import reservar_proximo_envio
        preview = self.preview(); self.confirm(preview)
        reserva = reservar_proximo_envio()
        page = self.page().get_data(as_text=True)
        self.assertIn('Em processamento', page)
        self.assertNotIn('Cancelar solicitação pendente', page)
        self.assertEqual(self.client.post(f'/convites/{reserva.dados.envio_id}/cancelar', data=dict(
            csrf_token=self.field(preview, 'csrf_token'))).status_code, 409)
        resend = self.preview(tipo='reenvio')
        self.assertIn('Existe envio pendente ou em processamento', resend.get_data(as_text=True))
        self.confirm(resend)
        self.assertEqual(self.count(E), 1)

    def test_signature_actor_expiry_and_scope(self):
        preview = self.preview()
        envelope = assinador().loads(self.field(preview, 'confirmacao'))
        envelope['usuario'] = self.uid + 1
        signed = assinador().dumps(envelope)
        data = dict(csrf_token=self.field(preview, 'csrf_token'), confirmacao=signed)
        self.assertEqual(self.client.post('/convites/confirmar', data=data).status_code, 400)
        data['confirmacao'] = self.field(preview, 'confirmacao')
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=1):
            expired = assinador().dumps({**envelope, 'usuario': self.uid})
        data['confirmacao'] = expired
        self.assertEqual(self.client.post('/convites/confirmar', data=data).status_code, 400)
        choices = [self.field(self.page(pid), 'escolha') for pid in [self.pid, self.outro_pid]]
        self.assertEqual(self.client.post('/convites/selecionar', data=dict(
            csrf_token=data['csrf_token'], tipo='primeiro_envio', escolha=choices)).status_code, 400)
        self.assertEqual(self.count(E), 0)

    def test_worker_fake_failure_and_indeterminate_no_retry(self):
        self.confirm(self.preview(ids=[self.pid, self.outro_pid]))
        for result in ['falha', 'resultado_indeterminado']:
            self.assertEqual(processar_fila(self.cfg, limite=1, resultado_fake=result), 1)
        db.session.expire_all()
        self.assertEqual(set(db.session.execute(select(E.estado)).scalars()), {'falha', 'resultado_indeterminado'})
        self.assertEqual(processar_fila(self.cfg), 0)

    def test_worker_respects_configuration_fake_only_and_allowlist(self):
        self.confirm(self.preview())
        for cfg in [replace(self.cfg, enabled=False), replace(self.cfg, backend='smtp')]:
            with self.assertRaises(MailBloqueado):
                processar_fila(cfg)
        self.assertEqual(self.count(T), 0)
        self.assertEqual(processar_fila(replace(self.cfg, test_recipients=frozenset({'other@example.com'}))), 1)
        self.assertEqual(db.session.execute(select(E.estado)).scalar_one(), 'falha')

    def test_admin_authentication_active_role_and_signed_tampering(self):
        preview = self.preview()
        value = self.field(preview, 'confirmacao')
        response = self.client.post('/convites/confirmar', data=dict(csrf_token=self.field(preview, 'csrf_token'), confirmacao=value+'x'))
        self.assertEqual(response.status_code, 400)
        user = db.session.get(Usuario, self.uid); user.perfil = 'operador'; db.session.commit()
        self.assertEqual(self.page().status_code, 403)
        user.perfil = 'administrador'; user.ativo = False; db.session.commit()
        self.assertEqual(self.page().status_code, 403)
        with self.client.session_transaction() as sess:
            sess.pop('_user_id', None)
        # Remove o cache do usuário associado ao app_context da fixture.
        from flask import g
        g.pop('_login_user', None)
        self.assertEqual(self.page().status_code, 302)
        self.assertEqual(self.count(E), 0)

    def test_sender_changed_requires_new_confirmation(self):
        preview = self.preview()
        self.app.extensions['mail_convites_config'] = replace(self.cfg, from_address='outro@example.com')
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.count(R), 0)
        self.assertEqual(self.count(E), 0)

    def test_no_token_link_secret_on_pages_receipts_logs_no_network(self):
        with patch('socket.create_connection') as network, patch('smtplib.SMTP') as smtp, patch('smtplib.SMTP_SSL') as tls, self.assertNoLogs(level='WARNING'):
            page = self.page(); preview = self.preview(); result = self.confirm(preview)
            processar_fila(self.cfg)
            history = self.page()
        for response in [page, preview, result, history]:
            for secret in ['TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR', '/aprender/t/', 'HASH_NAO_REGISTRAR', 'SMTP_PASSWORD']:
                self.assertNotIn(secret, response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        receipt = db.session.execute(select(R)).scalar_one()
        self.assertNotIn('pessoa@example.com', str(receipt.resultado))
        network.assert_not_called(); smtp.assert_not_called(); tls.assert_not_called()

    def test_factory_registers_blueprint_and_cli_without_dotenv_or_network(self):
        import os
        from app import create_app
        with patch('app.load_dotenv'), patch.dict(os.environ, {
            **ENV, 'SECRET_KEY': 'isolated-factory', 'DATABASE_URL': 'sqlite:///:memory:'
        }, clear=True), patch('socket.create_connection') as network:
            app = create_app()
        self.assertIn('convites.listar', app.view_functions)
        self.assertIn('convites', app.cli.commands)
        network.assert_not_called()


class AdminConvitesPostgresTest(AdminConvitesTest):
    postgres = True

    def concurrent(self, funcs):
        gate = Barrier(len(funcs))
        def call(fn):
            with self.app.app_context():
                gate.wait(timeout=10)
                return fn()
        with ThreadPoolExecutor(max_workers=len(funcs)) as pool:
            futures = [pool.submit(call, f) for f in funcs]
            return [f.result(timeout=20) for f in futures]

    def test_two_workers_and_no_lock_during_transport(self):
        self.confirm(self.preview(ids=[self.pid, self.outro_pid]))
        from app.utils.transporte_convites import TransporteFalso
        original = TransporteFalso.enviar
        def send(fake, dados):
            with Session(db.engine) as session, session.begin():
                session.execute(select(E).where(E.id == dados.envio_id).with_for_update(nowait=True)).scalar_one()
            return original(fake, dados)
        with patch.object(TransporteFalso, 'enviar', send):
            results = self.concurrent([lambda: processar_fila(self.cfg), lambda: processar_fila(self.cfg)])
        self.assertEqual(sum(results), 2)
        self.assertEqual(self.count(T), 2)
        self.assertEqual(set(db.session.execute(select(E.estado)).scalars()), {'enviado'})

    def test_concurrent_confirmation_one_receipt_and_audit(self):
        preview = self.preview(ids=[self.pid, self.outro_pid])
        envelope = assinador().loads(self.field(preview, 'confirmacao'))
        results = self.concurrent([lambda: confirmar(envelope, self.uid, self.cfg)] * 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count(E), 2)
        self.assertEqual(self.count(R), 1)
        self.assertEqual(self.count(Auditoria), 3)


class MigrationAdminConvitesTest(unittest.TestCase):
    postgres = True
    setUp = fixture.MigrationConvitesPostgresTest.setUp
    tearDown = fixture.MigrationConvitesPostgresTest.tearDown
    def test_receipt_migration_metadata_and_roundtrip(self):
        command.upgrade(self.config, 'head')
        with db.engine.connect() as conn:
            names = ['confirmacoes_convites']
            diff = compare_metadata(MigrationContext.configure(conn, opts={
                'include_object': lambda obj, name, type_, reflected, compare_to:
                    name in names if type_ == 'table' else True}), db.metadata)
            self.assertEqual(diff, [])
        db.session.remove(); command.downgrade(self.config, 'd186b7e4a903')
        self.assertNotIn('confirmacoes_convites', inspect(db.engine).get_table_names())
        self.assertIn('envios_convite_treinamento', inspect(db.engine).get_table_names())
        command.upgrade(self.config, 'head')
        self.assertIn('confirmacoes_convites', inspect(db.engine).get_table_names())
