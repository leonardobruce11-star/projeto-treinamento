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
from app.models.treinamento import Treinamento
from app.models.categoria_treinamento import CategoriaTreinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.questao import Questao
from app.models.alternativa import Alternativa
from app.utils.estrutura_treinamento import (
    estrutura_bloqueada, EstruturaTreinamentoBloqueada, MENSAGEM_ESTRUTURA_BLOQUEADA,
    AvaliacaoTreinamentoBloqueada,
)


treinamentos_bp = Blueprint(
    "treinamentos",
    __name__,
    url_prefix="/treinamentos"
)


# ==========================================================
# LISTAR
# ==========================================================

@treinamentos_bp.get("/")
@login_required
def listar():

    treinamentos = Treinamento.query.order_by(
        Treinamento.titulo
    ).all()

    return render_template(
        "treinamentos/listar.html",
        treinamentos=treinamentos
    )


# ==========================================================
# NOVO
# ==========================================================

@treinamentos_bp.route(
    "/novo",
    methods=["GET", "POST"]
)
@login_required
def novo():

    categorias = CategoriaTreinamento.query.filter_by(
        ativo=True
    ).order_by(
        CategoriaTreinamento.nome
    ).all()

    if request.method == "POST":

        titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        categoria_id = request.form.get(
            "categoria_id",
            type=int
        )

        if not titulo:

            flash(
                "Informe o título do treinamento.",
                "danger"
            )

            return redirect(
                url_for("treinamentos.novo")
            )

        existente = Treinamento.query.filter_by(
            titulo=titulo
        ).first()

        if existente:

            flash(
                "Já existe um treinamento com esse título.",
                "warning"
            )

            return redirect(
                url_for("treinamentos.novo")
            )

        categoria = None

        if categoria_id:

            categoria = CategoriaTreinamento.query.filter_by(
                id=categoria_id,
                ativo=True
            ).first()

            if not categoria:

                flash(
                    "A categoria selecionada não está disponível.",
                    "danger"
                )

                return redirect(
                    url_for("treinamentos.novo")
                )

        treinamento = Treinamento(
            titulo=titulo,
            descricao=descricao or None,
            categoria_id=(
                categoria.id
                if categoria
                else None
            )
        )

        db.session.add(
            treinamento
        )

        db.session.commit()

        flash(
            "Treinamento criado com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "treinamentos.editar",
                treinamento_id=treinamento.id
            )
        )

    return render_template(
        "treinamentos/novo.html",
        categorias=categorias
    )


# ==========================================================
# EDITAR TREINAMENTO
# ==========================================================

@treinamentos_bp.route(
    "/<int:treinamento_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(treinamento_id):

    treinamento = db.session.get(
        Treinamento,
        treinamento_id
    )

    if not treinamento:

        flash(
            "Treinamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    categorias = CategoriaTreinamento.query.filter_by(
        ativo=True
    ).order_by(
        CategoriaTreinamento.nome
    ).all()

    # Caso o treinamento esteja vinculado a uma categoria
    # atualmente inativa, ela continua disponível na edição
    # para preservar o histórico.
    if (
        treinamento.categoria
        and not treinamento.categoria.ativo
        and treinamento.categoria not in categorias
    ):
        categorias.append(
            treinamento.categoria
        )

    if request.method == "POST":

        titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        descricao = request.form.get(
            "descricao",
            ""
        ).strip()

        categoria_id = request.form.get(
            "categoria_id",
            type=int
        )

        if not titulo:

            flash(
                "Informe o título.",
                "danger"
            )

            return redirect(
                url_for(
                    "treinamentos.editar",
                    treinamento_id=treinamento.id
                )
            )

        categoria = None

        if categoria_id:

            categoria = db.session.get(
                CategoriaTreinamento,
                categoria_id
            )

            if not categoria:

                flash(
                    "Categoria selecionada não encontrada.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "treinamentos.editar",
                        treinamento_id=treinamento.id
                    )
                )

            # Categoria inativa só pode permanecer vinculada
            # se já for a categoria atual deste treinamento.
            if (
                not categoria.ativo
                and treinamento.categoria_id != categoria.id
            ):

                flash(
                    "A categoria selecionada está inativa.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "treinamentos.editar",
                        treinamento_id=treinamento.id
                    )
                )

        treinamento.titulo = titulo
        treinamento.descricao = descricao or None
        treinamento.categoria_id = (
            categoria.id
            if categoria
            else None
        )

        db.session.commit()

        flash(
            "Treinamento atualizado.",
            "success"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    return render_template(
        "treinamentos/editar.html",
        treinamento=treinamento,
        categorias=categorias
    )


# ==========================================================
# NOVO MÓDULO
# ==========================================================

@treinamentos_bp.route(
    "/<int:treinamento_id>/modulos/novo",
    methods=["GET", "POST"]
)
@login_required
def novo_modulo(treinamento_id):

    treinamento = db.session.get(
        Treinamento,
        treinamento_id
    )

    if not treinamento:

        flash(
            "Treinamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    if estrutura_bloqueada(treinamento.id, db.session):
        flash(MENSAGEM_ESTRUTURA_BLOQUEADA, "warning")
        return redirect(url_for("treinamentos.editar", treinamento_id=treinamento.id))

    if request.method == "POST":

        try:
            ordem = int(request.form.get("ordem", "1"))
            if not 1 <= ordem <= 2147483647:
                raise ValueError
        except (TypeError, ValueError):
            flash("Informe uma ordem válida.", "danger")
            return redirect(url_for("treinamentos.novo_modulo", treinamento_id=treinamento.id))

        titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        conteudo = request.form.get(
            "conteudo",
            ""
        ).strip()

        if not titulo or not conteudo:

            flash(
                "Informe título e conteúdo do módulo.",
                "danger"
            )

            return redirect(
                url_for(
                    "treinamentos.novo_modulo",
                    treinamento_id=treinamento.id
                )
            )

        modulo = ModuloTreinamento(
            treinamento_id=treinamento.id,
            titulo=titulo,
            conteudo=conteudo,
            ordem=ordem
        )

        db.session.add(modulo)
        try:
            db.session.commit()
        except EstruturaTreinamentoBloqueada:
            db.session.rollback()
            flash(MENSAGEM_ESTRUTURA_BLOQUEADA, "warning")
            return redirect(url_for("treinamentos.editar", treinamento_id=treinamento_id))

        flash(
            "Módulo criado com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "treinamentos.editar",
                treinamento_id=treinamento.id
            )
        )

    return render_template(
        "treinamentos/novo_modulo.html",
        treinamento=treinamento
    )


# ==========================================================
# EDITAR MÓDULO
# ==========================================================

@treinamentos_bp.route(
    "/modulos/<int:modulo_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar_modulo(modulo_id):

    modulo = db.session.get(
        ModuloTreinamento,
        modulo_id
    )

    if not modulo:

        flash(
            "Módulo não encontrado.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    if request.method == "POST":

        try:
            ordem = int(request.form.get("ordem", modulo.ordem))
            if not 1 <= ordem <= 2147483647:
                raise ValueError
        except (TypeError, ValueError):
            flash("Informe uma ordem válida.", "danger")
            return redirect(url_for("treinamentos.editar_modulo", modulo_id=modulo.id))

        modulo.titulo = request.form.get(
            "titulo",
            ""
        ).strip()

        modulo.conteudo = request.form.get(
            "conteudo",
            ""
        ).strip()

        modulo.ordem = ordem

        try:
            db.session.commit()
        except EstruturaTreinamentoBloqueada:
            db.session.rollback()
            flash(MENSAGEM_ESTRUTURA_BLOQUEADA, "warning")
            return redirect(url_for("treinamentos.editar_modulo", modulo_id=modulo_id))

        flash(
            "Módulo atualizado.",
            "success"
        )

        return redirect(
            url_for(
                "treinamentos.editar",
                treinamento_id=modulo.treinamento_id
            )
        )

    return render_template(
        "treinamentos/editar_modulo.html",
        modulo=modulo,
        estrutura_bloqueada=estrutura_bloqueada(modulo.treinamento_id, db.session)
    )

# ==========================================================
# NOVA QUESTÃO
# ==========================================================

@treinamentos_bp.route(
    "/<int:treinamento_id>/questoes/nova",
    methods=["GET", "POST"]
)
@login_required
def nova_questao(treinamento_id):

    treinamento = db.session.get(
        Treinamento,
        treinamento_id
    )

    if not treinamento:
        flash(
            "Treinamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    if estrutura_bloqueada(treinamento.id, db.session):
        raise AvaliacaoTreinamentoBloqueada()

    if request.method == "POST":

        try:
            ordem = int(request.form.get("ordem", "1"))
            if not 1 <= ordem <= 2147483647:
                raise ValueError
        except (TypeError, ValueError):
            flash("Informe uma ordem válida.", "danger")
            return redirect(url_for("treinamentos.nova_questao", treinamento_id=treinamento.id))

        enunciado = request.form.get(
            "enunciado",
            ""
        ).strip()

        alternativas = [
            request.form.get("alternativa_1", "").strip(),
            request.form.get("alternativa_2", "").strip(),
            request.form.get("alternativa_3", "").strip(),
            request.form.get("alternativa_4", "").strip(),
        ]

        correta = request.form.get(
            "correta"
        )

        if (
            not enunciado
            or not all(alternativas)
            or correta not in {"1", "2", "3", "4"}
        ):
            flash(
                "Preencha a questão, as quatro alternativas e indique a resposta correta.",
                "danger"
            )

            return redirect(
                url_for(
                    "treinamentos.nova_questao",
                    treinamento_id=treinamento.id
                )
            )

        questao = Questao(
            treinamento_id=treinamento.id,
            enunciado=enunciado,
            ordem=ordem
        )

        db.session.add(questao)
        db.session.flush()

        for indice, texto in enumerate(
            alternativas,
            start=1
        ):

            alternativa = Alternativa(
                questao_id=questao.id,
                texto=texto,
                correta=(str(indice) == correta)
            )

            db.session.add(alternativa)

        db.session.commit()

        flash(
            "Questão criada com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "treinamentos.editar",
                treinamento_id=treinamento.id
            )
        )

    return render_template(
        "treinamentos/nova_questao.html",
        treinamento=treinamento
    )

# ==========================================================
# EDITAR QUESTÃO
# ==========================================================

@treinamentos_bp.route(
    "/questoes/<int:questao_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar_questao(questao_id):

    questao = db.session.get(
        Questao,
        questao_id
    )

    if not questao:

        flash(
            "Questão não encontrada.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    treinamento = questao.treinamento

    if estrutura_bloqueada(treinamento.id, db.session):
        raise AvaliacaoTreinamentoBloqueada()

    alternativas_existentes = sorted(
        questao.alternativas,
        key=lambda alternativa: alternativa.id
    )

    # A estrutura atual do sistema trabalha
    # obrigatoriamente com quatro alternativas.
    if len(alternativas_existentes) != 4:

        flash(
            (
                "Esta questão possui uma estrutura "
                "de alternativas inválida."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "treinamentos.visualizar",
                treinamento_id=treinamento.id
            )
        )

    # ======================================================
    # SALVAR ALTERAÇÕES
    # ======================================================

    if request.method == "POST":

        enunciado = request.form.get(
            "enunciado",
            ""
        ).strip()

        ordem = request.form.get(
            "ordem",
            "1"
        ).strip()

        alternativas_texto = [
            request.form.get(
                "alternativa_1",
                ""
            ).strip(),

            request.form.get(
                "alternativa_2",
                ""
            ).strip(),

            request.form.get(
                "alternativa_3",
                ""
            ).strip(),

            request.form.get(
                "alternativa_4",
                ""
            ).strip(),
        ]

        correta = request.form.get(
            "correta"
        )

        # ------------------------------------------
        # VALIDAR CAMPOS
        # ------------------------------------------

        if (
            not enunciado
            or not all(alternativas_texto)
            or correta not in {
                "1",
                "2",
                "3",
                "4"
            }
        ):

            flash(
                (
                    "Preencha a questão, as quatro "
                    "alternativas e indique a "
                    "resposta correta."
                ),
                "danger"
            )

            return redirect(
                url_for(
                    "treinamentos.editar_questao",
                    questao_id=questao.id
                )
            )

        # ------------------------------------------
        # VALIDAR ORDEM
        # ------------------------------------------

        try:

            ordem = int(
                ordem
            )

            if not 1 <= ordem <= 2147483647:
                raise ValueError

        except ValueError:

            flash(
                "Informe uma ordem válida.",
                "danger"
            )

            return redirect(
                url_for(
                    "treinamentos.editar_questao",
                    questao_id=questao.id
                )
            )

        # ------------------------------------------
        # ATUALIZAR QUESTÃO
        # ------------------------------------------

        questao.enunciado = enunciado
        questao.ordem = ordem

        # ------------------------------------------
        # ATUALIZAR ALTERNATIVAS
        # ------------------------------------------

        for indice, alternativa in enumerate(
            alternativas_existentes,
            start=1
        ):

            alternativa.texto = (
                alternativas_texto[
                    indice - 1
                ]
            )

            alternativa.correta = (
                str(indice)
                == correta
            )

        db.session.commit()

        flash(
            "Questão atualizada com sucesso.",
            "success"
        )

        return redirect(
            url_for(
                "treinamentos.visualizar",
                treinamento_id=treinamento.id
            )
        )

    return render_template(
        "treinamentos/editar_questao.html",
        treinamento=treinamento,
        questao=questao,
        alternativas=alternativas_existentes
    )

# ==========================================================
# PRÉ-VISUALIZAR TREINAMENTO
# ==========================================================

@treinamentos_bp.get(
    "/<int:treinamento_id>/visualizar"
)
@login_required
def visualizar(treinamento_id):

    treinamento = db.session.get(
        Treinamento,
        treinamento_id
    )

    if not treinamento:

        flash(
            "Treinamento não encontrado.",
            "danger"
        )

        return redirect(
            url_for("treinamentos.listar")
        )

    modulos = list(
        treinamento.modulos
    )

    questoes = list(
        treinamento.questoes

    )

    return render_template(
        "treinamentos/visualizar.html",
        treinamento=treinamento,
        modulos=modulos,
        questoes=questoes
    )


@treinamentos_bp.get("/feedback")
@login_required
def feedback():
    from flask import abort
    from app.utils.metricas_feedback import (
        consulta_metricas_feedback, indicadores_feedback, comentarios_feedback,
    )

    def inteiro(chave, padrao=None):
        valor = request.args.get(chave, type=int)
        return valor if valor is not None and 0 < valor <= 2147483647 else padrao

    treinamento_id = inteiro('treinamento_id')
    categoria_id = inteiro('categoria_id')
    if treinamento_id is not None and db.session.get(Treinamento, treinamento_id) is None:
        abort(404)
    consulta = consulta_metricas_feedback(treinamento_id, categoria_id)
    return render_template('treinamentos/feedback.html',
        indicadores=indicadores_feedback(consulta),
        metricas=list(db.session.execute(consulta).mappings()),
        comentarios=comentarios_feedback(consulta, inteiro('page', 1)),
        treinamentos=db.session.execute(db.select(Treinamento.id, Treinamento.titulo)
            .order_by(Treinamento.titulo, Treinamento.id)).all(),
        categorias=CategoriaTreinamento.query.order_by(CategoriaTreinamento.nome).all(),
        treinamento_id=treinamento_id, categoria_id=categoria_id,
    )


@treinamentos_bp.errorhandler(AvaliacaoTreinamentoBloqueada)
def avaliacao_bloqueada(erro):
    db.session.rollback()
    flash(str(erro), "danger")
    return redirect(url_for("treinamentos.listar"))
