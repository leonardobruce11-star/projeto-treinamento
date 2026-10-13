"""Configuração fail-closed. Nenhum segredo aparece na representação."""
from dataclasses import dataclass, field
import math
import os
import re
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ConfiguracaoGophish:
    instance_id: str = ''
    enabled: bool = False
    send_enabled: bool = False
    backend: str = 'fake'
    base_url: str = field(default='', repr=False)
    api_key: str = field(default='', repr=False)
    timeout_seconds: float = 5.0
    ca_bundle: str = field(default='', repr=False)
    valida: bool = True

    @classmethod
    def do_ambiente(cls, env=None):
        env = os.environ if env is None else env
        enabled = str(env.get('GOPHISH_ENABLED', 'false')).strip().lower()
        send = str(env.get('GOPHISH_SEND_ENABLED', 'false')).strip().lower()
        backend = str(env.get('GOPHISH_BACKEND', 'fake')).strip().lower()
        valida = enabled in ('true', 'false') and send in ('true', 'false') and backend in ('fake', 'http')
        try:
            timeout = float(env.get('GOPHISH_TIMEOUT_SECONDS', 5))
            valida = valida and math.isfinite(timeout) and 0 < timeout <= 60
        except (ValueError, TypeError):
            timeout, valida = 5.0, False
        url = str(env.get('GOPHISH_URL', '')).strip().rstrip('/')
        key = str(env.get('GOPHISH_API_KEY', '')).strip()
        ca = str(env.get('GOPHISH_CA_BUNDLE', '')).strip()
        if backend == 'http':
            try:
                parsed = urlsplit(url)
                valida = valida and bool(parsed.hostname) and parsed.scheme == 'https'
                valida = valida and not any(c.isspace() or ord(c) < 32 for c in url) and not any(c in url for c in ('\\', '%'))
                valida = valida and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                valida = valida and parsed.path in ('', '/')
                parsed.port  # Valida também a sintaxe da porta.
            except ValueError:
                valida = False
            valida = valida and not any(c in key for c in ('\r', '\n'))
            valida = valida and (not ca or (Path(ca).is_absolute() and Path(ca).is_file()))
        instance = str(env.get('GOPHISH_INSTANCE_ID', 'one-fake' if backend == 'fake' else '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', instance) or (key and instance == key):
            instance = ''
            valida = False
        # 18.7C: nem GOPHISH_SEND_ENABLED=true libera execução.
        return cls(instance_id=instance, enabled=enabled == 'true', send_enabled=False, backend=backend,
                   base_url=url, api_key=key, timeout_seconds=timeout, ca_bundle=ca, valida=valida)

    def estado(self):
        if not self.enabled:
            return 'disabled'
        if not self.valida or self.send_enabled or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', self.instance_id) or (self.api_key and self.instance_id == self.api_key):
            return 'not_configured'
        if self.backend not in ('fake', 'http'):
            return 'not_configured'
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 60:
            return 'not_configured'
        if self.backend == 'http':
            try:
                parsed = urlsplit(self.base_url)
                parsed.port
                safe = (parsed.scheme == 'https' and parsed.hostname and
                        not (parsed.username or parsed.password or parsed.query or parsed.fragment) and
                        parsed.path in ('', '/') and not any(c.isspace() or ord(c) < 32 for c in self.base_url) and
                        not any(c in self.base_url for c in ('\\', '%')) and self.api_key and
                        not any(c in self.api_key for c in ('\r', '\n')))
                if not safe or (self.ca_bundle and not (Path(self.ca_bundle).is_absolute() and Path(self.ca_bundle).is_file())):
                    return 'not_configured'
            except (ValueError, TypeError):
                return 'not_configured'
        return 'ready'

    def origem_digest(self):
        # URL validada não contém credenciais; só seu hash é persistido.
        origem = {'backend': self.backend, 'origem': self.base_url if self.backend == 'http' else self.instance_id}
        return hashlib.sha256(json.dumps(origem,sort_keys=True,separators=(',',':')).encode()).hexdigest()
