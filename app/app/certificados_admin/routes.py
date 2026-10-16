"""Consultas administrativas HTML e JSON de certificados persistidos."""

from datetime import timezone
from io import BytesIO

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for
from sqlalchemy.exc import SQLAlchemyError

from app.convites.routes import administrador
from app.services.certificados import ErroCertificado
from app.services.consulta_certificados import listar_certificados_emitidos, obter_certificado_emitido
from app.services.pdf_certificados import gerar_pdf_certificado


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


def _voltar_aos_certificados():
    # Contexto de navegação opcional, nunca uma URL de retorno fornecida pelo
    # cliente. Valores inválidos/repetidos são descartados sem afetar a consulta.
    parametros = {}
    for chave, limite in (("participante", 150), ("treinamento", 200), ("codigo", 64), ("page", 10)):
        valores = request.args.getlist(chave)
        if len(valores) != 1:
            continue
        valor = valores[0].strip()
        if not valor or len(valores[0]) > limite or any(ord(c) < 32 or ord(c) == 127 for c in valores[0]):
            continue
        if chave == "page" and (
            not valor.isascii() or not valor.isdigit() or not 1 <= int(valor) <= 2147483647
        ):
            continue
        parametros[chave] = valor
    return url_for("certificados_admin.listar", **parametros)


@certificados_admin_bp.get("/<int:certificado_id>/detalhes")
@administrador
def detalhes(certificado_id):
    try:
        recibo = obter_certificado_emitido(certificado_id)
    except ErroCertificado:
        abort(404)
    except SQLAlchemyError:
        return render_template(
            "certificados_admin/detalhes.html", certificado=None,
            voltar_url=_voltar_aos_certificados(),
        ), 503
    if recibo is None:
        abort(404)
    return render_template(
        "certificados_admin/detalhes.html", certificado=recibo,
        voltar_url=_voltar_aos_certificados(), utc=timezone.utc,
    )


@certificados_admin_bp.get("/<int:certificado_id>/pdf")
@administrador
def baixar_pdf(certificado_id):
    try:
        recibo = obter_certificado_emitido(certificado_id)
    except ErroCertificado:
        return jsonify(erro="certificado_indisponivel"), 404
    except SQLAlchemyError:
        return jsonify(erro="consulta_indisponivel"), 503
    if recibo is None:
        return jsonify(erro="certificado_indisponivel"), 404

    try:
        conteudo = gerar_pdf_certificado(recibo)
    except ValueError:
        return jsonify(erro="dados_certificado_invalidos"), 409
    except Exception:
        # Erros do renderizador podem conter snapshots ou caminhos privados.
        # A captura é limitada à geração, antes de construir a resposta PDF.
        return jsonify(erro="geracao_indisponivel"), 503
    if not (
        isinstance(conteudo, bytes)
        and conteudo.startswith(b"%PDF-")
        and conteudo.rstrip().endswith(b"%%EOF")
    ):
        return jsonify(erro="geracao_indisponivel"), 503
    # HEAD usa a mesma geração em memória para obter os cabeçalhos reais;
    # Flask/Werkzeug suprime o corpo, sem emissão ou persistência de arquivos.
    return send_file(
        BytesIO(conteudo), mimetype="application/pdf", as_attachment=True,
        download_name=f"certificado-{certificado_id}.pdf", conditional=False, etag=False,
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
