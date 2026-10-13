"""Factory de mail: fallback só desabilitado; banco e credenciais sintéticos."""
import io
import logging
import os
import traceback
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.utils.mail_convites import (
    ConfiguracaoMail, ConfiguracaoMailInvalida, TransporteConfigurado, MailBloqueado,
)


class MailFactoryTest(unittest.TestCase):
    def setUp(self):
        self.apps = []
        self.stream = io.StringIO()
        self.logger = logging.getLogger('app')
        self.old_disabled, self.old_level = self.logger.disabled, self.logger.level
        self.logger.disabled = False
        self.logger.setLevel(logging.WARNING)
        self.handler = logging.StreamHandler(self.stream)
        self.logger.addHandler(self.handler)
        self.patches = [
            patch('socket.socket.connect', side_effect=AssertionError('Rede proibida')),
            patch('smtplib.SMTP', side_effect=AssertionError('SMTP real proibido')),
            patch('smtplib.SMTP_SSL', side_effect=AssertionError('SMTP real proibido')),
        ]
        self.guards = [item.start() for item in self.patches]

    def tearDown(self):
        for app in self.apps:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        for guard in self.guards:
            guard.assert_not_called()
        for item in reversed(self.patches):
            item.stop()
        self.logger.removeHandler(self.handler)
        self.logger.disabled, self.logger.level = self.old_disabled, self.old_level

    def factory(self, values):
        env = {
            'SECRET_KEY': 'SYN188B-SECRET', 'DATABASE_URL': 'sqlite://',
            'MAIL_BACKEND': 'fake', 'GOPHISH_ENABLED': 'false',
            'GOPHISH_BACKEND': 'fake', 'GOPHISH_SEND_ENABLED': 'false',
            **values,
        }
        with patch('app.load_dotenv'), patch.dict(os.environ, env, clear=True):
            app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        self.apps.append(app)
        return app

    def disabled(self, values):
        app = self.factory(values)
        config = app.extensions['mail_convites_config']
        self.assertFalse(config.enabled)
        with app.app_context():
            db.create_all()
        self.assertEqual(app.test_client().get('/health').status_code, 200)
        self.assertEqual(app.test_client().get('/aprender/t/SYN188B-INEXISTENTE').status_code, 404)
        transport = TransporteConfigurado(config)
        with self.assertRaisesRegex(MailBloqueado, 'mail_desabilitado'):
            transport.verificar_disponibilidade()
        self.assertEqual(transport.fake.chamadas, 0)
        return config

    def failure(self, values):
        with self.assertRaises(ConfiguracaoMailInvalida) as caught:
            self.factory(values)
        rendered = ''.join(traceback.format_exception(caught.exception))
        for secret in ('SYN188B-USERNAME', 'SYN188B-PASSWORD', 'SYN188B-SENSITIVE'):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, self.stream.getvalue())
        self.assertNotIn('convites desabilitados', self.stream.getvalue())
        return str(caught.exception)

    def test_disabled_smtp_invalid_port_starts(self):
        self.disabled({'MAIL_ENABLED': 'false', 'MAIL_BACKEND': 'smtp', 'SMTP_PORT': 'INVALID'})
        self.assertIn('convites desabilitados', self.stream.getvalue())

    def test_disabled_smtp_incomplete_starts(self):
        self.disabled({'MAIL_ENABLED': 'false', 'MAIL_BACKEND': 'smtp', 'SMTP_AUTH_MODE': 'password'})

    def test_disabled_invalid_smtp_settings_do_not_leak(self):
        cases = [
            {'SMTP_PORT': 'SYN188B-SENSITIVE'}, {'SMTP_PORT': '70000'},
            {'SMTP_SECURITY': 'INVALID'}, {'SMTP_AUTH_MODE': 'INVALID'},
            {'SMTP_HOST': 'SYN188B-SENSITIVE/bad'}, {'MAIL_TIMEOUT_SECONDS': 'NaN'},
            {'MAIL_BATCH_SIZE': '0'}, {'MAIL_RATE_LIMIT': '-1'},
        ]
        for values in cases:
            with self.subTest(values=tuple(values)):
                self.disabled({'MAIL_ENABLED': 'false', 'MAIL_BACKEND': 'smtp',
                               'SMTP_USERNAME': 'SYN188B-USERNAME',
                               'SMTP_PASSWORD': 'SYN188B-PASSWORD', **values})
        for marker in ('SYN188B-USERNAME', 'SYN188B-PASSWORD', 'SYN188B-SENSITIVE'):
            self.assertNotIn(marker, self.stream.getvalue())

    def test_absent_enabled_defaults_to_disabled(self):
        self.disabled({'MAIL_BACKEND': 'smtp', 'SMTP_PORT': 'INVALID'})

    def test_disabled_valid_fake_preserves_config(self):
        values = {'MAIL_ENABLED': 'false', 'MAIL_BACKEND': 'fake',
                  'BASE_URL': 'https://training.example.invalid',
                  'MAIL_FROM_ADDRESS': 'sender@example.com', 'MAIL_BATCH_SIZE': '7'}
        expected = ConfiguracaoMail.do_ambiente(values)
        actual = self.disabled(values)
        self.assertEqual(actual, expected)
        self.assertEqual(self.stream.getvalue(), '')

    def test_enabled_smtp_invalid_port_fails(self):
        message = self.failure({'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'smtp',
                                'SMTP_PORT': 'SYN188B-SENSITIVE',
                                'SMTP_USERNAME': 'SYN188B-USERNAME',
                                'SMTP_PASSWORD': 'SYN188B-PASSWORD'})
        self.assertEqual(message, 'SMTP_PORT deve ser positivo.')

    def test_enabled_smtp_incomplete_fails(self):
        message = self.failure({'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'smtp',
                                'SMTP_AUTH_MODE': 'password'})
        self.assertIn('SMTP exige:', message)
        self.assertIn('SMTP_USERNAME', message)

    def test_enabled_invalid_config_fails_without_values(self):
        cases = [
            {'BASE_URL': 'https://SYN188B-SENSITIVE:pass@example.invalid'},
            {'MAIL_FROM_ADDRESS': 'SYN188B-SENSITIVE'},
            {'SMTP_HOST': 'SYN188B-SENSITIVE/bad'},
        ]
        for values in cases:
            with self.subTest(values=tuple(values)):
                self.failure({'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'smtp', **values})

    def test_enabled_valid_fake_preserves_config(self):
        values = {'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'fake',
                  'BASE_URL': 'https://training.example.invalid',
                  'MAIL_FROM_ADDRESS': 'sender@example.com', 'MAIL_BATCH_SIZE': '7'}
        expected = ConfiguracaoMail.do_ambiente(values)
        app = self.factory(values)
        actual = app.extensions['mail_convites_config']
        self.assertEqual(actual, expected)
        transport = TransporteConfigurado(actual)
        transport.verificar_disponibilidade()
        self.assertEqual(transport.fake.chamadas, 0)
        self.assertEqual(self.stream.getvalue(), '')

    def test_enabled_fake_invalid_settings_are_not_masked(self):
        self.failure({'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'fake', 'SMTP_PORT': 'INVALID'})

    def test_enabled_valid_smtp_never_opens_connection(self):
        app = self.factory({'MAIL_ENABLED': 'true', 'MAIL_BACKEND': 'smtp',
                            'BASE_URL': 'https://training.example.invalid',
                            'MAIL_FROM_ADDRESS': 'sender@example.com',
                            'SMTP_HOST': 'smtp.example.invalid', 'SMTP_AUTH_MODE': 'password',
                            'SMTP_USERNAME': 'SYN188B-USERNAME', 'SMTP_PASSWORD': 'SYN188B-PASSWORD'})
        config = app.extensions['mail_convites_config']
        self.assertTrue(config.enabled)
        transport = TransporteConfigurado(config)
        with self.assertRaisesRegex(MailBloqueado, 'smtp_real_bloqueado_18_6c'):
            transport.verificar_disponibilidade()

    def test_invalid_enabled_flag_is_not_masked(self):
        for flag in ('INVALID', '', 'false ', ' TRUE '):
            with self.subTest(flag=flag):
                self.failure({'MAIL_ENABLED': flag, 'SMTP_PORT': 'INVALID'})

    def test_enabled_flag_case_matches_original_validator(self):
        self.disabled({'MAIL_ENABLED': 'FALSE', 'SMTP_PORT': 'INVALID'})
        self.stream.seek(0)
        self.stream.truncate()
        self.failure({'MAIL_ENABLED': 'TRUE', 'SMTP_PORT': 'INVALID'})

    def test_unrelated_internal_error_is_not_hidden(self):
        with patch.object(ConfiguracaoMail, 'do_ambiente', side_effect=RuntimeError('SYN-INTERNAL')):
            with self.assertRaisesRegex(RuntimeError, 'SYN-INTERNAL'):
                self.factory({'MAIL_ENABLED': 'false'})
