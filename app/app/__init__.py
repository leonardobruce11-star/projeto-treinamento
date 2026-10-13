import os

from datetime import timedelta

from flask import (
    Flask,
    flash,
    redirect,
    request,
    url_for,
)

from dotenv import load_dotenv

from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import (
    db,
    migrate,
    login_manager,
    csrf,
)


def create_app():

    # ==========================================================
    # VARIÁVEIS DE AMBIENTE
    # ==========================================================

    load_dotenv()

    # ==========================================================
    # CRIAÇÃO DA APLICAÇÃO
    # ==========================================================

    app = Flask(__name__)

    from log_privacidade import configurar_logs
    configurar_logs(app)

    from .utils.tempo import formatar_tempo_ativo
    app.add_template_filter(formatar_tempo_ativo, "tempo_ativo")

    # ==========================================================
    # PROXY REVERSO - APACHE2
    # ==========================================================

    # A aplicação recebe as conexões externas através do Apache2.
    #
    # x_for=1:
    # permite que o Flask reconheça o IP original do cliente
    # informado pelo proxy.
    #
    # x_proto=1:
    # permite que o Flask reconheça que a conexão original
    # foi realizada através de HTTPS.
    #
    # Existe apenas um proxy confiável entre o cliente
    # e a aplicação: Apache2.

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
    )

    # ==========================================================
    # AMBIENTE
    # ==========================================================

    ambiente = os.getenv(
        "APP_ENV",
        "development",
    ).lower()

    app.config["APP_ENV"] = ambiente

    from .integracoes.gophish.config import ConfiguracaoGophish
    from .integracoes.gophish.service import GophishService
    gophish_config = ConfiguracaoGophish.do_ambiente()
    app.extensions['gophish_config'] = gophish_config
    app.extensions['gophish_service'] = GophishService(gophish_config)

    from .utils.mail_convites import ConfiguracaoMail, ConfiguracaoMailInvalida
    try:
        mail_config = ConfiguracaoMail.do_ambiente()
    except ConfiguracaoMailInvalida:
        if os.getenv("MAIL_ENABLED", "false").lower() != "false":
            raise
        app.logger.warning("Configuração de mail inválida; convites desabilitados.")
        mail_config = ConfiguracaoMail.do_ambiente({})
    app.extensions["mail_convites_config"] = mail_config

    # ==========================================================
    # SECRET KEY
    # ==========================================================

    secret_key = os.getenv(
        "SECRET_KEY"
    )

    if not secret_key:

        raise RuntimeError(
            (
                "SECRET_KEY não configurada. "
                "Verifique o arquivo .env."
            )
        )

    app.config["SECRET_KEY"] = secret_key

    # ==========================================================
    # BANCO DE DADOS
    # ==========================================================

    database_url = os.getenv(
        "DATABASE_URL"
    )

    if not database_url:

        raise RuntimeError(
            (
                "DATABASE_URL não configurada. "
                "Verifique o arquivo .env."
            )
        )

    app.config[
        "SQLALCHEMY_DATABASE_URI"
    ] = database_url

    app.config[
        "SQLALCHEMY_TRACK_MODIFICATIONS"
    ] = False

    # ==========================================================
    # CONFIGURAÇÃO DE SESSÃO
    # ==========================================================

    app.config[
        "SESSION_COOKIE_HTTPONLY"
    ] = True

    app.config[
        "SESSION_COOKIE_SAMESITE"
    ] = "Lax"

    # Em desenvolvimento local usamos HTTP.
    # Em produção utilizamos HTTPS.
    app.config[
        "SESSION_COOKIE_SECURE"
    ] = (
        ambiente == "production"
    )

    # Sessão autenticada válida por 30 minutos.
    app.config[
        "PERMANENT_SESSION_LIFETIME"
    ] = timedelta(
        minutes=30
    )

    # ==========================================================
    # CONFIGURAÇÃO CSRF
    # ==========================================================

    # Proteção CSRF automática global habilitada.
    app.config[
        "WTF_CSRF_CHECK_DEFAULT"
    ] = True

    app.config[
        "WTF_CSRF_TIME_LIMIT"
    ] = 3600

    app.config[
        "WTF_CSRF_SSL_STRICT"
    ] = (
        ambiente == "production"
    )

    # ==========================================================
    # INICIALIZAÇÃO DAS EXTENSÕES
    # ==========================================================

    db.init_app(
        app
    )

    migrate.init_app(
        app,
        db
    )

    login_manager.init_app(
        app
    )

    csrf.init_app(
        app
    )

    # ==========================================================
    # FLASK-LOGIN
    # ==========================================================

    login_manager.login_view = (
        "auth.login"
    )

    login_manager.login_message = (
        "Faça login para acessar esta página."
    )

    login_manager.login_message_category = (
        "warning"
    )

    # ==========================================================
    # MODELOS
    # ==========================================================

    from . import models

    from .models.usuario import Usuario

    # ==========================================================
    # CARREGAMENTO DO USUÁRIO
    # ==========================================================

    @login_manager.unauthorized_handler
    def requer_login():
        flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for("auth.login", next=request.path))

    @login_manager.user_loader
    def load_user(user_id):

        try:

            usuario_id = int(
                user_id
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

        usuario = db.session.get(
            Usuario,
            usuario_id
        )
        return usuario if usuario is not None and usuario.ativo else None

    # ==========================================================
    # HEALTH
    # ==========================================================

    from .routes.health import (
        health_bp
    )

    app.register_blueprint(
        health_bp
    )

    # ==========================================================
    # AUTENTICAÇÃO
    # ==========================================================

    from .auth.routes import (
        auth_bp
    )

    app.register_blueprint(
        auth_bp
    )

    # ==========================================================
    # DASHBOARD
    # ==========================================================

    from .dashboard.routes import (
        dashboard_bp
    )

    app.register_blueprint(
        dashboard_bp
    )

    # ==========================================================
    # DEPARTAMENTOS
    # ==========================================================

    from .departamentos.routes import (
        departamentos_bp
    )

    app.register_blueprint(
        departamentos_bp
    )

    # ==========================================================
    # COLABORADORES
    # ==========================================================

    from .colaboradores.routes import (
        colaboradores_bp
    )

    app.register_blueprint(
        colaboradores_bp
    )

    # ==========================================================
    # INTEGRAÇÃO GOPHISH
    # ==========================================================

    from .integracoes.gophish.routes import (
        gophish_bp
    )

    app.register_blueprint(
        gophish_bp
    )

    # ==========================================================
    # GRUPOS
    # ==========================================================

    from .grupos.routes import (
        grupos_bp
    )

    app.register_blueprint(
        grupos_bp
    )

    # ==========================================================
    # CENÁRIOS
    # ==========================================================

    from .cenarios.routes import (
        cenarios_bp
    )

    app.register_blueprint(
        cenarios_bp
    )

    # ==========================================================
    # TEMPLATES DE E-MAIL
    # ==========================================================

    from .templates_email.routes import (
        templates_email_bp
    )

    app.register_blueprint(
        templates_email_bp
    )

    # ==========================================================
    # LANDING PAGES
    # ==========================================================

    from .landing_pages.routes import (
        landing_pages_bp
    )

    app.register_blueprint(
        landing_pages_bp
    )


    # ==========================================================
    # CATEGORIAS DE TREINAMENTO
    # ==========================================================

    from .categorias_treinamento.routes import (
        categorias_treinamento_bp
    )

    app.register_blueprint(
        categorias_treinamento_bp
    )

    # ==========================================================
    # TREINAMENTOS
    # ==========================================================

    from .treinamentos.routes import (
        treinamentos_bp
    )

    app.register_blueprint(
        treinamentos_bp
    )

    # ==========================================================
    # PORTAL DE TREINAMENTO
    # ==========================================================

    from .portal_treinamento.routes import (
        portal_treinamento_bp
    )

    app.register_blueprint(
        portal_treinamento_bp
    )

    # ==========================================================
    # CAMPANHAS
    # ==========================================================

    from .campanhas.routes import (
        campanhas_bp
    )

    app.register_blueprint(
        campanhas_bp
    )

    from .convites.routes import convites_bp
    from .convites.cli import registrar_cli
    app.register_blueprint(convites_bp)

    from .simulacoes.routes import simulacoes_bp
    app.register_blueprint(simulacoes_bp)
    from .simulacoes.publico import educacao_simulacao_bp
    app.register_blueprint(educacao_simulacao_bp)
    registrar_cli(app)

    # ==========================================================
    # TRATAMENTO GLOBAL DE ERROS
    # ==========================================================

    from .errors import (
        register_error_handlers
    )

    register_error_handlers(
        app
    )

    # ==========================================================
    # ROTA PRINCIPAL
    # ==========================================================

    @app.route("/")
    def index():

        return redirect(
            url_for(
                "auth.login"
            )
        )

    # ==========================================================
    # RETORNO DA APLICAÇÃO
    # ==========================================================

    return app
