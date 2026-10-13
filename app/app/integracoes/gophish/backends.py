"""Transportes sem logging/retries/redirects. POST restrito a auxiliares aprovados."""
from copy import deepcopy
from threading import RLock
from app.simulacoes.contracts import TIPOS, NOME, normalizar, digest
import ssl
import requests


class HttpBackend:
    def __init__(self, config):
        self.config = config

    def request(self, method, endpoint, payload=None, *, preparacao=False):
        if method != 'GET' and not (preparacao and method == 'POST' and endpoint in ('/api/groups/','/api/templates/','/api/pages/')):
            return {'status': 'forbidden'}
        cfg = self.config
        # CA padrão do sistema; não herda proxies/CA de variáveis requests.
        ca = cfg.ca_bundle or ssl.get_default_verify_paths().cafile
        if not ca:
            return {'status': 'tls_error'}
        try:
            with requests.Session() as session:
                session.trust_env = False
                options = {'json':payload} if method == 'POST' else {}
                response = session.request(method, cfg.base_url + endpoint,
                    headers={'Authorization': cfg.api_key, 'Accept': 'application/json'},
                    timeout=cfg.timeout_seconds, verify=ca, allow_redirects=False, **options)
                status = response.status_code
                if status not in ((200,201) if method == 'POST' else (200,)):
                    return {'http_status': status}
                try:
                    data = response.json()
                except ValueError:
                    return {'status': 'invalid_response'}
                return {'http_status': status, 'data': data}
        except requests.exceptions.SSLError:
            return {'status': 'tls_error'}
        except requests.exceptions.Timeout:
            return {'status': 'timeout'}
        except requests.RequestException:
            return {'status': 'connection_error'}
        except OSError:
            return {'status': 'tls_error'}


class FakeBackend:
    """Estado em memória e respostas programáveis por (método, endpoint).

    Não instancia requests, sockets ou SMTP. Não conserva payload/HTML/alvos.
    """
    def __init__(self):
        self.respostas = {}
        self.recursos = {kind: {} for kind in ('groups', 'templates', 'pages', 'campaigns', 'smtp')}
        self.chamadas = []  # Somente operação/caminho com IDs; nunca payload.
        self._next_id = 1
        self._lock = RLock()

    def configurar_campanha_tecnica(self, ident, nome, digest, resultados=None, eventos=None):
        # Fixture explícita, nunca POST/create campaign. Não há transporte externo.
        with self._lock:
            self.recursos['campaigns'][ident]={'id':ident,'name':nome,'digest':digest,
                'results':deepcopy(resultados or []),'timeline':deepcopy(eventos or [])}

    def configurar_resposta(self, method, endpoint, *, http_status=None, data=None, status=None, simular_criacao=False):
        self.respostas[(method, endpoint)] = dict(http_status=http_status, data=data, status=status, simular_criacao=simular_criacao)

    def request(self, method, endpoint, payload=None, *, preparacao=False):
        with self._lock:
            return self._request(method,endpoint,payload)

    def _request(self, method, endpoint, payload=None):
        self.chamadas.append((method, endpoint))
        override = deepcopy(self.respostas.get((method,endpoint)))
        if override is not None and not (method == 'POST' and override.pop('simular_criacao',False)):
            return override
        parts = endpoint.strip('/').split('/')
        kind = parts[1]
        table = self.recursos[kind]
        ident = int(parts[2]) if len(parts) >= 3 else None
        if method == 'GET':
            if ident is None:
                data = list(table.values())
            elif ident not in table:
                return {'http_status': 404}
            else:
                data = table[ident]
            return {'http_status': 200, 'data': deepcopy(data)}
        if method == 'PUT' and ident not in table:
            return {'http_status': 404}
        if method == 'POST':
            ident = self._next_id
            self._next_id += 1
        table[ident] = {'id': ident}
        # 18.7C: só nome técnico e hash; nunca HTML, e-mails ou tokens.
        if payload and NOME.fullmatch(payload.get('name','')):
            tipo=next(k for k,v in TIPOS.items() if v == kind)
            table[ident].update(name=payload['name'],_one_digest=digest(normalizar(tipo,payload)))
        if kind == 'pages':
            table[ident].update(capture_credentials=False, capture_passwords=False)
        if override is not None:
            return override  # Simula perda da resposta após possível criação.
        return {'http_status': 201 if method == 'POST' else 200, 'data': deepcopy(table[ident])}
