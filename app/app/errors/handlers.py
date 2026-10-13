from flask import (
    current_app,
    render_template,
)

from flask_wtf.csrf import CSRFError

from app.extensions import db


# ==========================================================
# REGISTRO CENTRAL DOS HANDLERS
# ==========================================================

def register_error_handlers(app):

    # ======================================================
    # CSRF
    # ======================================================

    @app.errorhandler(CSRFError)
    def csrf_error(error):

        current_app.logger.warning(
            "Requisição bloqueada por CSRF."
        )

        return (
            render_template(
                "errors/400.html",
                titulo="Solicitação inválida",
                mensagem=(
                    "Não foi possível validar este formulário. "
                    "A página ou a sessão pode ter expirado. "
                    "Atualize a página e tente novamente."
                ),
            ),
            400,
        )

    # ======================================================
    # 400 - BAD REQUEST
    # ======================================================

    @app.errorhandler(400)
    def bad_request(error):

        return (
            render_template(
                "errors/400.html",
                titulo="Solicitação inválida",
                mensagem=(
                    "Não foi possível processar a solicitação. "
                    "Verifique os dados informados e tente novamente."
                ),
            ),
            400,
        )

    # ======================================================
    # 403 - FORBIDDEN
    # ======================================================

    @app.errorhandler(403)
    def forbidden(error):

        current_app.logger.warning(
            "Tentativa de acesso a recurso sem autorização."
        )

        return (
            render_template(
                "errors/403.html"
            ),
            403,
        )

    # ======================================================
    # 404 - NOT FOUND
    # ======================================================

    @app.errorhandler(404)
    def not_found(error):

        return (
            render_template(
                "errors/404.html"
            ),
            404,
        )

    # ======================================================
    # 500 - INTERNAL SERVER ERROR
    # ======================================================

    @app.errorhandler(500)
    def internal_server_error(error):

        # --------------------------------------------------
        # RECUPERAR SESSÃO DO SQLALCHEMY
        # --------------------------------------------------

        try:

            db.session.rollback()

        except Exception:

            current_app.logger.exception(
                (
                    "Falha ao executar rollback "
                    "após erro interno."
                )
            )

        # --------------------------------------------------
        # REGISTRAR O ERRO SOMENTE NO SERVIDOR
        # --------------------------------------------------

        current_app.logger.error(
            "Erro interno não tratado.",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )

        # --------------------------------------------------
        # NÃO EXPOR O TRACEBACK AO USUÁRIO
        # --------------------------------------------------

        return (
            render_template(
                "errors/500.html"
            ),
            500,
        )