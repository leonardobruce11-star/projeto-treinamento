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
from app.models.categoria_treinamento import CategoriaTreinamento


categorias_treinamento_bp = Blueprint(
    "categorias_treinamento",
    __name__,
    url_prefix="/categorias-treinamento"
)


# ==========================================================
# LISTAR
# ==========================================================

@categorias_treinamento_bp.get("/")
@login_required
def listar():

    categorias = CategoriaTreinamento.query.order_by(
        CategoriaTreinamento.nome
    ).all()

    total_ativas = sum(
        1 for categoria in categorias
        if categoria.ativo
    )

    total_inativas = len(categorias) - total_ativas

    return render_template(
        "categorias_treinamento/listar.html",
        categorias=categorias,
        total_ativas=total_ativas,
        total_inativas=total_inativas,
    )


# ==========================================================
# NOVA CATEGORIA
# ==========================================================

@categorias_treinamento_bp.route(
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

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        if not nome:

            flash(
                "Informe o nome da categoria.",
                "danger"
            )

            return redirect(
                url_for(
                    "categorias_treinamento.nova"
                )
            )

        existente = CategoriaTreinamento.query.filter(
            db.func.lower(
                CategoriaTreinamento.nome
            ) == nome.lower()
        ).first()

        if existente:

            flash(
                "Já existe uma categoria com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "categorias_treinamento.nova"
                )
            )

        categoria = CategoriaTreinamento(
            nome=nome,
            descricao=descricao or None,
            ativo=True,
        )

        db.session.add(
            categoria
        )

        db.session.commit()

        flash(
            "Categoria cadastrada com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "categorias_treinamento.listar"
            )
        )

    return render_template(
        "categorias_treinamento/nova.html"
    )


# ==========================================================
# EDITAR
# ==========================================================

@categorias_treinamento_bp.route(
    "/<int:categoria_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(categoria_id):

    categoria = db.session.get(
        CategoriaTreinamento,
        categoria_id
    )

    if not categoria:

        flash(
            "Categoria não encontrada.",
            "danger"
        )

        return redirect(
            url_for(
                "categorias_treinamento.listar"
            )
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

        if not nome:

            flash(
                "Informe o nome da categoria.",
                "danger"
            )

            return redirect(
                url_for(
                    "categorias_treinamento.editar",
                    categoria_id=categoria.id
                )
            )

        existente = CategoriaTreinamento.query.filter(
            db.func.lower(
                CategoriaTreinamento.nome
            ) == nome.lower(),
            CategoriaTreinamento.id != categoria.id
        ).first()

        if existente:

            flash(
                "Já existe outra categoria com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "categorias_treinamento.editar",
                    categoria_id=categoria.id
                )
            )

        categoria.nome = nome
        categoria.descricao = descricao or None

        db.session.commit()

        flash(
            "Categoria atualizada com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "categorias_treinamento.listar"
            )
        )

    return render_template(
        "categorias_treinamento/editar.html",
        categoria=categoria
    )


# ==========================================================
# ATIVAR / DESATIVAR
# ==========================================================

@categorias_treinamento_bp.post(
    "/<int:categoria_id>/status"
)
@login_required
def alterar_status(categoria_id):

    categoria = db.session.get(
        CategoriaTreinamento,
        categoria_id
    )

    if not categoria:

        flash(
            "Categoria não encontrada.",
            "danger"
        )

        return redirect(
            url_for(
                "categorias_treinamento.listar"
            )
        )

    categoria.ativo = not categoria.ativo

    db.session.commit()

    if categoria.ativo:

        flash(
            "Categoria ativada com sucesso.",
            "success"
        )

    else:

        flash(
            "Categoria desativada com sucesso.",
            "warning"
        )

    return redirect(
        url_for(
            "categorias_treinamento.listar"
        )
    )
