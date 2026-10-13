"""Consultas somente de leitura dos tempos oficiais registrados no servidor."""
from app.extensions import db
from app.models.tempo_treinamento import TempoTreinamento


def agregacao_tempo_por_participacao(participacao_ids=None):
    """Select SQL oficial: uma linha por participação, sem executar consulta.

    IDs opcionais restringem a leitura antes do GROUP BY.
    """
    consulta = db.select(
        TempoTreinamento.participacao_id.label("participacao_id"),
        db.func.sum(TempoTreinamento.segundos_ativos).label("segundos"),
        db.func.max(TempoTreinamento.ultima_atividade_em).label("ultima_atividade_em"),
    )
    if participacao_ids is not None:
        consulta = consulta.where(TempoTreinamento.participacao_id.in_(participacao_ids))
    return consulta.group_by(TempoTreinamento.participacao_id)


def totais_por_participacao(participacao_ids):
    ids = list(set(participacao_ids))
    if not ids:
        return {}
    totais = dict.fromkeys(ids, 0)
    linhas = db.session.execute(
        agregacao_tempo_por_participacao(ids)
    ).all()
    totais.update({pid: int(total or 0) for pid, total, _ in linhas})
    return totais


def tempos_da_participacao(participacao_id):
    # Duas consultas fixas: SUM oficial e tempos dos módulos da participação.
    total = totais_por_participacao([participacao_id])[participacao_id]
    linhas = db.session.execute(
        db.select(TempoTreinamento.modulo_id, TempoTreinamento.segundos_ativos)
        .where(TempoTreinamento.participacao_id == participacao_id)
    ).all()
    return total, {mid: int(segundos or 0) for mid, segundos in linhas}
