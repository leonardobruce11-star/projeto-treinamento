"""Leitura agregada SQL; recortes pequenos são suprimidos antes da apresentação."""
from sqlalchemy import case
from app.extensions import db
from app.models.feedback_treinamento import FeedbackTreinamento as F
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.treinamento import Treinamento as T
from app.utils.feedback_treinamento import CAMPOS_NOTAS

MINIMO_AVALIACOES = 5
COMENTARIOS_POR_PAGINA = 10


def consulta_metricas_feedback(treinamento_id=None, categoria_id=None):
    # Cada subconsulta produz uma única linha por treinamento. Não há join
    # com módulos, quizzes, tempos ou colaboradores que multiplique respostas.
    concluidas = db.select(P.treinamento_id, db.func.count().label('concluidas')).where(
        P.concluido_em.isnot(None)).group_by(P.treinamento_id).subquery()
    agregados = [db.func.count(F.id).label('quantidade')]
    for campo in CAMPOS_NOTAS:
        coluna = getattr(F, campo)
        agregados.append(db.func.avg(coluna).label('media_' + campo))
        agregados.extend(db.func.sum(case((coluna == nota, 1), else_=0)).label(
            f'{campo}_{nota}') for nota in range(1, 6))
    respostas = db.select(P.treinamento_id, *agregados).join(
        F, F.participacao_id == P.id).where(P.concluido_em.isnot(None)).group_by(
        P.treinamento_id).subquery()
    quantidade = db.func.coalesce(respostas.c.quantidade, 0)
    total = db.func.coalesce(concluidas.c.concluidas, 0)
    permitido = quantidade >= MINIMO_AVALIACOES
    consulta = db.select(T.id.label('treinamento_id'), T.titulo, quantidade.label('quantidade'),
        total.label('concluidas'), permitido.label('permitido'),
        (100.0 * quantidade / db.func.nullif(total, 0)).label('taxa_resposta'),
        *(case((permitido, respostas.c[c.key])).label(c.key)
          for c in respostas.c if c.key not in ('treinamento_id', 'quantidade')),
    ).select_from(T).outerjoin(concluidas, concluidas.c.treinamento_id == T.id).outerjoin(
        respostas, respostas.c.treinamento_id == T.id)
    if treinamento_id is not None:
        consulta = consulta.where(T.id == treinamento_id)
    if categoria_id is not None:
        consulta = consulta.where(T.categoria_id == categoria_id)
    return consulta.order_by(T.titulo, T.id)


def indicadores_feedback(consulta):
    # Totais SQL sobre o recorte inteiro; sem médias globais que possibilitem
    # reconstruir a média suprimida de um treinamento por subtração.
    conjunto = consulta.order_by(None).subquery()
    quantidade = db.func.coalesce(db.func.sum(conjunto.c.quantidade), 0)
    concluidas = db.func.coalesce(db.func.sum(conjunto.c.concluidas), 0)
    return dict(db.session.execute(db.select(
        db.func.count().label('treinamentos'), quantidade.label('quantidade'),
        concluidas.label('concluidas'),
        (100.0 * quantidade / db.func.nullif(concluidas, 0)).label('taxa_resposta'),
    )).mappings().one())


def comentarios_feedback(consulta, pagina=1):
    elegiveis = consulta.order_by(None).subquery()
    # LTRIM/RTRIM com conjunto explícito de caracteres funcionam nos dois
    # bancos e correspondem aos espaços em branco aceitos pelo validador.
    espacos = ' \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
    texto = db.func.rtrim(db.func.ltrim(F.comentario, espacos), espacos)
    # O limiar conta avaliações, não comentários nem o tamanho da página.
    base = db.select(F.comentario, F.criado_em, T.titulo).join(
        P, P.id == F.participacao_id).join(T, T.id == P.treinamento_id).join(
        elegiveis, elegiveis.c.treinamento_id == T.id).where(
        elegiveis.c.permitido.is_(True), P.concluido_em.isnot(None),
        F.comentario.isnot(None), db.func.length(db.func.trim(texto)) > 0)
    total = db.session.execute(db.select(db.func.count()).select_from(base.subquery())).scalar_one()
    paginas = max(1, (total + COMENTARIOS_POR_PAGINA - 1) // COMENTARIOS_POR_PAGINA)
    pagina = min(max(1, pagina), paginas)
    itens = list(db.session.execute(base.order_by(F.criado_em.desc(), F.id.desc()).limit(
        COMENTARIOS_POR_PAGINA).offset((pagina - 1) * COMENTARIOS_POR_PAGINA)).mappings())
    return dict(itens=itens, total=total, pagina=pagina, paginas=paginas)


def datas_feedback_participacoes(participacao_ids):
    """Histórico individual: projeta exclusivamente chave técnica e data."""
    return dict(db.session.execute(db.select(F.participacao_id, F.criado_em).where(
        F.participacao_id.in_(list(participacao_ids)))).all())
