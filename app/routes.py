from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento


colaboradores_bp = Blueprint(
    "colaboradores",
    __name__,
    url_prefix="/colaboradores"
)


@colaboradores_bp.get("/")
@login_required
def listar():

    colaboradores = Colaborador.query.order_by(
        Colaborador.nome
    ).all()

    return render_template(
        "colaboradores/listar.html",
        colaboradores=colaboradores
    )


@colaboradores_bp.route(
    "/novo",
    methods=["GET", "POST"]
)
@login_required
def novo():

    departamentos = Departamento.query.filter_by(
        ativo=True
    ).order_by(
        Departamento.nome
    ).all()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        cargo = request.form.get(
            "cargo",
            ""
        ).strip()

        departamento_id = request.form.get(
            "departamento_id"
        )

        if not nome or not email or not departamento_id:

            flash(
                "Preencha os campos obrigatórios.",
                "danger"
            )

            return redirect(
                url_for("colaboradores.novo")
            )

        existente = Colaborador.query.filter_by(
            email=email
        ).first()

        if existente:

            flash(
                "Já existe colaborador com esse e-mail.",
                "warning"
            )

            return redirect(
                url_for("colaboradores.novo")
            )

        colaborador = Colaborador(
            nome=nome,
            email=email,
            cargo=cargo or None,
            departamento_id=int(departamento_id)
        )

        db.session.add(colaborador)
        db.session.commit()

        flash(
            "Colaborador cadastrado.",
            "success"
        )

        return redirect(
            url_for("colaboradores.listar")
        )

    return render_template(
        "colaboradores/novo.html",
        departamentos=departamentos
    )