from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)

from flask_login import login_required

from app.extensions import db
from app.models.landing_page import LandingPage
from app.integracoes.gophish.policy import administrador, bloquear_mutacao_avulsa


landing_pages_bp = Blueprint(
    "landing_pages",
    __name__,
    url_prefix="/landing-pages"
)


# ==========================================================
# LISTAR
# ==========================================================

@landing_pages_bp.get("/")
@login_required
def listar():

    paginas = LandingPage.query.order_by(
        LandingPage.nome
    ).all()

    return render_template(
        "landing_pages/listar.html",
        paginas=paginas
    )


# ==========================================================
# NOVA
# ==========================================================

@landing_pages_bp.route(
    "/nova",
    methods=["GET", "POST"]
)
@login_required
def nova():

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        mensagem = request.form.get(
            "mensagem",
            ""
        ).strip()

        conteudo_html = request.form.get(
            "conteudo_html",
            ""
        ).strip()

        if (
            not nome
            or not titulo
            or not mensagem
            or not conteudo_html
        ):

            flash(
                "Preencha todos os campos obrigatórios.",
                "danger"
            )

            return redirect(
                url_for("landing_pages.nova")
            )

        existente = LandingPage.query.filter_by(
            nome=nome
        ).first()

        if existente:

            flash(
                "Já existe uma landing page com esse nome.",
                "warning"
            )

            return redirect(
                url_for("landing_pages.nova")
            )

        pagina = LandingPage(
            nome=nome,
            titulo=titulo,
            mensagem=mensagem,
            conteudo_html=conteudo_html
        )

        db.session.add(pagina)
        db.session.commit()

        flash(
            "Landing page criada com sucesso.",
            "success"
        )

        return redirect(
            url_for("landing_pages.listar")
        )

    return render_template(
        "landing_pages/nova.html"
    )


# ==========================================================
# EDITAR
# ==========================================================

@landing_pages_bp.route(
    "/<int:pagina_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(pagina_id):

    pagina = db.session.get(
        LandingPage,
        pagina_id
    )

    if not pagina:

        flash(
            "Landing page não encontrada.",
            "danger"
        )

        return redirect(
            url_for("landing_pages.listar")
        )

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        mensagem = request.form.get(
            "mensagem",
            ""
        ).strip()

        conteudo_html = request.form.get(
            "conteudo_html",
            ""
        ).strip()

        if (
            not nome
            or not titulo
            or not mensagem
            or not conteudo_html
        ):

            flash(
                "Preencha todos os campos obrigatórios.",
                "danger"
            )

            return redirect(
                url_for(
                    "landing_pages.editar",
                    pagina_id=pagina.id
                )
            )

        existente = LandingPage.query.filter(
            LandingPage.nome == nome,
            LandingPage.id != pagina.id
        ).first()

        if existente:

            flash(
                "Já existe outra landing page com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "landing_pages.editar",
                    pagina_id=pagina.id
                )
            )

        pagina.nome = nome
        pagina.titulo = titulo
        pagina.mensagem = mensagem
        pagina.conteudo_html = conteudo_html

        db.session.commit()

        flash(
            (
                "Landing page atualizada. "
                "Sincronize novamente com o Gophish."
            ),
            "success"
        )

        return redirect(
            url_for("landing_pages.listar")
        )

    return render_template(
        "landing_pages/editar.html",
        pagina=pagina
    )


# ==========================================================
# SINCRONIZAR
# ==========================================================

@landing_pages_bp.post(
    "/<int:pagina_id>/sincronizar"
)
@administrador
def sincronizar(pagina_id):
    bloquear_mutacao_avulsa()
