"""Consultas de leitura do painel; não carregam relacionamentos nem alteram dados.

As funções consulta_* retornam Select componíveis. Indicadores recebem
opcionalmente o mesmo Select de colaboradores usado pelo consumidor.
Atividade significa tempo positivo, conforme a etapa 18.2. A última atividade
é a maior data registrada de heartbeat, início, conclusão ou quiz.
"""
from app.utils.resultados_quiz import resultado_representativo, resultados_carregados, resultados_representativos_sql

from sqlalchemy import case, or_, union_all
from types import SimpleNamespace
from app.models.treinamento import Treinamento

from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.departamento import Departamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.utils.tempos_treinamento import agregacao_tempo_por_participacao


ROTULOS_PARTICIPACAO = {
    "nao_iniciado": "Não iniciado",
    "em_andamento": "Em andamento",
    "aprovado": "Aprovado",
    "reprovado": "Reprovado",
    "historico": "Concluído — histórico/não classificado",
}


def _nulo(valor):
    return valor.is_(None) if hasattr(valor, "is_") else valor is None


def _igual_booleano(valor, esperado):
    return valor.is_(esperado) if hasattr(valor, "is_") else valor is esperado


def _negar(valor):
    return not valor if isinstance(valor, bool) else ~valor


def condicoes_participacao(p, r):
    """A mesma semântica para objetos e expressões SQL; nenhuma nota recalculada.

    Aprovação persistida prevalece; conclusão é requisito para reprovação/histórico.
    Resultado NULL nunca é inferido.
    Sem resultado, a primeira aprovação persistida identifica a conclusão aprovada.
    """
    concluido = _negar(_nulo(p.concluido_em))
    iniciado = _negar(_nulo(p.iniciado_em))
    tem_resultado = _negar(_nulo(r.id))
    aprovado = ((tem_resultado & _igual_booleano(r.aprovado, True)) |
                (_negar(tem_resultado) & _negar(_nulo(p.aprovado_em))))
    reprovado = concluido & tem_resultado & _igual_booleano(r.aprovado, False)
    return {
        "nao_iniciado": _negar(concluido) & _negar(iniciado) & _negar(aprovado),
        "em_andamento": _negar(concluido) & iniciado & _negar(aprovado),
        "aprovado": aprovado,
        "reprovado": reprovado,
        "historico": concluido & _negar(aprovado | reprovado),
    }


def status_participacao(participacao, resultado):
    if isinstance(resultado, (list, tuple)):
        resultado = resultado_representativo(resultado)
    r = resultado if resultado is not None else SimpleNamespace(id=None, aprovado=None)
    return next(nome for nome, condicao in condicoes_participacao(participacao, r).items() if condicao)


_RESULTADO_NAO_INFORMADO = object()


def situacao_participacao(participacao, resultado=_RESULTADO_NAO_INFORMADO):
    # Consumidores que já possuem o resultado não devem consultar o backref.
    if resultado is _RESULTADO_NAO_INFORMADO:
        resultado = resultado_representativo(resultados_carregados(participacao))
    return ROTULOS_PARTICIPACAO[status_participacao(participacao, resultado)]


def contexto_status_capacitacao():
    return {"situacao_participacao": situacao_participacao,
            "rotulos_participacao": ROTULOS_PARTICIPACAO,
            "resultado_representativo": resultado_representativo,
            "resultados_carregados": resultados_carregados}


def consulta_painel_colaboradores(treinamento_id=None):
    """Uma linha por cadastro, incluindo inativos e pessoas sem atribuições.

    Atribuições contam participações, inclusive repetições de um treinamento.
    Iniciadas incluem concluídas que tenham iniciado_em; pendentes incluem
    não iniciadas e em andamento. Legados não são corrigidos por esta leitura.
    """
    p = ParticipacaoTreinamento
    if treinamento_id is not None:
        # O recorte antecede contagens, tempo e todos os eventos de atividade.
        p = db.select(*ParticipacaoTreinamento.__table__.c).where(
            ParticipacaoTreinamento.treinamento_id == treinamento_id,
        ).subquery().c
    tempos = agregacao_tempo_por_participacao().subquery()
    contagem = lambda condicao: db.func.sum(case((condicao, 1), else_=0))
    r = resultados_representativos_sql()
    estados = condicoes_participacao(p, r.c)
    participacoes = db.select(
        p.colaborador_id,
        db.func.count(p.id).label('atribuidas'),
        contagem(p.iniciado_em.isnot(None)).label('iniciadas'),
        contagem(p.concluido_em.isnot(None)).label('concluidas'),
        contagem(estados['aprovado']).label('aprovadas'),
        contagem(estados['reprovado']).label('reprovadas'),
        contagem(estados['historico']).label('historicas'),
        contagem(estados['nao_iniciado']).label('nao_iniciadas'),
        contagem(estados['em_andamento']).label('em_andamento'),
        db.func.coalesce(db.func.sum(tempos.c.segundos), 0).label('total_segundos'),
    ).select_from(p if p is ParticipacaoTreinamento else p.id.table).outerjoin(
        r, r.c.participacao_id == p.id,
    ).outerjoin(tempos, tempos.c.participacao_id == p.id).group_by(p.colaborador_id).subquery()

    # UNION ALL + MAX é compatível com SQLite e PostgreSQL e ignora NULLs.
    # Esses eventos ficam separados das contagens, evitando multiplicação.
    eventos = union_all(
        db.select(p.colaborador_id, p.iniciado_em.label('data')),
        db.select(p.colaborador_id, p.concluido_em.label('data')),
        db.select(p.colaborador_id, tempos.c.ultima_atividade_em.label('data'))
        .join(tempos, tempos.c.participacao_id == p.id),
        db.select(p.colaborador_id, ResultadoQuiz.concluido_em.label('data'))
        .join(ResultadoQuiz, ResultadoQuiz.participacao_id == p.id),
    ).subquery()
    atividade = db.select(
        eventos.c.colaborador_id,
        db.func.max(eventos.c.data).label('ultima_atividade_em'),
    ).group_by(eventos.c.colaborador_id).subquery()
    atribuidas = db.func.coalesce(participacoes.c.atribuidas, 0)
    concluidas = db.func.coalesce(participacoes.c.concluidas, 0)
    aprovadas = db.func.coalesce(participacoes.c.aprovadas, 0)
    reprovadas = db.func.coalesce(participacoes.c.reprovadas, 0)
    consulta = db.select(
        Colaborador.id.label('colaborador_id'), Colaborador.nome,
        Colaborador.email, Colaborador.ativo,
        Colaborador.departamento_id, Departamento.nome.label('departamento'),
        atribuidas.label('atribuidas'),
        db.func.coalesce(participacoes.c.iniciadas, 0).label('iniciadas'),
        concluidas.label('concluidas'),
        aprovadas.label('aprovadas'), reprovadas.label('reprovadas'),
        db.func.coalesce(participacoes.c.historicas, 0).label('historicas'),
        db.func.coalesce(participacoes.c.nao_iniciadas, 0).label('nao_iniciadas'),
        (100.0 * aprovadas / db.func.nullif(aprovadas + reprovadas, 0)).label('percentual_aprovacao'),
        db.func.coalesce(participacoes.c.em_andamento, 0).label('em_andamento'),
        (atribuidas - concluidas).label('pendentes'),
        (100.0 * concluidas / db.func.nullif(atribuidas, 0)).label('percentual_conclusao'),
        db.func.coalesce(participacoes.c.total_segundos, 0).label('total_segundos'),
        atividade.c.ultima_atividade_em,
    ).select_from(Colaborador).outerjoin(
        Departamento, Departamento.id == Colaborador.departamento_id,
    ).outerjoin(participacoes, participacoes.c.colaborador_id == Colaborador.id).outerjoin(
        atividade, atividade.c.colaborador_id == Colaborador.id,
    )
    if treinamento_id is not None:
        consulta = consulta.where(atribuidas > 0)
    return consulta


STATUS_CAPACITACAO = {
    "sem_atribuicao": "Sem atribuição",
    "nao_iniciado": "Não iniciado",
    "em_andamento": "Em andamento",
    "parcialmente_concluido": "Parcialmente concluído",
    "concluido": "Concluído",
    "aprovado": "Com aprovação",
    "reprovado": "Com reprovação",
    "historico": "Com histórico/não classificado",
}
ATIVIDADES = {"com_tempo": "Com tempo ativo", "sem_tempo": "Sem tempo ativo"}
SITUACOES = {"ativos": "Ativos", "inativos": "Inativos"}


def aplicar_filtros_painel(consulta, departamento_id=None, status=None,
                           atividade=None, situacao=None, busca=""):
    """Restringe a consulta agregada sem mudar as regras das métricas.

    Os status anteriores são exclusivos; aprovação/reprovação/histórico indicam
    presença de participações e podem se sobrepor. Busca usa parâmetros e escapa curingas.
    Treinamento deve ser aplicado na construção da consulta, antes dos totais.
    """
    c = consulta.selected_columns
    estados = {
        "sem_atribuicao": c.atribuidas == 0,
        "concluido": (c.atribuidas > 0) & (c.concluidas == c.atribuidas),
        "em_andamento": (c.pendentes > 0) & (c.em_andamento > 0),
        "parcialmente_concluido": (c.concluidas > 0) & (c.pendentes > 0) & (c.em_andamento == 0),
        "nao_iniciado": (c.atribuidas > 0) & (c.iniciadas == 0) & (c.concluidas == 0),
    }
    estados.update(aprovado=c.aprovadas > 0, reprovado=c.reprovadas > 0, historico=c.historicas > 0)
    if departamento_id is not None:
        consulta = consulta.where(c.departamento_id == departamento_id)
    if status in estados:
        consulta = consulta.where(estados[status])
    if atividade in ATIVIDADES:
        consulta = consulta.where(c.total_segundos > 0 if atividade == "com_tempo" else c.total_segundos == 0)
    if situacao in SITUACOES:
        consulta = consulta.where(c.ativo.is_(situacao == "ativos"))
    busca = " ".join(busca.split())
    if busca:
        literal = busca.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        consulta = consulta.where(or_(c.nome.ilike("%" + literal + "%", escape="!"),
                                     c.email.ilike("%" + literal + "%", escape="!")))
    return consulta


def consulta_indicadores_gerais(consulta=None):
    """Resumo SQL do conjunto recebido, antes de LIMIT/OFFSET do consumidor."""
    if consulta is None:
        consulta = consulta_painel_colaboradores()
    conjunto = consulta.order_by(None).subquery()
    contar = lambda condicao: db.func.count(case((condicao, 1)))
    return db.select(
        db.func.count().label('total_colaboradores'),
        *(db.func.coalesce(db.func.sum(conjunto.c[nome]), 0).label(nome)
          for nome in ('atribuidas','iniciadas','concluidas','aprovadas','reprovadas',
                       'historicas','nao_iniciadas','em_andamento','pendentes')),
        contar(conjunto.c.atribuidas > 0).label('com_treinamento_atribuido'),
        contar(conjunto.c.em_andamento > 0).label('com_treinamento_em_andamento'),
        contar(conjunto.c.concluidas > 0).label('com_treinamentos_concluidos'),
        contar(conjunto.c.total_segundos == 0).label('sem_atividade'),
        contar(conjunto.c.total_segundos > 0).label('com_atividade'),
        db.func.coalesce(db.func.sum(conjunto.c.total_segundos), 0).label('total_segundos'),
        db.func.coalesce(db.func.avg(case(
            (conjunto.c.total_segundos > 0, conjunto.c.total_segundos),
        )), 0).label('media_segundos_por_colaborador_com_atividade'),
    ).select_from(conjunto)


def calcular_indicadores_gerais(consulta=None):
    """Executa uma consulta; retorna números prontos para apresentação futura."""
    resultado = dict(db.session.execute(consulta_indicadores_gerais(consulta)).mappings().one())
    media = 'media_segundos_por_colaborador_com_atividade'
    resumo = {chave: float(valor) if chave == media else int(valor)
              for chave, valor in resultado.items()}
    resumo['taxa_conclusao'] = (100 * resumo['concluidas'] / resumo['atribuidas']
                               if resumo['atribuidas'] else None)
    classificados = resumo['aprovadas'] + resumo['reprovadas']
    resumo['taxa_aprovacao'] = (100 * resumo['aprovadas'] / classificados
                               if classificados else None)
    return resumo


def consulta_quiz_mais_recente(colaborador_ids=None, treinamento_id=None):
    """Resultado representativo mais recente por colaborador, por data DESC e ID DESC.

    IDs opcionais permitem ao futuro consumidor restringir à página desejada.
    Sem quiz, não há linha; o consumidor pode usar LEFT JOIN.
    """
    p = ParticipacaoTreinamento
    q = resultados_representativos_sql().c
    consulta = db.select(
        p.colaborador_id, q.id.label('resultado_quiz_id'), q.participacao_id,
        p.treinamento_id, q.percentual, q.total_acertos, q.total_questoes,
        q.concluido_em,
        db.func.row_number().over(
            partition_by=p.colaborador_id,
            order_by=(q.concluido_em.desc(), q.id.desc()),
        ).label('posicao'),
    ).join(p, p.id == q.participacao_id)
    if colaborador_ids is not None:
        consulta = consulta.where(p.colaborador_id.in_(colaborador_ids))
    if treinamento_id is not None:
        consulta = consulta.where(p.treinamento_id == treinamento_id)
    classificados = consulta.subquery()
    return db.select(*(coluna for coluna in classificados.c if coluna.key != 'posicao')).where(
        classificados.c.posicao == 1,
    )


def consulta_participacoes_painel(colaborador_ids, treinamento_id=None):
    """Todas as participações da página, em uma consulta e sem lazy loading.

    Mantém row_number para identificar o quiz mais recente por colaborador.
    A seleção representativa e a agregação de tempos evitam duplicações para N tentativas.
    """
    p = ParticipacaoTreinamento
    representantes = resultados_representativos_sql()
    r = representantes.c
    tempos = agregacao_tempo_por_participacao().subquery()
    estados = condicoes_participacao(p, r)
    consulta = db.select(
        p.id.label('participacao_id'), p.colaborador_id, p.treinamento_id,
        Treinamento.titulo.label('treinamento'), p.iniciado_em,
        p.concluido_em, p.aprovado_em,
        case(*((condicao, nome) for nome, condicao in estados.items())).label('status'),
        r.id.label('resultado_quiz_id'), r.percentual, r.total_questoes, r.total_acertos,
        r.aprovado, r.percentual_minimo_aplicado, r.minimo_acertos_aplicado,
        r.numero_tentativa, db.func.coalesce(r.total_tentativas, 0).label('total_tentativas'),
        case((r.aprovado.is_(True), r.id)).label('primeira_aprovacao_id'),
        r.concluido_em.label('quiz_concluido_em'),
        db.func.coalesce(tempos.c.segundos, 0).label('total_segundos'),
        db.func.row_number().over(partition_by=p.colaborador_id,
            order_by=(r.concluido_em.desc().nullslast(), r.id.desc().nullslast())).label('posicao_quiz'),
    ).select_from(p).join(Treinamento, Treinamento.id == p.treinamento_id).outerjoin(
        representantes, r.participacao_id == p.id,
    ).outerjoin(tempos, tempos.c.participacao_id == p.id).where(p.colaborador_id.in_(colaborador_ids))
    if treinamento_id is not None:
        consulta = consulta.where(p.treinamento_id == treinamento_id)
    return consulta.order_by(p.colaborador_id, p.criado_em.desc(), p.id.desc())
