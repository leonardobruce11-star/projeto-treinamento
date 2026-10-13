"""Métricas de leitura do tempo ativo, sem depender das datas de progresso."""
from sqlalchemy import case, literal, union_all

from app.extensions import db
from app.models.categoria_treinamento import CategoriaTreinamento
from app.models.colaborador import Colaborador
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.utils.tempos_treinamento import agregacao_tempo_por_participacao
from app.models.treinamento import Treinamento


def calcular_metricas_capacitacao():
    # Uma linha por participação evita contar módulos como participações e
    # impede que os joins multipliquem o tempo registrado.
    tempos = agregacao_tempo_por_participacao().subquery()

    com_atividade = case((tempos.c.segundos > 0, tempos.c.segundos))
    resumo = db.session.execute(db.select(
        db.func.coalesce(db.func.sum(tempos.c.segundos), 0).label("total_segundos"),
        db.func.coalesce(db.func.avg(com_atividade), 0).label("media_segundos"),
        db.func.count(com_atividade).label("participacoes_com_tempo"),
        db.func.count(db.distinct(case(
            (tempos.c.segundos > 0, ParticipacaoTreinamento.colaborador_id),
        ))).label("colaboradores_com_atividade"),
    ).select_from(tempos).join(
        ParticipacaoTreinamento, ParticipacaoTreinamento.id == tempos.c.participacao_id,
    )).one()

    def listar_totais(modelo, nome, vinculo):
        consulta = db.select(
            modelo.id.label("id"), nome.label("nome"),
            db.func.coalesce(db.func.sum(tempos.c.segundos), 0).label("segundos"),
        ).select_from(modelo).outerjoin(
            ParticipacaoTreinamento, vinculo,
        ).outerjoin(
            tempos, tempos.c.participacao_id == ParticipacaoTreinamento.id,
        ).group_by(modelo.id, nome)
        consulta = consulta.order_by(db.desc("segundos"), nome, modelo.id)
        return [dict(linha) for linha in db.session.execute(consulta).mappings()]

    por_treinamento = listar_totais(
        Treinamento, Treinamento.titulo,
        ParticipacaoTreinamento.treinamento_id == Treinamento.id,
    )
    por_colaborador = listar_totais(
        Colaborador, Colaborador.nome,
        ParticipacaoTreinamento.colaborador_id == Colaborador.id,
    )

    categorias = db.select(
        CategoriaTreinamento.id.label("id"),
        CategoriaTreinamento.nome.label("nome"),
        db.func.coalesce(db.func.sum(tempos.c.segundos), 0).label("segundos"),
    ).select_from(CategoriaTreinamento).outerjoin(
        Treinamento, Treinamento.categoria_id == CategoriaTreinamento.id,
    ).outerjoin(
        ParticipacaoTreinamento, ParticipacaoTreinamento.treinamento_id == Treinamento.id,
    ).outerjoin(
        tempos, tempos.c.participacao_id == ParticipacaoTreinamento.id,
    ).group_by(CategoriaTreinamento.id, CategoriaTreinamento.nome)

    sem_categoria = db.select(
        literal(None).label("id"), literal("Sem categoria").label("nome"),
        db.func.coalesce(db.func.sum(tempos.c.segundos), 0).label("segundos"),
    ).select_from(Treinamento).outerjoin(
        ParticipacaoTreinamento, ParticipacaoTreinamento.treinamento_id == Treinamento.id,
    ).outerjoin(
        tempos, tempos.c.participacao_id == ParticipacaoTreinamento.id,
    ).where(Treinamento.categoria_id.is_(None)).having(db.func.count(Treinamento.id) > 0)

    grupos = union_all(categorias, sem_categoria).subquery()
    por_categoria = [dict(linha) for linha in db.session.execute(
        db.select(grupos).order_by(grupos.c.segundos.desc(), grupos.c.nome, grupos.c.id)
    ).mappings()]

    return {
        "total_segundos": int(resumo.total_segundos),
        "media_segundos": float(resumo.media_segundos),
        "participacoes_com_tempo": int(resumo.participacoes_com_tempo),
        "colaboradores_com_atividade": int(resumo.colaboradores_com_atividade),
        "por_treinamento": por_treinamento,
        "por_categoria": por_categoria,
        "por_colaborador": por_colaborador,
    }
