"""Representação canônica e payloads mínimos, sem estado de banco/segredos."""
import hashlib
import json
import re
from app.integracoes.gophish.content import validar_conteudo

TIPOS = {'grupo':'groups', 'template':'templates', 'landing_page':'pages'}
NOME = re.compile(r'^ONE-[0-9a-f]{32}-(grupo|template|landing_page)-[0-9a-f]{64}$')


def canonico(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonico(value).encode('utf-8')).hexdigest()


def normalizar(tipo, data):
    if tipo == 'grupo':
        targets = data['targets']
        if not isinstance(targets, list):raise ValueError()
        result=[]
        for t in targets:
            if not isinstance(t,dict) or not isinstance(t.get('email'),str):raise ValueError()
            row={k:t.get(k,'') for k in ('email','first_name','last_name','position')}
            if any(not isinstance(v,str) for v in row.values()):raise ValueError()
            result.append(row)
        return {'targets':sorted(result,key=lambda t:(t['email'],t['first_name'],t['last_name'],t['position']))}
    validar_conteudo(data['html'], capture_credentials=data.get('capture_credentials',False),
                     capture_passwords=data.get('capture_passwords',False))
    if tipo == 'template':
        if not isinstance(data.get('subject'),str) or data.get('text','') != '' or data.get('attachments',[]) != []:
            raise ValueError()
        return {'subject':data['subject'], 'html':data['html'], 'text':'', 'attachments':[]}
    if tipo == 'landing_page':
        if data.get('capture_credentials') is not False or data.get('capture_passwords') is not False or data.get('redirect_url','') != '':
            raise ValueError()
        return {'html':data['html'], 'capture_credentials':False, 'capture_passwords':False, 'redirect_url':''}
    raise ValueError()


def payload_grupo(publico):
    return {'targets':[{'email':p['email'], 'first_name':'','last_name':'','position':''} for p in publico]}
