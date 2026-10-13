import logging
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote
from uuid import uuid4
from app.utils.mail_convites import *
from app.utils.transporte_convites import DadosEnvio, TransporteFalso

ENV = dict(BASE_URL='https://treinamentos.oneinteligencia.com.br/', MAIL_ENABLED='true',
           MAIL_FROM_ADDRESS='treinamentos@example.com', SMTP_HOST='smtp.example.com')

class MailTest(unittest.TestCase):
    def cfg(self, **changes):
        return ConfiguracaoMail.do_ambiente({**ENV, **changes})

    def dados(self, recipient='pessoa@example.com'):
        return DadosEnvio(1, 2, recipient, uuid4())

    def message(self, cfg=None, nome='Maria', titulo='Segurança', token='TOKEN_PRIVADO'):
        return compor_mensagem(cfg or self.cfg(), self.dados(), nome, titulo, token)

    def test_base_valid(self):
        for base in ['https://treinamentos.oneinteligencia.com.br/', 'http://localhost:8080', 'https://[::1]:443']:
            self.assertEqual(validar_base_url(base), base.rstrip('/'))

    def test_base_invalid(self):
        for base in ['example.com', 'https://u:p@example.com', 'https://example.com?q=1', 'https://example.com#x',
                     'https://example.com\n', 'https://example.com/extra', 'https://', 'ftp://example.com',
                     'https://example.com:65536', 'https://example.com:', 'https://example.com?',
                     'https://exa mple.com', 'https://example.com\\evil', 'https://-bad.com', 'https://example.com/%0a']:
            with self.subTest(base=base), self.assertRaises(ConfiguracaoMailInvalida):
                validar_base_url(base)

    def test_link_preserves_token(self):
        for token in ['TOKEN_PRIVADO', 'a/b?x#% ç', 'AbC_-123']:
            link = construir_link(ENV['BASE_URL'], token)
            self.assertEqual(unquote(link.split('/aprender/t/')[1]), token)
            self.assertEqual(link.split('/aprender/t/')[0], ENV['BASE_URL'].rstrip('/'))
        self.assertEqual(construir_link(ENV['BASE_URL'], 'abc'), ENV['BASE_URL'] + 'aprender/t/abc')

    def test_institutional_content(self):
        msg = self.message()
        for text in ['Olá, Maria.', 'Segurança', 'plataforma de treinamentos da ONE', 'Não compartilhe este endereço.',
                     'Esta mensagem não solicita senha ou envio de credenciais.', '/aprender/t/TOKEN_PRIVADO']:
            self.assertIn(text, msg.get_content())
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertEqual(msg['Subject'], 'ONE — Convite de treinamento')

    def test_header_injection(self):
        for field in ['nome', 'titulo', 'token']:
            with self.subTest(field=field), self.assertRaises(ConfiguracaoMailInvalida):
                self.message(**{field: 'x\r\nBcc: evil@example.com'})
        for key in ['MAIL_FROM_ADDRESS', 'MAIL_FROM_NAME', 'MAIL_REPLY_TO', 'MAIL_TEST_RECIPIENTS']:
            with self.subTest(key=key), self.assertRaises(ConfiguracaoMailInvalida):
                self.cfg(**{key: 'a@example.com\r\nBcc: evil@example.com'})
        for value in ['invalid', 'a@example.com\nBcc: b@example.com', 'Nome <a@example.com>']:
            with self.assertRaises(ConfiguracaoMailInvalida):
                compor_mensagem(self.cfg(), self.dados(value), 'Maria', 'Curso', 'TOKEN_PRIVADO')

    def test_defaults_disabled_and_fake(self):
        cfg = ConfiguracaoMail.do_ambiente({})
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.backend, 'fake')
        with patch('socket.create_connection') as network, self.assertRaisesRegex(MailBloqueado, 'mail_desabilitado'):
            processar_convite_configurado(TransporteConfigurado(cfg))
        network.assert_not_called()

    def test_disabled_smtp_no_network(self):
        cfg = self.cfg(MAIL_BACKEND='smtp', MAIL_ENABLED='false')
        factory = Mock()
        with self.assertRaisesRegex(MailBloqueado, 'mail_desabilitado'):
            AdaptadorSMTP(cfg, fabrica_simulada=factory).enviar_mensagem(self.message(cfg), 'pessoa@example.com')
        factory.assert_not_called()

    def test_fake_no_network_or_payload(self):
        fake = TransporteFalso()
        with patch('socket.create_connection') as network, self.assertNoLogs(level=logging.DEBUG):
            self.assertEqual(fake.enviar(self.dados()).estado, 'enviado')
        network.assert_not_called()
        self.assertEqual(set(vars(fake)), {'resultado', 'chamadas'})

    def test_missing_smtp_configuration(self):
        with self.assertRaisesRegex(ConfiguracaoMailInvalida, 'BASE_URL, MAIL_FROM_ADDRESS, SMTP_HOST'):
            ConfiguracaoMail.do_ambiente({'MAIL_BACKEND': 'smtp'})
        with self.assertRaisesRegex(ConfiguracaoMailInvalida, 'SMTP_USERNAME, SMTP_PASSWORD'):
            self.cfg(MAIL_BACKEND='smtp', SMTP_AUTH_MODE='password')

    def test_real_smtp_always_blocked(self):
        cfg = self.cfg(MAIL_BACKEND='smtp')
        with patch('smtplib.SMTP') as smtp, patch('smtplib.SMTP_SSL') as tls:
            with self.assertRaisesRegex(MailBloqueado, 'smtp_real_bloqueado_18_6c'):
                AdaptadorSMTP(cfg).enviar_mensagem(self.message(cfg), 'pessoa@example.com')
            with patch('app.utils.convites_treinamento.reservar_proximo_envio') as reserve:
                with self.assertRaises(MailBloqueado):
                    processar_convite_configurado(TransporteConfigurado(cfg))
                reserve.assert_not_called()
            smtp.assert_not_called(); tls.assert_not_called()

    def test_starttls_direct_tls_auth_and_timeout(self):
        for security in ['starttls', 'tls']:
            for auth in ['none', 'password']:
                cfg = self.cfg(MAIL_BACKEND='smtp', SMTP_SECURITY=security, SMTP_AUTH_MODE=auth,
                               SMTP_USERNAME='usuario', SMTP_PASSWORD='SENHA_PRIVADA', MAIL_TIMEOUT_SECONDS='3')
                from unittest.mock import MagicMock
                factory = MagicMock(); client = factory.return_value.__enter__.return_value
                client.send_message.return_value = {}
                with self.assertNoLogs(level=logging.DEBUG):
                    result = AdaptadorSMTP(cfg, fabrica_simulada=factory).enviar_mensagem(self.message(cfg), 'pessoa@example.com')
                self.assertEqual(result.estado, 'enviado')
                self.assertEqual(factory.call_args.kwargs['timeout'], 3)
                if security == 'starttls':
                    context = client.starttls.call_args.kwargs['context']
                    self.assertEqual(client.ehlo.call_count, 2)
                else:
                    context = factory.call_args.kwargs['context']; client.starttls.assert_not_called()
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED); self.assertTrue(context.check_hostname)
                self.assertEqual(client.login.call_count, int(auth == 'password'))
                client.set_debuglevel.assert_not_called()
                self.assertEqual(client.send_message.call_args.kwargs['to_addrs'], ['pessoa@example.com'])

    def test_timeout_sanitized(self):
        from unittest.mock import MagicMock
        factory = MagicMock(); factory.return_value.__enter__.return_value.send_message.side_effect = TimeoutError('SENHA_PRIVADA TOKEN_PRIVADO CORPO_PRIVADO')
        cfg = self.cfg(MAIL_BACKEND='smtp')
        with self.assertNoLogs(level=logging.DEBUG):
            result = AdaptadorSMTP(cfg, fabrica_simulada=factory).enviar_mensagem(self.message(cfg), 'pessoa@example.com')
        self.assertEqual((result.estado, result.codigo_erro), ('resultado_indeterminado', 'excecao_transporte'))

    def test_allowlist_no_replacement_logs_only_code(self):
        cfg = self.cfg(MAIL_BACKEND='smtp', MAIL_TEST_RECIPIENTS='permitido@example.com')
        factory = Mock()
        with self.assertLogs('app.utils.mail_convites', level='WARNING') as logs, self.assertRaises(MailBloqueado):
            AdaptadorSMTP(cfg, fabrica_simulada=factory).enviar_mensagem(self.message(cfg), 'pessoa@example.com')
        factory.assert_not_called()
        self.assertEqual(logs.records[0].getMessage(), 'mail_destinatario_bloqueado')
        self.assertNotIn('pessoa', str(logs.output))
        from unittest.mock import MagicMock
        factory = MagicMock(); factory.return_value.__enter__.return_value.send_message.return_value = {}
        AdaptadorSMTP(cfg, fabrica_simulada=factory).enviar_mensagem(self.message(cfg), 'permitido@example.com')
        self.assertEqual(factory.return_value.__enter__.return_value.send_message.call_args.kwargs['to_addrs'], ['permitido@example.com'])

    def test_invalid_configuration(self):
        for key, value in [('MAIL_ENABLED','yes'), ('MAIL_BACKEND','other'), ('SMTP_SECURITY','none'),
                           ('SMTP_AUTH_MODE','other'), ('SMTP_PORT','65536'), ('MAIL_TIMEOUT_SECONDS','nan'),
                           ('MAIL_BATCH_SIZE','0'), ('MAIL_RATE_LIMIT','-1')]:
            with self.subTest(key=key), self.assertRaises(ConfiguracaoMailInvalida):
                self.cfg(**{key:value})

class MailIntegracaoTest(unittest.TestCase):
    postgres = False
    import test_convites_treinamento as fixture
    setUp = fixture.ConvitesTest.setUp
    tearDown = fixture.ConvitesTest.tearDown

    def test_configured_fake_composes_existing_token_and_reenvio(self):
        from app.utils.convites_treinamento import solicitar_primeiro_envio, solicitar_reenvio
        from app.models.participacao_treinamento import ParticipacaoTreinamento as P
        cfg = ConfiguracaoMail.do_ambiente(ENV)
        transport = TransporteConfigurado(cfg)
        solicitar_primeiro_envio(self.pid, self.uid, self.confirmacao)
        original = db.session.get(P, self.pid).token
        with patch('socket.create_connection') as network, patch('app.utils.mail_convites.compor_mensagem', wraps=compor_mensagem) as compose:
            processar_convite_configurado(transport)
            solicitar_reenvio(self.pid, self.uid, uuid4())
            processar_convite_configurado(transport)
        self.assertEqual([c.args[-1] for c in compose.call_args_list], [original, original])
        db.session.expire_all()
        self.assertEqual(db.session.get(P, self.pid).token, original)
        self.assertEqual(transport.fake.chamadas, 2)
        network.assert_not_called()

    def test_disabled_does_not_reserve(self):
        from app.utils.convites_treinamento import solicitar_primeiro_envio
        from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
        result = solicitar_primeiro_envio(self.pid, self.uid, self.confirmacao)
        cfg = ConfiguracaoMail.do_ambiente({**ENV, 'MAIL_ENABLED':'false'})
        with self.assertRaises(MailBloqueado):
            processar_convite_configurado(TransporteConfigurado(cfg))
        self.assertEqual(db.session.get(E, result.envio_id).estado, 'pendente')

    def test_allowlist_fake_fails_without_transport(self):
        from app.utils.convites_treinamento import solicitar_primeiro_envio
        from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
        result = solicitar_primeiro_envio(self.pid, self.uid, self.confirmacao)
        cfg = ConfiguracaoMail.do_ambiente({**ENV, 'MAIL_TEST_RECIPIENTS':'permitido@example.com'})
        transport = TransporteConfigurado(cfg)
        with self.assertLogs('app.utils.mail_convites', level='WARNING'):
            processar_convite_configurado(transport)
        self.assertEqual(transport.fake.chamadas, 0)
        self.assertEqual(db.session.get(E, result.envio_id).estado, 'falha')
