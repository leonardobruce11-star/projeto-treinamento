"""Listagem administrativa e consulta mínima de certificados persistidos."""

from datetime import timezone

from flask import Blueprint, abort, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from app.convites.routes import administrador
from app.services.certificados import ErroCertificado
from app.services.consulta_certificados import listar_certificados_emitidos, obter_certificado_emitido


certificados_admin_bp = Blueprint(
    "certificados_admin", __name__, url_prefix="/certificados"
)


@certificados_admin_bp.after_request
def privacidade(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@certificados_admin_bp.get("/")
@administrador
def listar():
    filtros = {}
    for chave in ("participante", "treinamento", "codigo", "page"):
        valores = request.args.getlist(chave)
        if len(valores) > 1:
            abort(400)
        filtros[chave] = valores[0] if valores else ("1" if chave == "page" else "")
    pagina = filtros.pop("page")
    if not 1 <= len(pagina) <= 10 or not pagina.isascii() or not pagina.isdigit():
        abort(400)
    try:
        resultado = listar_certificados_emitidos(pagina=int(pagina), **filtros)
    except ValueError:
        abort(400)
    except SQLAlchemyError:
        return render_template("certificados_admin/listar.html", resultado=None), 503
    parametros = {
        chave: getattr(resultado.filtros, chave)
        for chave in filtros if getattr(resultado.filtros, chave)
    }
    return render_template(
        "certificados_admin/listar.html", resultado=resultado,
        parametros=parametros, utc=timezone.utc,
    )


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
