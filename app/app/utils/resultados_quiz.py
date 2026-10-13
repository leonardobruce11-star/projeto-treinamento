"""Leituras de tentativas. Helpers puros; nunca buscam relações implicitamente.

A aprovação é persistida, não inferida da nota. A numeração identifica a ordem
histórica (um futuro fluxo de refação deverá alocá-la sob FOR UPDATE da participação).
"""
from sqlalchemy import case, inspect
from sqlalchemy.exc import InvalidRequestError
from app.extensions import db
from app.models.resultado_quiz import ResultadoQuiz


def resultados_carregados(participacao):
    """Exige selectinload explícito; impede consultas escondidas/N+1."""
    if 'resultados_quiz' in inspect(participacao).unloaded:
        raise InvalidRequestError('Carregue resultados_quiz explicitamente com selectinload.')
    return participacao.resultados_quiz


def _ordem(resultado):
    return (resultado.numero_tentativa or 1, resultado.id or 0)


def ultima_tentativa(resultados):
    return max(resultados, key=_ordem, default=None)


def primeira_aprovacao(resultados):
    return min((r for r in resultados if r.aprovado is True), key=_ordem, default=None)


def tem_tentativa_aprovada(resultados):
    return primeira_aprovacao(resultados) is not None


def total_tentativas(resultados):
    return len(resultados)


def resultado_representativo(resultados):
    """Primeira aprovação; senão última classificada; senão último histórico."""
    resultados = tuple(resultados)
    return (primeira_aprovacao(resultados)
            or ultima_tentativa([r for r in resultados if r.aprovado is not None])
            or ultima_tentativa(resultados))


def resumo_tentativas(resultados):
    """Dados estruturados para detalhe/consumidores, sem efeitos no histórico."""
    resultados = sorted(resultados, key=_ordem)
    return dict(historico=resultados, resultado=resultado_representativo(resultados),
                ultima=ultima_tentativa(resultados), primeira_aprovacao=primeira_aprovacao(resultados),
                total_tentativas=total_tentativas(resultados))


def resultados_representativos_sql():
    """Uma linha por participação; política equivalente aos helpers Python.

    Reduza a cardinalidade ANTES dos JOINs/AVG/SUM de participação. Atividade
    pode usar todas as tentativas em UNION/MAX; métricas de tentativas são distintas.
    """
    r = ResultadoQuiz
    aprovado = r.aprovado.is_(True)
    prioridade = case((aprovado, 0), (r.aprovado.is_(False), 1), else_=2)
    rank = db.select(
        *r.__table__.c,
        db.func.count().over(partition_by=r.participacao_id).label('total_tentativas'),
        db.func.max(r.numero_tentativa).over(partition_by=r.participacao_id).label('ultima_numero_tentativa'),
        db.func.row_number().over(partition_by=r.participacao_id, order_by=(
            prioridade,
            case((aprovado, r.numero_tentativa)).asc(),
            case((aprovado, r.id)).asc(),
            r.numero_tentativa.desc(), r.id.desc(),
        )).label('posicao_representativa'),
    ).subquery()
    return db.select(*(c for c in rank.c if c.key != 'posicao_representativa')).where(
        rank.c.posicao_representativa == 1,
    ).subquery('resultados_representativos')
