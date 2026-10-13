"""Configuração e composição 18.6C. SMTP real permanece bloqueado nesta etapa."""
import ipaddress
import logging
import math
import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.parse import quote, urlsplit
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.utils.transporte_convites import TransporteFalso, ResultadoTransporte


class ConfiguracaoMailInvalida(ValueError):
    pass


class MailBloqueado(ValueError):
    """Contém somente código estável; nunca inclui valores ou exceções externas."""


def texto_seguro(value, campo):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfiguracaoMailInvalida(campo + ' inválido.')
    return value


def endereco(value, campo='Endereço'):
    texto_seguro(value, campo)
    try:
        return validate_email(value, check_deliverability=False, allow_smtputf8=False).normalized
    except EmailNotValidError:
        raise ConfiguracaoMailInvalida(campo + ' inválido.') from None


def validar_base_url(value):
    texto_seguro(value, 'BASE_URL')
    try:
        if any(c.isspace() for c in value) or any(c in value for c in '\\?#%'):
            raise ValueError()
        u = urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
        if u.path not in ('', '/') or u.query or u.fragment or u.netloc.endswith(':'):
            raise ValueError()
        if u.port is not None and not 1 <= u.port <= 65535:
            raise ValueError()
        host = u.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in host.split('.')):
                raise ValueError()
        return value.rstrip('/')
    except ValueError:
        raise ConfiguracaoMailInvalida('BASE_URL deve ser uma origem http/https válida, sem credenciais, path, query ou fragmento.') from None


def construir_link(base_url, token):
    texto_seguro(token, 'Token')
    if token in ('.', '..'):
        raise ConfiguracaoMailInvalida('Token inválido.')
    return validar_base_url(base_url) + '/aprender/t/' + quote(token, safe='')


@dataclass(frozen=True, repr=False)
class ConfiguracaoMail:
    base_url: str
    enabled: bool
    backend: str
    from_address: str
    from_name: str
    reply_to: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_auth_mode: str
    smtp_username: str = field(repr=False)
    smtp_password: str = field(repr=False)
    timeout_seconds: float
    batch_size: int
    rate_limit: int
    test_recipients: frozenset

    @classmethod
    def do_ambiente(cls, env=None):
        env = os.environ if env is None else env
        def number(key, default, kind):
            try:
                v = kind(env.get(key, default))
                if not math.isfinite(v) or v <= 0:
                    raise ValueError()
                return v
            except (ValueError, TypeError, OverflowError):
                raise ConfiguracaoMailInvalida(key + ' deve ser positivo.') from None
        flag = env.get('MAIL_ENABLED', 'false').lower()
        if flag not in ('true', 'false'):
            raise ConfiguracaoMailInvalida('MAIL_ENABLED deve ser true ou false.')
        backend = env.get('MAIL_BACKEND', 'fake').lower()
        security = env.get('SMTP_SECURITY', 'starttls').lower()
        auth = env.get('SMTP_AUTH_MODE', 'none').lower()
        if backend not in ('fake', 'smtp') or security not in ('starttls', 'tls') or auth not in ('none', 'password'):
            raise ConfiguracaoMailInvalida('MAIL_BACKEND, SMTP_SECURITY ou SMTP_AUTH_MODE inválido.')
        base = env.get('BASE_URL', '')
        if base:
            base = validar_base_url(base)
        sender = env.get('MAIL_FROM_ADDRESS', '')
        reply = env.get('MAIL_REPLY_TO', '')
        if sender:
            sender = endereco(sender, 'MAIL_FROM_ADDRESS')
        if reply:
            reply = endereco(reply, 'MAIL_REPLY_TO')
        name = texto_seguro(env.get('MAIL_FROM_NAME', 'ONE — Treinamentos'), 'MAIL_FROM_NAME')
        host = env.get('SMTP_HOST', '')
        if host:
            texto_seguro(host, 'SMTP_HOST')
            validar_base_url('https://' + host)
            if any(c in host for c in '/:@'):
                raise ConfiguracaoMailInvalida('SMTP_HOST inválido.')
        port = number('SMTP_PORT', '465' if security == 'tls' else '587', int)
        if port > 65535:
            raise ConfiguracaoMailInvalida('SMTP_PORT inválido.')
        recipients = env.get('MAIL_TEST_RECIPIENTS', '')
        allowlist = frozenset(endereco(v.strip(), 'MAIL_TEST_RECIPIENTS') for v in recipients.split(',')) if recipients else frozenset()
        cfg = cls(base, flag == 'true', backend, sender, name, reply, host, port, security, auth,
                  env.get('SMTP_USERNAME', ''), env.get('SMTP_PASSWORD', ''),
                  number('MAIL_TIMEOUT_SECONDS', '10', float), number('MAIL_BATCH_SIZE', '20', int),
                  number('MAIL_RATE_LIMIT', '60', int), allowlist)
        if backend == 'smtp':
            missing = [k for k, v in [('BASE_URL', base), ('MAIL_FROM_ADDRESS', sender), ('SMTP_HOST', host)] if not v]
            if auth == 'password':
                missing += [k for k, v in [('SMTP_USERNAME', cfg.smtp_username), ('SMTP_PASSWORD', cfg.smtp_password)] if not v]
            if missing:
                raise ConfiguracaoMailInvalida('SMTP exige: ' + ', '.join(missing) + '.')
        return cfg


def compor_mensagem(cfg, dados, nome, titulo, token):
    nome = texto_seguro(nome, 'Nome')
    titulo = texto_seguro(titulo, 'Título')
    recipient = endereco(dados.destinatario, 'Destinatário')
    sender = endereco(cfg.from_address, 'MAIL_FROM_ADDRESS')
    msg = EmailMessage()
    msg['Subject'] = 'ONE — Convite de treinamento'
    msg['From'] = Address(display_name=texto_seguro(cfg.from_name, 'MAIL_FROM_NAME'), addr_spec=sender)
    msg['To'] = recipient
    if cfg.reply_to:
        msg['Reply-To'] = endereco(cfg.reply_to, 'MAIL_REPLY_TO')
    msg['Message-ID'] = '<' + str(dados.message_id) + '@' + sender.rsplit('@', 1)[1] + '>'
    link = construir_link(cfg.base_url, token)
    msg.set_content(f'Olá, {nome}.\nO treinamento “{titulo}” está disponível na plataforma de treinamentos da ONE.\nAcesse pelo seu link individual: {link}\nNão compartilhe este endereço.\nEsta mensagem não solicita senha ou envio de credenciais.\n\nONE — Plataforma de treinamentos\n')
    return msg


class AdaptadorSMTP:
    """Sem cliente real por padrão; liberação operacional pertence a etapa futura."""
    def __init__(self, cfg, *, fabrica_simulada=None):
        self.cfg = cfg
        self._fabrica = fabrica_simulada

    def enviar_mensagem(self, mensagem, destinatario):
        cfg = self.cfg
        if not cfg.enabled:
            raise MailBloqueado('mail_desabilitado')
        recipient = endereco(destinatario, 'Destinatário')
        if cfg.test_recipients and recipient not in cfg.test_recipients:
            logging.getLogger(__name__).warning('mail_destinatario_bloqueado')
            raise MailBloqueado('mail_destinatario_bloqueado')
        if self._fabrica is None:
            raise MailBloqueado('smtp_real_bloqueado_18_6c')
        # O cliente simulado deve reproduzir smtplib.SMTP ou SMTP_SSL.
        context = ssl.create_default_context()
        kwargs = dict(host=cfg.smtp_host, port=cfg.smtp_port, timeout=cfg.timeout_seconds)
        if cfg.smtp_security == 'tls':
            kwargs['context'] = context
        try:
            with self._fabrica(**kwargs) as client:
                client.ehlo()
                if cfg.smtp_security == 'starttls':
                    client.starttls(context=context)
                    client.ehlo()
                if cfg.smtp_auth_mode == 'password':
                    client.login(cfg.smtp_username, cfg.smtp_password)
                refused = client.send_message(mensagem, from_addr=cfg.from_address, to_addrs=[recipient])
                if refused:
                    return ResultadoTransporte('falha', 'destinatario_invalido')
        except (OSError, smtplib.SMTPException):
            # Timeout/desconexão podem ocorrer após aceite; nunca repetir automaticamente.
            return ResultadoTransporte('resultado_indeterminado', 'excecao_transporte')
        return ResultadoTransporte('enviado')


class TransporteConfigurado:
    def __init__(self, cfg, *, fabrica_simulada=None):
        self.cfg = cfg
        self.fake = TransporteFalso()
        self.smtp = AdaptadorSMTP(cfg, fabrica_simulada=fabrica_simulada)

    def verificar_disponibilidade(self):
        if not self.cfg.enabled:
            raise MailBloqueado('mail_desabilitado')
        if self.cfg.backend == 'smtp' and self.smtp._fabrica is None:
            raise MailBloqueado('smtp_real_bloqueado_18_6c')

    def enviar(self, dados):
        self.verificar_disponibilidade()
        recipient = endereco(dados.destinatario, 'Destinatário')
        if self.cfg.test_recipients and recipient not in self.cfg.test_recipients:
            logging.getLogger(__name__).warning('mail_destinatario_bloqueado')
            return ResultadoTransporte('falha', 'destinatario_invalido')
        with Session(db.engine) as session:
            p = session.get(ParticipacaoTreinamento, dados.participacao_id)
            mensagem = compor_mensagem(self.cfg, dados, p.colaborador.nome, p.treinamento.titulo, p.token)
        if self.cfg.backend == 'fake':
            return self.fake.enviar(dados)
        return self.smtp.enviar_mensagem(mensagem, recipient)


def processar_convite_configurado(transporte):
    """Bloqueia antes da reserva; sem worker, lote, retry ou agendamento 18.6D."""
    from app.utils.convites_treinamento import processar_proximo_envio
    transporte.verificar_disponibilidade()
    return processar_proximo_envio(transporte)
