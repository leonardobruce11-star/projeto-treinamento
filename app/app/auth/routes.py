from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from app.models.usuario import Usuario


# ==========================================================
# BLUEPRINT
# ==========================================================

auth_bp = Blueprint(
    "auth",
    __name__,
)


# ==========================================================
# LOGIN
# ==========================================================

@auth_bp.route(
    "/login",
    methods=[
        "GET",
        "POST",
    ],
)
def login():

    # ======================================================
    # USUÁRIO JÁ AUTENTICADO
    # ======================================================

    if current_user.is_authenticated:

        return redirect(
            url_for(
                "dashboard.index"
            )
        )

    # ======================================================
    # PROCESSAR LOGIN
    # ======================================================

    if request.method == "POST":

        email = request.form.get(
            "email",
            "",
        ).strip().lower()

        senha = request.form.get(
            "senha",
            "",
        )

        # ==================================================
        # CAMPOS OBRIGATÓRIOS
        # ==================================================

        if not email or not senha:

            flash(
                (
                    "Informe o e-mail e "
                    "a senha para continuar."
                ),
                "danger",
            )

            return render_template(
                "auth/login.html"
            )

        # ==================================================
        # BUSCAR USUÁRIO
        # ==================================================

        usuario = (
            Usuario.query
            .filter_by(
                email=email
            )
            .first()
        )

        # ==================================================
        # CREDENCIAIS INVÁLIDAS
        # ==================================================

        if (
            not usuario
            or not usuario.verificar_senha(
                senha
            )
        ):

            flash(
                "E-mail ou senha inválidos.",
                "danger",
            )

            return render_template(
                "auth/login.html"
            )

        # ==================================================
        # USUÁRIO INATIVO
        # ==================================================

        if not usuario.ativo:

            flash(
                (
                    "Este usuário está inativo. "
                    "Entre em contato com o administrador."
                ),
                "warning",
            )

            return render_template(
                "auth/login.html"
            )

        # ==================================================
        # AUTENTICAR
        # ==================================================

        login_user(
            usuario
        )

        session.permanent = True

        flash(
            f"Bem-vindo, {usuario.nome}.",
            "success",
        )

        # ==================================================
        # REDIRECIONAMENTO SEGURO
        # ==================================================

        proxima_pagina = request.args.get(
            "next"
        )

        if (
            proxima_pagina
            and proxima_pagina.startswith("/")
            and not proxima_pagina.startswith("//")
        ):

            return redirect(
                proxima_pagina
            )

        return redirect(
            url_for(
                "dashboard.index"
            )
        )

    return render_template(
        "auth/login.html"
    )


# ==========================================================
# LOGOUT
# ==========================================================

@auth_bp.post("/logout")
@login_required
def logout():

    logout_user()
    session.clear()

    flash(
        "Sessão encerrada com sucesso.",
        "success",
    )

    return redirect(
        url_for("auth.login")
    )