"""Contrato conservador de conteúdo; não renderiza nem sincroniza HTML.

Não substitui revisão humana. Nesta etapa não há scripts nem formulários
permitidos; futuras exceções precisarão de política explícita e testes.
"""
from html.parser import HTMLParser
import re


class ConteudoInseguro(ValueError):
    def __init__(self):
        super().__init__('Conteúdo de simulação não permitido.')


LOGIN = re.compile(r'\b(login|sign\s*in|password|senha|autentica\w*)\b|entre\s+(na|em)\s+sua\s+conta|enter\s+your\s+(account|credentials)', re.I)


class _Conteudo(HTMLParser):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.textos = []

    def handle_data(self, data):
        self.textos.append(data)

    permitidas = {'html', 'head', 'title', 'meta', 'body', 'main', 'section', 'article',
                 'header', 'footer', 'div', 'span', 'p', 'br', 'hr', 'h1', 'h2', 'h3',
                 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'a', 'table',
                 'thead', 'tbody', 'tr', 'th', 'td'}

    def handle_starttag(self, tag, attrs):
        if tag not in self.permitidas:
            raise ConteudoInseguro()
        for name, value in attrs:
            value = value or ''
            if LOGIN.search(value) or '\\' in value or any(ord(c) < 32 for c in value):
                raise ConteudoInseguro()
            if name.startswith('on') or name in ('src', 'srcdoc', 'action', 'formaction', 'style'):
                raise ConteudoInseguro()
            if tag == 'meta' and name == 'http-equiv':
                raise ConteudoInseguro()
            if name == 'href' and not (value == '{{.URL}}' or (value.startswith('/') and not value.startswith('//')) or value.startswith('#')):
                raise ConteudoInseguro()

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag not in self.permitidas:
            raise ConteudoInseguro()


def validar_conteudo(conteudo_html, *, capture_credentials=False, capture_passwords=False):
    if capture_credentials is not False or capture_passwords is not False:
        raise ConteudoInseguro()
    if not isinstance(conteudo_html, str) or not conteudo_html.strip() or len(conteudo_html) > 200_000:
        raise ConteudoInseguro()
    # UI/texto de autenticação imitada também não é aceito sem formulário.
    if LOGIN.search(conteudo_html):
        raise ConteudoInseguro()
    parser = _Conteudo(convert_charrefs=True)
    try:
        parser.feed(conteudo_html)
        parser.close()
        if LOGIN.search("".join(parser.textos)):
            raise ConteudoInseguro()
    except (ValueError, TypeError):
        raise ConteudoInseguro() from None
