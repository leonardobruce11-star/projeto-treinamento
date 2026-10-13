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
from app.models.cenario import Cenario


cenarios_bp = Blueprint(
    "cenarios",
    __name__,
    url_prefix="/cenarios"
)


@cenarios_bp.get("/")
@login_required
def listar():

    cenarios = Cenario.query.order_by(
        Cenario.nome
    ).all()

    return render_template(
        "cenarios/listar.html",
        cenarios=cenarios
    )


@cenarios_bp.route(
    "/novo",
    methods=["GET", "POST"]
)
@login_required
def novo():

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        nivel = request.form.get(
            "nivel",
            "basico"
        )

        if not nome:

            flash(
                "Informe o nome do cenário.",
                "danger"
            )

            return redirect(
                url_for("cenarios.novo")
            )

        existente = Cenario.query.filter_by(
            nome=nome
        ).first()

        if existente:

            flash(
                "Já existe um cenário com esse nome.",
                "warning"
            )

            return redirect(
                url_for("cenarios.novo")
            )

        cenario = Cenario(
            nome=nome,
            descricao=descricao or None,
            nivel=nivel
        )

        db.session.add(cenario)
        db.session.commit()

        flash(
            "Cenário criado com sucesso.",
            "success"
        )

        return redirect(
            url_for("cenarios.listar")
        )

    return render_template(
        "cenarios/novo.html"
    )


@cenarios_bp.route(
    "/<int:cenario_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(cenario_id):

    cenario = db.session.get(
        Cenario,
        cenario_id
    )

    if not cenario:

        flash(
            "Cenário não encontrado.",
            "danger"
        )

        return redirect(
            url_for("cenarios.listar")
        )

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        nivel = request.form.get(
            "nivel",
            "basico"
        )

        if not nome:

            flash(
                "Informe o nome do cenário.",
                "danger"
            )

            return redirect(
                url_for(
                    "cenarios.editar",
                    cenario_id=cenario.id
                )
            )

        existente = Cenario.query.filter(
            Cenario.nome == nome,
            Cenario.id != cenario.id
        ).first()

        if existente:

            flash(
                "Já existe outro cenário com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "cenarios.editar",
                    cenario_id=cenario.id
                )
            )

        cenario.nome = nome
        cenario.descricao = descricao or None
        cenario.nivel = nivel

        db.session.commit()

        flash(
            "Cenário atualizado.",
            "success"
        )

        return redirect(
            url_for("cenarios.listar")
        )

    return render_template(
        "cenarios/editar.html",
        cenario=cenario
    )