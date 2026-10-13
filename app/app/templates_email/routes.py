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
from app.models.template_email import TemplateEmail
from app.models.cenario import Cenario
from app.integracoes.gophish.policy import administrador, bloquear_mutacao_avulsa


templates_email_bp = Blueprint(
    "templates_email",
    __name__,
    url_prefix="/templates-email"
)


# ==========================================================
# LISTAR
# ==========================================================

@templates_email_bp.get("/")
@login_required
def listar():

    templates = TemplateEmail.query.order_by(
        TemplateEmail.nome
    ).all()

    return render_template(
        "templates_email/listar.html",
        templates=templates
    )


# ==========================================================
# NOVO
# ==========================================================

@templates_email_bp.route(
    "/novo",
    methods=["GET", "POST"]
)
@login_required
def novo():

    cenarios = Cenario.query.filter_by(
        ativo=True
    ).order_by(
        Cenario.nome
    ).all()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        assunto = request.form.get(
            "assunto",
            ""
        ).strip()

        conteudo_html = request.form.get(
            "conteudo_html",
            ""
        ).strip()

        cenario_id = request.form.get(
            "cenario_id"
        )

        if (
            not nome
            or not assunto
            or not conteudo_html
            or not cenario_id
        ):

            flash(
                "Preencha todos os campos obrigatórios.",
                "danger"
            )

            return redirect(
                url_for("templates_email.novo")
            )

        existente = TemplateEmail.query.filter_by(
            nome=nome
        ).first()

        if existente:

            flash(
                "Já existe um template com esse nome.",
                "warning"
            )

            return redirect(
                url_for("templates_email.novo")
            )

        template = TemplateEmail(
            nome=nome,
            assunto=assunto,
            conteudo_html=conteudo_html,
            cenario_id=int(cenario_id)
        )

        db.session.add(template)
        db.session.commit()

        flash(
            "Template criado com sucesso.",
            "success"
        )

        return redirect(
            url_for("templates_email.listar")
        )

    return render_template(
        "templates_email/novo.html",
        cenarios=cenarios
    )


# ==========================================================
# EDITAR
# ==========================================================

@templates_email_bp.route(
    "/<int:template_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(template_id):

    template = db.session.get(
        TemplateEmail,
        template_id
    )

    if not template:

        flash(
            "Template não encontrado.",
            "danger"
        )

        return redirect(
            url_for("templates_email.listar")
        )

    cenarios = Cenario.query.filter_by(
        ativo=True
    ).order_by(
        Cenario.nome
    ).all()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        assunto = request.form.get(
            "assunto",
            ""
        ).strip()

        conteudo_html = request.form.get(
            "conteudo_html",
            ""
        ).strip()

        cenario_id = request.form.get(
            "cenario_id"
        )

        if (
            not nome
            or not assunto
            or not conteudo_html
            or not cenario_id
        ):

            flash(
                "Preencha todos os campos obrigatórios.",
                "danger"
            )

            return redirect(
                url_for(
                    "templates_email.editar",
                    template_id=template.id
                )
            )

        existente = TemplateEmail.query.filter(
            TemplateEmail.nome == nome,
            TemplateEmail.id != template.id
        ).first()

        if existente:

            flash(
                "Já existe outro template com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "templates_email.editar",
                    template_id=template.id
                )
            )

        template.nome = nome
        template.assunto = assunto
        template.conteudo_html = conteudo_html
        template.cenario_id = int(cenario_id)

        db.session.commit()

        flash(
            (
                "Template atualizado. "
                "Sincronize novamente com o Gophish."
            ),
            "success"
        )

        return redirect(
            url_for("templates_email.listar")
        )

    return render_template(
        "templates_email/editar.html",
        template=template,
        cenarios=cenarios
    )


# ==========================================================
# SINCRONIZAR COM GOPHISH
# ==========================================================

@templates_email_bp.post(
    "/<int:template_id>/sincronizar"
)
@administrador
def sincronizar(template_id):
    bloquear_mutacao_avulsa()
