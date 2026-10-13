"""Cliente com DTO mínimo e classificação estável, sem dados brutos."""
from flask import current_app, has_app_context
from itsdangerous import URLSafeTimedSerializer, BadData
import re
from uuid import UUID
from app.simulacoes.contracts import TIPOS, NOME, normalizar, digest
from .config import ConfiguracaoGophish
from .backends import FakeBackend, HttpBackend
from .content import validar_conteudo, ConteudoInseguro

STATUS = {'disabled', 'not_configured', 'not_found', 'auth_error', 'forbidden',
          'timeout', 'tls_error', 'server_error', 'invalid_response',
          'result_indeterminate', 'connection_error', 'invalid_content', 'invalid_request', 'binding_mismatch'}


def resultado(status, data=None, http_status=None):
    return {'ok': status in ('connected', 'created', 'updated'), 'status': status,
            'data': data, 'http_status': http_status}


def _id(value):
    return type(value) is int and value > 0


class GophishService:
    def __init__(self, config=None, backend=None):
        if config is None:
            config = current_app.extensions.get('gophish_config') if has_app_context() else None
            if config is None:
                config = ConfiguracaoGophish.do_ambiente()
        self.config = config
        if config.backend == 'fake':
            if backend is not None and not isinstance(backend, FakeBackend):
                raise ValueError('Backend fake obrigatório.')
            self.backend = backend or FakeBackend()
        else:
            if backend is not None:
                raise ValueError('Transporte HTTP não pode ser substituído por configuração.')
            self.backend = HttpBackend(config)

    def configurado(self):
        return self.config.estado() == 'ready'

    def estado_local(self):
        return {'status': self.config.estado(), 'backend': self.config.backend if self.config.backend in ('fake', 'http') else 'invalid',
                'send_enabled': False}

    def _request(self, method, endpoint, payload=None, *, lista=False, pagina=False, expected_id=None, aux_tipo=None, nome_filter=None, autorizado=False):
        state = self.config.estado()
        if state != 'ready':
            return resultado(state)
        # Mutações avulsas continuam proibidas; só a capability interna permite auxiliares.
        if method != 'GET' and self.config.backend != 'fake' and not autorizado:
            return resultado('forbidden')
        raw = self.backend.request(method, endpoint, payload, preparacao=True) if autorizado else self.backend.request(method, endpoint, payload)
        if not isinstance(raw,dict):return resultado('invalid_response')
        code = raw.get('status')
        http = raw.get('http_status')
        http = http if type(http) is int and 100 <= http <= 599 else None
        if code:
            if not isinstance(code,str) or code not in STATUS:
                code = 'invalid_response'
            if method != 'GET' and code in ('timeout', 'connection_error'):
                code = 'result_indeterminate'
            return resultado(code)
        errors = {404: 'not_found', 401: 'auth_error', 403: 'forbidden'}
        if http in errors:
            return resultado(errors[http], http_status=http)
        if http is not None and http >= 500:
            return resultado('server_error', http_status=http)
        if http not in ((200, 201) if method == 'POST' else (200,)):
            return resultado('invalid_response', http_status=http)
        data = raw.get('data')
        values = data if lista else [data]
        if not isinstance(values, list):
            return resultado('invalid_response', http_status=http)
        clean = []
        for item in values:
            if not isinstance(item, dict) or not _id(item.get('id')):
                return resultado('invalid_response', http_status=http)
            if expected_id is not None and item['id'] != expected_id:
                return resultado('invalid_response', http_status=http)
            if aux_tipo:
                name=item.get('name')
                if not isinstance(name,str):return resultado('invalid_response',http_status=http)
                if nome_filter is not None and name != nome_filter:continue
                if not NOME.fullmatch(name):return resultado('binding_mismatch',http_status=http)
                try:
                    if self.config.backend == 'fake' and '_one_digest' in item:
                        content_digest=item['_one_digest']
                        if not isinstance(content_digest,str) or not re.fullmatch(r'[0-9a-f]{64}',content_digest):raise ValueError()
                        if aux_tipo == 'landing_page' and (item.get('capture_credentials') is not False or item.get('capture_passwords') is not False):
                            raise ConteudoInseguro()
                    else:
                        content_digest=digest(normalizar(aux_tipo,item))
                except ConteudoInseguro:return resultado('invalid_content',http_status=http)
                except (ValueError,KeyError,TypeError):return resultado('invalid_response',http_status=http)
                clean.append({'id':item['id'],'name':name,'digest':content_digest})
                continue
            safe = {'id': item['id']}
            if pagina:
                for flag in ('capture_credentials', 'capture_passwords'):
                    if type(item.get(flag)) is not bool:
                        return resultado('invalid_response', http_status=http)
                    safe[flag] = item[flag]
            clean.append(safe)
        return resultado('created' if method == 'POST' else 'updated' if method == 'PUT' else 'connected',
                         clean if lista else clean[0], http)

    def verificar_conexao(self):
        response = self.listar_campanhas()
        return {'status': response['status'], 'backend': self.estado_local()['backend'], 'send_enabled': False}

    def _listar(self, kind):
        return self._request('GET', f'/api/{kind}/', lista=True, pagina=kind == 'pages')

    def _obter(self, kind, ident, suffix=''):
        if not _id(ident):
            return resultado('invalid_request')
        return self._request('GET', f'/api/{kind}/{ident}{suffix}', pagina=kind == 'pages', expected_id=ident)

    def listar_campanhas(self): return self._listar('campaigns')
    def obter_campanha(self, campanha_id): return self._obter('campaigns', campanha_id)
    def obter_resumo_campanha(self, campanha_id): return self._obter('campaigns', campanha_id, '/summary')
    def listar_grupos(self): return self._listar('groups')
    def obter_grupo(self, grupo_id): return self._obter('groups', grupo_id)
    def listar_templates(self): return self._listar('templates')
    def obter_template(self, template_id): return self._obter('templates', template_id)
    def listar_paginas(self): return self._listar('pages')
    def obter_pagina(self, pagina_id): return self._obter('pages', pagina_id)
    def listar_sending_profiles(self): return self._listar('smtp')

    def _mutar(self, kind, payload, ident=None):
        if ident is not None and not _id(ident):
            return resultado('invalid_request')
        return self._request('PUT' if ident else 'POST', f'/api/{kind}/' + (str(ident) if ident else ''),
                             payload, pagina=kind == 'pages', expected_id=ident)

    def criar_grupo(self, nome, participantes):
        return self._mutar('groups', {'name': nome, 'targets': participantes})

    def atualizar_grupo(self, grupo_id, nome, participantes):
        return self._mutar('groups', {'name': nome, 'targets': participantes}, grupo_id)

    def _conteudo(self, kind, nome, html, ident=None, assunto=None):
        state = self.config.estado()
        if state != 'ready':
            return resultado(state)
        try:
            validar_conteudo(html)
        except ConteudoInseguro:
            return resultado('invalid_content')
        payload = {'name': nome, 'html': html}
        if kind == 'pages':
            payload.update(capture_credentials=False, capture_passwords=False, redirect_url='')
        else:
            payload.update(subject=assunto, text='', attachments=[])
        return self._mutar(kind, payload, ident)

    def criar_template(self, nome, assunto, conteudo_html):
        return self._conteudo('templates', nome, conteudo_html, assunto=assunto)

    def atualizar_template(self, template_id, nome, assunto, conteudo_html):
        return self._conteudo('templates', nome, conteudo_html, template_id, assunto)

    def criar_pagina(self, nome, conteudo_html):
        return self._conteudo('pages', nome, conteudo_html)

    def atualizar_pagina(self, pagina_id, nome, conteudo_html):
        return self._conteudo('pages', nome, conteudo_html, pagina_id)

    def consultar_auxiliar(self, tipo, ident):
        if tipo not in TIPOS or not _id(ident):return resultado('invalid_request')
        return self._request('GET',f'/api/{TIPOS[tipo]}/{ident}',expected_id=ident,aux_tipo=tipo)

    def localizar_auxiliar(self, tipo, nome):
        if tipo not in TIPOS or not NOME.fullmatch(nome):return resultado('invalid_request')
        return self._request('GET',f'/api/{TIPOS[tipo]}/',lista=True,aux_tipo=tipo,nome_filter=nome)

    def criar_auxiliar_aprovado(self, tipo, payload, autorizacao):
        if self.config.estado() != 'ready':return resultado(self.config.estado())
        if tipo not in TIPOS or not isinstance(payload,dict):return resultado('invalid_request')
        try:
            if not has_app_context():raise ValueError()
            claim=URLSafeTimedSerializer(current_app.secret_key,salt='gophish-aux-18.7C').loads(autorizacao,max_age=300)
            if not isinstance(claim,dict):raise ValueError()
            content=normalizar(tipo,payload)
            expected={'tipo':tipo,'digest':digest(content),'nome':payload.get('name'),
                      'instancia':self.config.instance_id,'origem':self.config.origem_digest()}
            if any(claim.get(k)!=v for k,v in expected.items()) or not NOME.fullmatch(payload.get('name','')):
                raise ValueError()
            UUID(claim['operacao'])
            from app.simulacoes.leases import validar_capability
            validar_capability(claim)
            if self.config.backend == 'http' and current_app.config.get('APP_ENV') not in ('production','homologation','homologacao'):raise ValueError()
        except ConteudoInseguro:return resultado('invalid_content')
        except (ValueError,TypeError,KeyError,BadData):return resultado('forbidden')
        return self._request('POST',f'/api/{TIPOS[tipo]}/',payload,aux_tipo=tipo,autorizado=True)

    def ler_campanha_tecnica(self, ident):
        state=self.config.estado()
        if state!='ready':return resultado(state)
        if not _id(ident):return resultado('invalid_request')
        # Contrato D ainda não homologado com Gophish real: falha fechada em HTTP.
        if self.config.backend!='fake':return resultado('forbidden')
        raw=self.backend.request('GET',f'/api/campaigns/{ident}/results')
        if not isinstance(raw,dict):return resultado('invalid_response')
        code=raw.get('status')
        if code:return resultado(code if isinstance(code,str) and code in STATUS else 'invalid_response')
        http=raw.get('http_status')
        if http in (401,403,404):return resultado({401:'auth_error',403:'forbidden',404:'not_found'}[http])
        if type(http) is int and http>=500:return resultado('server_error')
        if http!=200:return resultado('invalid_response')
        try:
            from app.simulacoes.eventos_contrato import projetar
            data=projetar(raw.get('data'),ident)
        except (ValueError,TypeError,KeyError):return resultado('invalid_response')
        return resultado('connected',data)

    def executar_campanha(self, *args, **kwargs):
        # Defesa explícita para futura execução; sem endpoint ou payload externo.
        return resultado('disabled' if not self.config.enabled else 'forbidden')


def servico_gophish():
    service = current_app.extensions.get('gophish_service')
    if service is None:
        service = GophishService()
        current_app.extensions['gophish_service'] = service
    return service
