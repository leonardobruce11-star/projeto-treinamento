"""Tomada de posse explícita; qualquer retomada externa é somente GET."""
from .leases import recuperar,TICKET
from .preparacao import executar_operacao


def retomar(key,uid,confirmar=False,legado_encerrado=False):
    previous=TICKET.get()
    try:
        ticket,eid,action=recuperar(key,uid,confirmar,legado_encerrado)
        if action in ('preparar','reconciliar'):
            return executar_operacao(eid,uid,'reconciliar',key,recuperada=ticket)
        from .resultados import executar
        return executar(eid,uid,action,key,recuperada=ticket)
    finally:TICKET.set(previous)

