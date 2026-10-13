"""Whitelist de DTOs técnicos. Nenhum corpo/details/cookie é projetado."""
import re
from datetime import datetime,timezone

IDENT=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$')
TIPOS={'Email Sent':'mensagem_enviada','Email Opened':'mensagem_aberta',
       'Clicked Link':'link_acessado','Error Sending Email':'erro_envio',
       **{v:v for v in ('mensagem_enviada','mensagem_aberta','link_acessado','erro_envio')}}


def identificador(value):
    if not isinstance(value,str) or not IDENT.fullmatch(value):raise ValueError()
    return value


def timestamp(value):
    if not isinstance(value,str) or len(value)>50:raise ValueError()
    date=datetime.fromisoformat(value.replace('Z','+00:00'))
    if date.tzinfo is None:raise ValueError()
    return date.astimezone(timezone.utc)


def projetar(data,expected_id):
    if not isinstance(data,dict) or type(data.get('id')) is not int or data['id']!=expected_id:raise ValueError()
    name=data.get('name');sha=data.get('digest')
    if not isinstance(name,str) or not re.fullmatch(r'ONE-CAMP-[0-9a-f]{32}-[0-9a-f]{64}',name):raise ValueError()
    if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError()
    results=data.get('results');timeline=data.get('timeline')
    if not isinstance(results,list) or not isinstance(timeline,list) or len(results)>10000 or len(timeline)>20000:raise ValueError()
    clean_results=[];clean_events=[];discarded=0
    for row in results:
        if not isinstance(row,dict):raise ValueError()
        rid=identificador(row.get('rid'))
        email=row.get('email')
        if email is not None and (not isinstance(email,str) or len(email)>180):raise ValueError()
        clean_results.append({'rid':rid,'email':email})
    for row in timeline:
        if not isinstance(row,dict):discarded+=1;continue
        # Dados submetidos e tipos desconhecidos são descartados antes de ler outros campos.
        kind=TIPOS.get(row.get('message')) if isinstance(row.get('message'),str) else None
        if kind is None:discarded+=1;continue
        try:
            rid=identificador(row.get('rid'));source=timestamp(row.get('time'))
            event_id=identificador(row['event_id']) if row.get('event_id') is not None else None
            seq=row.get('sequence')
            if seq is not None and (type(seq) is not int or not 0<=seq<=2147483647):raise ValueError()
        except (ValueError,TypeError,OverflowError):discarded+=1;continue
        clean_events.append({'rid':rid,'tipo':kind,'origem':source,'event_id':event_id,'sequencia':seq})
    return {'id':data['id'],'name':name,'digest':sha,'results':clean_results,'events':clean_events,'descartados':discarded}
