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
from app.models.departamento import Departamento
from app.models.colaborador import Colaborador


departamentos_bp = Blueprint(
    "departamentos",
    __name__,
    url_prefix="/departamentos"
)


# ==========================================================
# LISTAR
# ==========================================================

@departamentos_bp.get("/")
@login_required
def listar():

    departamentos = Departamento.query.order_by(
        Departamento.nome
    ).all()

    return render_template(
        "departamentos/listar.html",
        departamentos=departamentos
    )


# ==========================================================
# NOVO
# ==========================================================

@departamentos_bp.route(
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

        if not nome:

            flash(
                "Informe o nome do departamento.",
                "danger"
            )

            return redirect(
                url_for("departamentos.novo")
            )

        existente = Departamento.query.filter_by(
            nome=nome
        ).first()

        if existente:

            flash(
                "Esse departamento já existe.",
                "warning"
            )

            return redirect(
                url_for("departamentos.novo")
            )

        departamento = Departamento(
            nome=nome,
            ativo=True
        )

        db.session.add(departamento)
        db.session.commit()

        flash(
            "Departamento cadastrado com sucesso.",
            "success"
        )

        return redirect(
            url_for("departamentos.listar")
        )

    return render_template(
        "departamentos/novo.html"
    )


# ==========================================================
# EDITAR
# ==========================================================

@departamentos_bp.route(
    "/<int:departamento_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(departamento_id):

    departamento = db.session.get(
        Departamento,
        departamento_id
    )

    if not departamento:

        flash(
            "Departamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("departamentos.listar")
        )

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        if not nome:

            flash(
                "Informe o nome do departamento.",
                "danger"
            )

            return redirect(
                url_for(
                    "departamentos.editar",
                    departamento_id=departamento.id
                )
            )

        existente = Departamento.query.filter(
            Departamento.nome == nome,
            Departamento.id != departamento.id
        ).first()

        if existente:

            flash(
                "Já existe outro departamento com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "departamentos.editar",
                    departamento_id=departamento.id
                )
            )

        departamento.nome = nome

        db.session.commit()

        flash(
            "Departamento atualizado com sucesso.",
            "success"
        )

        return redirect(
            url_for("departamentos.listar")
        )

    return render_template(
        "departamentos/editar.html",
        departamento=departamento
    )


# ==========================================================
# EXCLUIR
# ==========================================================

@departamentos_bp.post(
    "/<int:departamento_id>/excluir"
)
@login_required
def excluir(departamento_id):

    departamento = db.session.get(
        Departamento,
        departamento_id
    )

    if not departamento:

        flash(
            "Departamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("departamentos.listar")
        )

    quantidade = Colaborador.query.filter_by(
        departamento_id=departamento.id
    ).count()

    if quantidade > 0:

        flash(
            (
                "Não é possível excluir este departamento. "
                f"Existem {quantidade} colaborador(es) vinculados."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "departamentos.editar",
                departamento_id=departamento.id
            )
        )

    db.session.delete(departamento)
    db.session.commit()

    flash(
        "Departamento excluído com sucesso.",
        "success"
    )

    return redirect(
        url_for("departamentos.listar")
    )