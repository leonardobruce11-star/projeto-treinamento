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
from app.models.grupo import Grupo
from app.models.grupo_participante import GrupoParticipante
from app.models.colaborador import Colaborador
from app.integracoes.gophish.policy import administrador, bloquear_mutacao_avulsa


grupos_bp = Blueprint(
    "grupos",
    __name__,
    url_prefix="/grupos"
)


# ==========================================================
# LISTAR GRUPOS
# ==========================================================

@grupos_bp.get("/")
@login_required
def listar():

    grupos = Grupo.query.order_by(
        Grupo.nome
    ).all()

    return render_template(
        "grupos/listar.html",
        grupos=grupos
    )


# ==========================================================
# CRIAR NOVO GRUPO
# ==========================================================

@grupos_bp.route(
    "/novo",
    methods=["GET", "POST"]
)
@login_required
def novo():

    colaboradores = Colaborador.query.filter_by(
        ativo=True
    ).order_by(
        Colaborador.nome
    ).all()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        colaboradores_ids = request.form.getlist(
            "colaboradores"
        )

        # ------------------------------------------
        # VALIDAÇÃO DO NOME
        # ------------------------------------------

        if not nome:

            flash(
                "Informe o nome do grupo.",
                "danger"
            )

            return redirect(
                url_for("grupos.novo")
            )

        # ------------------------------------------
        # VERIFICAR DUPLICIDADE
        # ------------------------------------------

        existente = Grupo.query.filter_by(
            nome=nome
        ).first()

        if existente:

            flash(
                "Já existe um grupo com esse nome.",
                "warning"
            )

            return redirect(
                url_for("grupos.novo")
            )

        # ------------------------------------------
        # CRIAR GRUPO
        # ------------------------------------------

        grupo = Grupo(
            nome=nome,
            descricao=descricao or None
        )

        db.session.add(grupo)

        # Precisamos do ID antes do commit
        db.session.flush()

        # ------------------------------------------
        # ADICIONAR PARTICIPANTES
        # ------------------------------------------

        for colaborador_id in colaboradores_ids:

            participante = GrupoParticipante(
                grupo_id=grupo.id,
                colaborador_id=int(
                    colaborador_id
                )
            )

            db.session.add(
                participante
            )

        db.session.commit()

        flash(
            "Grupo criado com sucesso.",
            "success"
        )

        return redirect(
            url_for("grupos.listar")
        )

    return render_template(
        "grupos/novo.html",
        colaboradores=colaboradores
    )


# ==========================================================
# EDITAR GRUPO
# ==========================================================

@grupos_bp.route(
    "/<int:grupo_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(grupo_id):

    grupo = db.session.get(
        Grupo,
        grupo_id
    )

    if not grupo:

        flash(
            "Grupo não encontrado.",
            "danger"
        )

        return redirect(
            url_for("grupos.listar")
        )

    colaboradores = Colaborador.query.filter_by(
        ativo=True
    ).order_by(
        Colaborador.nome
    ).all()

    # ======================================================
    # SALVAR ALTERAÇÕES
    # ======================================================

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        colaboradores_ids = request.form.getlist(
            "colaboradores"
        )

        if not nome:

            flash(
                "Informe o nome do grupo.",
                "danger"
            )

            return redirect(
                url_for(
                    "grupos.editar",
                    grupo_id=grupo.id
                )
            )

        # ------------------------------------------
        # EVITAR NOME DUPLICADO
        # ------------------------------------------

        existente = Grupo.query.filter(
            Grupo.nome == nome,
            Grupo.id != grupo.id
        ).first()

        if existente:

            flash(
                "Já existe outro grupo com esse nome.",
                "warning"
            )

            return redirect(
                url_for(
                    "grupos.editar",
                    grupo_id=grupo.id
                )
            )

        # ------------------------------------------
        # ATUALIZAR DADOS
        # ------------------------------------------

        grupo.nome = nome
        grupo.descricao = descricao or None

        # ------------------------------------------
        # VALIDAR PARTICIPANTES SELECIONADOS
        # ------------------------------------------

        try:

            colaboradores_ids = {
                int(colaborador_id)
                for colaborador_id
                in colaboradores_ids
            }

        except ValueError:

            flash(
                "Um ou mais colaboradores são inválidos.",
                "danger"
            )

            return redirect(
                url_for(
                    "grupos.editar",
                    grupo_id=grupo.id
                )
            )

        # ------------------------------------------
        # SOMENTE COLABORADORES ATIVOS
        # PODEM SER ADICIONADOS
        # ------------------------------------------

        colaboradores_ativos = (
            Colaborador.query
            .filter(
                Colaborador.id.in_(
                    colaboradores_ids
                ),
                Colaborador.ativo.is_(True)
            )
            .all()
            if colaboradores_ids
            else []
        )

        ids_ativos_permitidos = {
            colaborador.id
            for colaborador
            in colaboradores_ativos
        }

        if (
            colaboradores_ids
            != ids_ativos_permitidos
        ):

            flash(
                (
                    "Um ou mais colaboradores selecionados "
                    "não estão ativos."
                ),
                "danger"
            )

            return redirect(
                url_for(
                    "grupos.editar",
                    grupo_id=grupo.id
                )
            )

        # ------------------------------------------
        # VÍNCULOS ATUAIS
        # ------------------------------------------

        vinculos_atuais = (
            GrupoParticipante.query
            .filter_by(
                grupo_id=grupo.id
            )
            .all()
        )

        # ------------------------------------------
        # PRESERVAR INATIVOS
        # ------------------------------------------

        for vinculo in vinculos_atuais:

            colaborador = (
                vinculo.colaborador
            )

            if (
                colaborador
                and not colaborador.ativo
            ):

                continue

            if (
                vinculo.colaborador_id
                not in colaboradores_ids
            ):

                db.session.delete(
                    vinculo
                )

        # ------------------------------------------
        # ADICIONAR NOVOS ATIVOS
        # ------------------------------------------

        ids_ja_vinculados = {
            vinculo.colaborador_id
            for vinculo
            in vinculos_atuais
        }

        for colaborador_id in colaboradores_ids:

            if (
                colaborador_id
                not in ids_ja_vinculados
            ):

                participante = (
                    GrupoParticipante(
                        grupo_id=grupo.id,
                        colaborador_id=colaborador_id
                    )
                )

                db.session.add(
                    participante
                )

    # ------------------------------------------
    # SALVAR ALTERAÇÕES
    # ------------------------------------------

        db.session.commit()

        flash(
            "Grupo atualizado com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "grupos.editar",
                grupo_id=grupo.id
            )
        )



    # ======================================================
    # ABRIR TELA DE EDIÇÃO
    # ======================================================

    participantes_ids = {
        vinculo.colaborador_id
        for vinculo in grupo.participantes
    }

    return render_template(
        "grupos/editar.html",
        grupo=grupo,
        colaboradores=colaboradores,
        participantes_ids=participantes_ids
    )


# ==========================================================
# SINCRONIZAR COM GOPHISH
# ==========================================================

@grupos_bp.post(
    "/<int:grupo_id>/sincronizar"
)
@administrador
def sincronizar(grupo_id):
    bloquear_mutacao_avulsa()
