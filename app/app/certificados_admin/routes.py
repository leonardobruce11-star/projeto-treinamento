"""Consulta mínima para validar o contrato administrativo; sem interface visual."""

from flask import Blueprint, jsonify
from sqlalchemy.exc import SQLAlchemyError

from app.convites.routes import administrador
from app.services.certificados import ErroCertificado
from app.services.consulta_certificados import obter_certificado_emitido


certificados_admin_bp = Blueprint(
    "certificados_admin", __name__, url_prefix="/certificados"
)


@certificados_admin_bp.after_request
def privacidade(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@certificados_admin_bp.get("/<int:certificado_id>")
@administrador
def consultar(certificado_id):
    try:
        recibo = obter_certificado_emitido(certificado_id)
    except ErroCertificado:
        return jsonify(erro="certificado_indisponivel"), 404
    except SQLAlchemyError:
        return jsonify(erro="consulta_indisponivel"), 503
    if recibo is None:
        return jsonify(erro="certificado_indisponivel"), 404
    return jsonify(
        certificado_id=recibo.certificado_id,
        participacao_id=recibo.participacao_id,
        nome_participante=recibo.nome_participante,
        titulo_treinamento=recibo.titulo_treinamento,
        codigo_verificacao=recibo.codigo_verificacao,
        emitido_em=recibo.emitido_em.isoformat() if recibo.emitido_em else None,
    )
