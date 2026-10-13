from app.utils.resultados_quiz import resultados_representativos_sql
from flask import (
    Blueprint,
    render_template,
)

from flask_login import login_required
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.utils.painel_colaboradores import calcular_indicadores_gerais, contexto_status_capacitacao

from app.extensions import db
from app.utils.metricas_capacitacao import calcular_metricas_capacitacao

from app.models.departamento import Departamento
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.campanha import Campanha

from app.models.participacao_treinamento import (
    ParticipacaoTreinamento
)

from app.models.resultado_quiz import (
    ResultadoQuiz
)


dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/dashboard"
)

dashboard_bp.context_processor(contexto_status_capacitacao)


@dashboard_bp.get("/")
@login_required
def index():

    # ==========================================================
    # VISÃO ADMINISTRATIVA DO AMBIENTE
    # ==========================================================

    total_colaboradores = (
        Colaborador.query.count()
    )

    total_colaboradores_ativos = (
        Colaborador.query
        .filter_by(
            ativo=True
        )
        .count()
    )

    total_departamentos = (
        Departamento.query.count()
    )

    total_treinamentos = (
        Treinamento.query.count()
    )

    total_campanhas = (
        Campanha.query.count()
    )

    # ==========================================================
    # STATUS DAS CAMPANHAS
    # ==========================================================

    campanhas_rascunho = (
        Campanha.query
        .filter_by(
            status="rascunho"
        )
        .count()
    )

    campanhas_aprovadas = (
        Campanha.query
        .filter_by(
            status="aprovada"
        )
        .count()
    )

    campanhas_em_preparacao = (
        Campanha.query
        .filter(
            Campanha.status.in_(
                [
                    "preparada",
                    "piloto_pronto",
                    "piloto_validado",
                ]
            )
        )
        .count()
    )

    campanhas_piloto_aprovado = (
        Campanha.query
        .filter_by(
            status="piloto_aprovado"
        )
        .count()
    )

    campanhas_reprovadas = (
        Campanha.query
        .filter_by(
            status="reprovada"
        )
        .count()
    )



    # ==========================================================
    # INDICADORES GERAIS
    # ==========================================================

    metricas_participacao = calcular_indicadores_gerais()
    total_participacoes = metricas_participacao['atribuidas']
    total_iniciados = metricas_participacao['iniciadas']
    total_concluidos = metricas_participacao['concluidas']
    total_pendentes = metricas_participacao['pendentes']
    total_nao_iniciados = metricas_participacao['nao_iniciadas']
    total_em_andamento = metricas_participacao['em_andamento']
    taxa_conclusao = round(metricas_participacao['taxa_conclusao'], 2) if metricas_participacao['taxa_conclusao'] is not None else None

    # ==========================================================
    # QUIZ GERAL
    # ==========================================================

    representantes = resultados_representativos_sql()
    total_quizzes = (
        db.session.query(representantes).count()
    )

    media_quiz = (
        db.session.query(
            func.avg(
                representantes.c.percentual
            )
        ).scalar()
    )

    if media_quiz is None:

        media_quiz = 0

    else:

        media_quiz = round(
            float(media_quiz),
            2
        )

    # ==========================================================
    # RESULTADOS RECENTES
    # ==========================================================

    resultados_recentes = (
        ResultadoQuiz.query
        .join(representantes, representantes.c.id == ResultadoQuiz.id)
        .options(
            selectinload(ResultadoQuiz.participacao).selectinload(ParticipacaoTreinamento.colaborador).selectinload(Colaborador.departamento),
            selectinload(ResultadoQuiz.participacao).selectinload(ParticipacaoTreinamento.treinamento),
        )
        .order_by(
            ResultadoQuiz.concluido_em.desc(), ResultadoQuiz.id.desc()
        )
        .limit(10)
        .all()
    )

    # ==========================================================
    # INDICADORES POR DEPARTAMENTO
    # ==========================================================

    departamentos = (
        Departamento.query
        .order_by(
            Departamento.nome
        )
        .all()
    )

    indicadores_departamentos = []

    for departamento in departamentos:

        # ------------------------------------------------------
        # Colaboradores pertencentes ao departamento
        # ------------------------------------------------------

        colaboradores_ids = [
            colaborador.id
            for colaborador in (
                Colaborador.query
                .filter_by(
                    departamento_id=departamento.id
                )
                .all()
            )
        ]

        total_colaboradores_departamento = len(
            colaboradores_ids
        )

        # ------------------------------------------------------
        # Caso não existam colaboradores
        # ------------------------------------------------------

        if not colaboradores_ids:

            indicadores_departamentos.append(
                {
                    "departamento": departamento.nome,
                    "colaboradores": 0,
                    "participacoes": 0,
                    "iniciados": 0,
                    "concluidos": 0,
                    "pendentes": 0,
                    "taxa_conclusao": 0,
                    "media_quiz": 0,
                    "quizzes": 0,
                }
            )

            continue

        # ------------------------------------------------------
        # Participações
        # ------------------------------------------------------

        total_participacoes_departamento = (
            ParticipacaoTreinamento.query
            .filter(
                ParticipacaoTreinamento.colaborador_id.in_(
                    colaboradores_ids
                )
            )
            .count()
        )

        # ------------------------------------------------------
        # Iniciados
        # ------------------------------------------------------

        total_iniciados_departamento = (
            ParticipacaoTreinamento.query
            .filter(
                ParticipacaoTreinamento.colaborador_id.in_(
                    colaboradores_ids
                ),
                ParticipacaoTreinamento.iniciado_em.isnot(None)
            )
            .count()
        )

        # ------------------------------------------------------
        # Concluídos
        # ------------------------------------------------------

        total_concluidos_departamento = (
            ParticipacaoTreinamento.query
            .filter(
                ParticipacaoTreinamento.colaborador_id.in_(
                    colaboradores_ids
                ),
                ParticipacaoTreinamento.concluido_em.isnot(None)
            )
            .count()
        )

        # ------------------------------------------------------
        # Pendentes
        # ------------------------------------------------------

        total_pendentes_departamento = (
            total_participacoes_departamento
            - total_concluidos_departamento
        )

        # ------------------------------------------------------
        # Taxa de conclusão
        # ------------------------------------------------------

        if total_participacoes_departamento > 0:

            taxa_conclusao_departamento = round(
                (
                    total_concluidos_departamento
                    / total_participacoes_departamento
                ) * 100,
                2
            )

        else:

            taxa_conclusao_departamento = 0

        # ------------------------------------------------------
        # Total de quizzes do departamento
        # ------------------------------------------------------

        total_quizzes_departamento = (
            db.session.query(representantes)
            .join(
                ParticipacaoTreinamento,
                representantes.c.participacao_id
                == ParticipacaoTreinamento.id
            )
            .filter(
                ParticipacaoTreinamento.colaborador_id.in_(
                    colaboradores_ids
                )
            )
            .count()
        )

        # ------------------------------------------------------
        # Média do quiz
        # ------------------------------------------------------

        media_departamento = (
            db.session.query(
                func.avg(
                    representantes.c.percentual
                )
            )
            .join(
                ParticipacaoTreinamento,
                representantes.c.participacao_id
                == ParticipacaoTreinamento.id
            )
            .filter(
                ParticipacaoTreinamento.colaborador_id.in_(
                    colaboradores_ids
                )
            )
            .scalar()
        )

        if media_departamento is None:

            media_departamento = 0

        else:

            media_departamento = round(
                float(media_departamento),
                2
            )

        # ------------------------------------------------------
        # Resultado final do departamento
        # ------------------------------------------------------

        indicadores_departamentos.append(
            {
                "departamento": departamento.nome,
                "colaboradores": total_colaboradores_departamento,
                "participacoes": (
                    total_participacoes_departamento
                ),
                "iniciados": (
                    total_iniciados_departamento
                ),
                "concluidos": (
                    total_concluidos_departamento
                ),
                "pendentes": (
                    total_pendentes_departamento
                ),
                "taxa_conclusao": (
                    taxa_conclusao_departamento
                ),
                "media_quiz": media_departamento,
                "quizzes": (
                    total_quizzes_departamento
                ),
            }
        )

    # ==========================================================
    # TEMPLATE
    # ==========================================================

    metricas_capacitacao = calcular_metricas_capacitacao()

    return render_template(
        "dashboard/index.html",
        metricas_capacitacao=metricas_capacitacao,
        metricas_participacao=metricas_participacao,

        total_colaboradores=total_colaboradores,
        total_colaboradores_ativos=total_colaboradores_ativos,
        total_departamentos=total_departamentos,
        total_treinamentos=total_treinamentos,
        total_campanhas=total_campanhas,

        campanhas_rascunho=campanhas_rascunho,
        campanhas_aprovadas=campanhas_aprovadas,
        campanhas_em_preparacao=campanhas_em_preparacao,
        campanhas_piloto_aprovado=campanhas_piloto_aprovado,
        campanhas_reprovadas=campanhas_reprovadas,

        total_participacoes=total_participacoes,
        total_iniciados=total_iniciados,
        total_concluidos=total_concluidos,
        total_pendentes=total_pendentes,
        total_nao_iniciados=total_nao_iniciados,
        total_em_andamento=total_em_andamento,

        taxa_conclusao=taxa_conclusao,

        total_quizzes=total_quizzes,
        media_quiz=media_quiz,

        resultados_recentes=resultados_recentes,

        indicadores_departamentos=(
            indicadores_departamentos
        ),
    )
