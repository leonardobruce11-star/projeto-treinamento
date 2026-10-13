"""Proteção da representação de logs; nunca modifica a requisição."""
import argparse
import logging
import os
import re
import sys
import traceback
from urllib.parse import unquote

def _segmento(nome):
    # Reconhece também letras percent-encoded, sem decodificar a mensagem inteira.
    return ''.join('(?:' + c + '|%' + format(ord(c), '02x') + '|%' + format(ord(c.upper()), '02x') + ')' for c in nome)


PORTAL = re.compile(r"(?:/|%2f)" + _segmento('aprender') + r"(?:/|%2f)" + _segmento('t') + r"(?:/|%2f)((?:(?!%2f|%3f|%23)[^\s/\?#\"'<>\\])+)", re.I)
PONTE = re.compile(r"(?:/|%2f)"+_segmento('simulacao')+r"(?:/|%2f)"+_segmento('educacao')+r"(?:/|%2f)((?:(?!%2f|%3f|%23)[^\s/\?#\"'<>\\])+)",re.I)
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
QUERY = re.compile(r"((?:https?://|/)[^\s?\"'<>]*)\?[^\s\"'<>]*", re.I)


def sanitizar(texto, token=None):
    texto = str(texto)
    tokens = {token} if token else set()
    for match in list(PORTAL.finditer(texto))+list(PONTE.finditer(texto)):
        valor = match.group(1)
        if valor != '[REDACTED]':
            tokens.add(valor)
            tokens.add(unquote(valor))
    texto = PORTAL.sub('/aprender/t/[REDACTED]', texto)
    texto = PONTE.sub('/simulacao/educacao/[REDACTED]', texto)
    # Remover a query antes de redigir e-mail: o regex de e-mail pode consumir '?' e '&'.
    texto = QUERY.sub(r'\1?[REDACTED]', texto)
    texto = EMAIL.sub('[EMAIL REDACTED]',texto)
    for valor in sorted(tokens, key=len, reverse=True):
        if valor and valor != '[REDACTED]':
            texto = texto.replace(valor, '[REDACTED]')
    return texto


class FiltroPrivacidade(logging.Filter):
    def filter(self, record):
        token = None
        try:
            from flask import has_request_context, request
            if has_request_context():
                token = (request.view_args or {}).get('token') or (request.view_args or {}).get('identificador')
                if not token:
                    match = PORTAL.search(request.path) or PONTE.search(request.path)
                    token = match.group(1) if match else None
            mensagem = record.getMessage()
            if not token:
                match = PORTAL.search(mensagem) or PONTE.search(mensagem)
                token = unquote(match.group(1)) if match else None
            record.msg = sanitizar(mensagem, token)
            record.args = ()
            if record.exc_info:
                # Mensagens de exceção podem conter SQL, parâmetros e payloads.
                # Mantém tipo e localização sem valores ou linhas de código.
                tipo, _, trace = record.exc_info
                frames = "".join(
                    f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
                    for frame in traceback.extract_tb(trace)
                )
                nome = tipo.__name__ if tipo else "Exception"
                record.exc_text = sanitizar(
                    frames + nome + ": detalhes omitidos por privacidade.", token
                )
                record.exc_info = None
                record._one_exception_redacted = True
            elif record.exc_text:
                if getattr(record, "_one_exception_redacted", False):
                    record.exc_text = sanitizar(record.exc_text, token)
                else:
                    record.exc_text = "Detalhes da exceção omitidos por privacidade."
                    record._one_exception_redacted = True
            if record.stack_info:
                record.stack_info = sanitizar(record.stack_info, token)
        except Exception:
            # Falha fechada: nunca usar a mensagem original como fallback.
            record.msg = 'Mensagem de log omitida por falha de sanitização.'
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def proteger_logger(logger):
    if not any(isinstance(f, FiltroPrivacidade) for f in logger.filters):
        logger.addFilter(FiltroPrivacidade())
    for handler in logger.handlers:
        if not any(isinstance(f, FiltroPrivacidade) for f in handler.filters):
            handler.addFilter(FiltroPrivacidade())


def configurar_logs(app):
    proteger_logger(app.logger)
    proteger_logger(logging.getLogger())
    for name in ('gunicorn.error', 'gunicorn.access'):
        proteger_logger(logging.getLogger(name))
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            for handler in logger.handlers:
                if not any(isinstance(f, FiltroPrivacidade) for f in handler.filters):
                    handler.addFilter(FiltroPrivacidade())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arquivo', required=True)
    args = parser.parse_args()
    # Reabre por linha: rotação por rename não prende o descritor no arquivo antigo.
    # Apache supervisiona o pipe. Não há saída crua de contingência.
    for linha in sys.stdin:
        try:
            limpa = sanitizar(linha)
            fd = os.open(args.arquivo, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
            with os.fdopen(fd, 'a', encoding='utf-8') as arquivo:
                arquivo.write(limpa)
        except Exception:
            sys.stderr.write('Falha ao gravar log sanitizado.\n')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
