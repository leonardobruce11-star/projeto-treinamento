"""Política existente: administrador ativo, sem novo RBAC."""
from flask import abort
from app.convites.routes import administrador
from .service import servico_gophish


def bloquear_mutacao_avulsa():
    status = 'disabled' if servico_gophish().config.estado() == 'disabled' else 'forbidden'
    abort(409, description='Integração desabilitada.' if status == 'disabled' else
          'Sincronização avulsa indisponível; preparação após aprovação será definida na 18.7C.')
