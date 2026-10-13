from app.utils.resultados_quiz import resultado_representativo, resultados_carregados, resumo_tentativas
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)

from flask_login import login_required

from app.extensions import db
from sqlalchemy.orm import selectinload
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.utils.tempos_treinamento import totais_por_participacao
from app.utils.painel_colaboradores import (
    consulta_painel_colaboradores, calcular_indicadores_gerais,
    consulta_quiz_mais_recente, consulta_participacoes_painel, aplicar_filtros_painel,
    contexto_status_capacitacao, status_participacao,
    STATUS_CAPACITACAO, ATIVIDADES, SITUACOES,
)

from app.models.departamento import Departamento

from app.models.grupo_participante import (
    GrupoParticipante,
)

from app.models.participacao_treinamento import (
    ParticipacaoTreinamento,
)

colaboradores_bp = Blueprint(
    "colaboradores",
    __name__,
    url_prefix="/colaboradores"
)


colaboradores_bp.context_processor(contexto_status_capacitacao)


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

@colaboradores_bp.get("/painel")
@login_required
def painel():
    departamentos = list(db.session.execute(
        db.select(Departamento.id, Departamento.nome).order_by(Departamento.nome, Departamento.id)
    ).mappings())
    treinamentos = list(db.session.execute(
        db.select(Treinamento.id, Treinamento.titulo).order_by(Treinamento.titulo, Treinamento.id)
    ).mappings())

    def id_valido(chave, opcoes):
        try:
            valor = int(request.args.get(chave, ""))
        except (TypeError, ValueError):
            return None
        return valor if any(opcao["id"] == valor for opcao in opcoes) else None

    filtros = {
        "departamento": id_valido("departamento", departamentos),
        "treinamento": id_valido("treinamento", treinamentos),
        "status": request.args.get("status", ""),
        "atividade": request.args.get("atividade", ""),
        "situacao": request.args.get("situacao", ""),
        "busca": " ".join(request.args.get("busca", "").split()),
    }
    for chave, opcoes in [("status", STATUS_CAPACITACAO), ("atividade", ATIVIDADES), ("situacao", SITUACOES)]:
        if filtros[chave] not in opcoes:
            filtros[chave] = ""
    consulta = aplicar_filtros_painel(
        consulta_painel_colaboradores(filtros["treinamento"]),
        departamento_id=filtros["departamento"], status=filtros["status"],
        atividade=filtros["atividade"], situacao=filtros["situacao"], busca=filtros["busca"],
    )
    indicadores = calcular_indicadores_gerais(consulta)
    por_pagina = 25
    total_paginas = max(1, (indicadores["total_colaboradores"] + por_pagina - 1) // por_pagina)
    try:
        pagina = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
        pagina = 1
    pagina = min(max(1, pagina), total_paginas)
    parametros = {chave: valor for chave, valor in filtros.items() if valor is not None and valor != ""}
    escopo = []
    if filtros["departamento"] is not None:
        escopo.append("Departamento: " + next(d["nome"] for d in departamentos if d["id"] == filtros["departamento"]))
    if filtros["treinamento"] is not None:
        escopo.append("Treinamento: " + next(t["titulo"] for t in treinamentos if t["id"] == filtros["treinamento"]))
    for chave, opcoes, prefixo in [("status", STATUS_CAPACITACAO, "Capacitação: "),
                                   ("atividade", ATIVIDADES, "Atividade: "),
                                   ("situacao", SITUACOES, "Situação cadastral: ")]:
        if filtros[chave]:
            escopo.append(prefixo + opcoes[filtros[chave]])
    if filtros["busca"]:
        escopo.append("Busca: " + filtros["busca"])

    # Apenas a página é materializada; os cartões abrangem todo o conjunto.
    colaboradores = list(db.session.execute(
        consulta.order_by(Colaborador.nome, Colaborador.id)
        .limit(por_pagina).offset((pagina - 1) * por_pagina)
    ).mappings())
    quizzes = {}
    participacoes_painel = {}
    if colaboradores:
        for linha in db.session.execute(consulta_participacoes_painel(
            [linha['colaborador_id'] for linha in colaboradores], filtros['treinamento'],
        )).mappings():
            participacoes_painel.setdefault(linha['colaborador_id'], []).append(linha)
            if linha['resultado_quiz_id'] is not None and linha['posicao_quiz'] == 1:
                quizzes[linha['colaborador_id']] = dict(linha, concluido_em=linha['quiz_concluido_em'])
    return render_template(
        "colaboradores/painel.html", colaboradores=colaboradores,
        indicadores=indicadores, quizzes=quizzes, participacoes_painel=participacoes_painel,
        pagina=pagina, total_paginas=total_paginas,
        filtros=filtros, parametros=parametros, escopo=escopo,
        departamentos=departamentos, treinamentos=treinamentos,
        status_opcoes=STATUS_CAPACITACAO, atividade_opcoes=ATIVIDADES,
        situacao_opcoes=SITUACOES,
    )


# ==========================================================
# DETALHE DO COLABORADOR
# ==========================================================

@colaboradores_bp.get(
    "/<int:colaborador_id>"
)
@login_required
def detalhe(colaborador_id):

    colaborador = db.session.get(
        Colaborador,
        colaborador_id,
    )

    if not colaborador:

        abort(404)

    # ======================================================
    # GRUPOS
    # ======================================================

    grupos = (
        GrupoParticipante.query
        .filter_by(
            colaborador_id=colaborador.id
        )
        .order_by(
            GrupoParticipante.criado_em.desc()
        )
        .all()
    )

    # ======================================================
    # PARTICIPAÇÕES / TREINAMENTOS
    # ======================================================

    participacoes = (
        ParticipacaoTreinamento.query
        .options(
            selectinload(ParticipacaoTreinamento.treinamento).selectinload(Treinamento.categoria),
            selectinload(ParticipacaoTreinamento.ultimo_modulo),
            selectinload(ParticipacaoTreinamento.campanha),
            selectinload(ParticipacaoTreinamento.resultados_quiz),
        )
        .filter_by(
            colaborador_id=colaborador.id
        )
        .order_by(
            ParticipacaoTreinamento.criado_em.desc()
        )
        .all()
    )

    # ======================================================
    # INDICADORES
    # ======================================================

    total_participacoes = len(
        participacoes
    )

    estados_participacoes = {p.id: status_participacao(p, resultado_representativo(resultados_carregados(p))) for p in participacoes}
    total_nao_iniciados = sum(estado == 'nao_iniciado' for estado in estados_participacoes.values())

    total_em_andamento = sum(estado == 'em_andamento' for estado in estados_participacoes.values())

    total_concluidos = sum(
        1
        for participacao
        in participacoes
        if participacao.concluido_em is not None
    )




    resultados = [
        resultado_representativo(resultados_carregados(participacao))
        for participacao
        in participacoes
        if resultado_representativo(resultados_carregados(participacao))
    ]

    media_quiz = None

    if resultados:

        media_quiz = round(
            sum(
                resultado.percentual
                for resultado
                in resultados
            )
            / len(resultados),
            1,
        )

    from app.utils.metricas_feedback import datas_feedback_participacoes
    datas_feedback = datas_feedback_participacoes(p.id for p in participacoes)
    tempos_ativos = totais_por_participacao(p.id for p in participacoes)
    tempo_total_capacitacao = sum(tempos_ativos.values())
    total_iniciados = sum(p.iniciado_em is not None for p in participacoes)

    return render_template(
        "colaboradores/detalhe.html",
        tempo_total_capacitacao=tempo_total_capacitacao,
        total_iniciados=total_iniciados,
        colaborador=colaborador,
        grupos=grupos,
        participacoes=participacoes,
        tempos_ativos=tempos_ativos,
        datas_feedback=datas_feedback,
        total_participacoes=total_participacoes,
        estados_participacoes=estados_participacoes,
        tentativas_por_participacao={p.id: resumo_tentativas(resultados_carregados(p)) for p in participacoes},
        total_nao_iniciados=total_nao_iniciados,
        total_em_andamento=total_em_andamento,
        total_concluidos=total_concluidos,
        media_quiz=media_quiz,
    )


# ==========================================================
# EDITAR COLABORADOR
# ==========================================================

@colaboradores_bp.route(
    "/<int:colaborador_id>/editar",
    methods=["GET", "POST"],
)
@login_required
def editar(colaborador_id):

    colaborador = db.session.get(
        Colaborador,
        colaborador_id,
    )

    if not colaborador:

        abort(404)

    departamentos = (
        Departamento.query
        .filter_by(
            ativo=True
        )
        .order_by(
            Departamento.nome
        )
        .all()
    )

    # ======================================================
    # PROCESSAR EDIÇÃO
    # ======================================================

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            "",
        ).strip()

        email = request.form.get(
            "email",
            "",
        ).strip().lower()

        cargo = request.form.get(
            "cargo",
            "",
        ).strip()

        departamento_id = request.form.get(
            "departamento_id"
        )

        # ==================================================
        # CAMPOS OBRIGATÓRIOS
        # ==================================================

        if (
            not nome
            or not email
            or not departamento_id
        ):

            flash(
                "Preencha os campos obrigatórios.",
                "danger",
            )

            return redirect(
                url_for(
                    "colaboradores.editar",
                    colaborador_id=colaborador.id,
                )
            )

        # ==================================================
        # VALIDAR DEPARTAMENTO
        # ==================================================

        try:

            departamento_id = int(
                departamento_id
            )

        except (TypeError, ValueError):

            flash(
                "Departamento inválido.",
                "danger",
            )

            return redirect(
                url_for(
                    "colaboradores.editar",
                    colaborador_id=colaborador.id,
                )
            )

        departamento = db.session.get(
            Departamento,
            departamento_id,
        )

        if (
            not departamento
            or not departamento.ativo
        ):

            flash(
                "Selecione um departamento ativo.",
                "danger",
            )

            return redirect(
                url_for(
                    "colaboradores.editar",
                    colaborador_id=colaborador.id,
                )
            )

        # ==================================================
        # IMPEDIR E-MAIL DUPLICADO
        # ==================================================

        existente = (
            Colaborador.query
            .filter(
                Colaborador.email == email,
                Colaborador.id != colaborador.id,
            )
            .first()
        )

        if existente:

            flash(
                "Já existe outro colaborador com esse e-mail.",
                "warning",
            )

            return redirect(
                url_for(
                    "colaboradores.editar",
                    colaborador_id=colaborador.id,
                )
            )

        # ==================================================
        # ATUALIZAR
        # ==================================================

        colaborador.nome = nome
        colaborador.email = email
        colaborador.cargo = cargo or None
        colaborador.departamento_id = departamento_id

        db.session.commit()

        flash(
            "Dados do colaborador atualizados.",
            "success",
        )

        return redirect(
            url_for(
                "colaboradores.detalhe",
                colaborador_id=colaborador.id,
            )
        )

    return render_template(
        "colaboradores/editar.html",
        colaborador=colaborador,
        departamentos=departamentos,
    )


# ==========================================================
# ALTERAR STATUS
# ==========================================================

@colaboradores_bp.post(
    "/<int:colaborador_id>/status"
)
@login_required
def alterar_status(colaborador_id):

    colaborador = db.session.get(
        Colaborador,
        colaborador_id,
    )

    if not colaborador:

        abort(404)

    acao = request.form.get(
        "acao",
        "",
    ).strip().lower()

    # ======================================================
    # ATIVAR
    # ======================================================

    if acao == "ativar":

        colaborador.ativo = True

        mensagem = (
            "Colaborador ativado com sucesso."
        )

    # ======================================================
    # DESATIVAR
    # ======================================================

    elif acao == "desativar":

        colaborador.ativo = False

        mensagem = (
            "Colaborador desativado com sucesso."
        )

    else:

        flash(
            "Ação de status inválida.",
            "danger",
        )

        return redirect(
            url_for(
                "colaboradores.detalhe",
                colaborador_id=colaborador.id,
            )
        )

    db.session.commit()

    flash(
        mensagem,
        "success",
    )

    return redirect(
        url_for(
            "colaboradores.detalhe",
            colaborador_id=colaborador.id,
        )
    )

# ==========================================================
# CRIAR NOVO COLABORADOR
# ==========================================================

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
