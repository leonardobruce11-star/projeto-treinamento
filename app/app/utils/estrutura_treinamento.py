"""Proteção central da sequência e avaliação usadas pelas participações."""
from sqlalchemy import event, inspect, or_, select
from sqlalchemy.orm import Session
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.treinamento import Treinamento
from app.models.questao import Questao
from app.models.alternativa import Alternativa

MENSAGEM_ESTRUTURA_BLOQUEADA = (
    "Este treinamento já possui participação iniciada ou histórico de progresso. "
    "Não é permitido adicionar, remover ou reordenar módulos. "
    "Título e conteúdo podem ser editados mantendo a ordem atual."
)


MENSAGEM_AVALIACAO_BLOQUEADA = (
    "Este treinamento já foi iniciado. Sua avaliação não pode mais ser alterada."
)


class AvaliacaoTreinamentoBloqueada(ValueError):
    def __init__(self, mensagem=MENSAGEM_AVALIACAO_BLOQUEADA):
        super().__init__(mensagem)


class EstruturaTreinamentoBloqueada(ValueError):
    def __init__(self):
        super().__init__(MENSAGEM_ESTRUTURA_BLOQUEADA)


CAMPOS_INICIO_ESTRUTURAL = ('iniciado_em', 'ultimo_modulo_id', 'concluido_em')


def estrutura_bloqueada(treinamento_id, session):
    criterio = select(ParticipacaoTreinamento.id).where(
        ParticipacaoTreinamento.treinamento_id == treinamento_id,
        or_(*(getattr(ParticipacaoTreinamento, campo).is_not(None)
              for campo in CAMPOS_INICIO_ESTRUTURAL)),
    ).exists()
    with session.no_autoflush:
        # A regra também vale se início e edição forem preparados no mesmo flush.
        for participacao in session.new | session.dirty:
            if not isinstance(participacao, ParticipacaoTreinamento):
                continue
            tid = participacao.treinamento_id
            if tid is None and participacao.treinamento is not None:
                tid = participacao.treinamento.id
            if tid == treinamento_id and any(
                    getattr(participacao, campo) is not None
                    for campo in CAMPOS_INICIO_ESTRUTURAL):
                return True
        return bool(session.execute(select(criterio)).scalar_one())


def bloquear_sequencia(treinamento_id, session, compartilhado=False):
    # KEY SHARE permite inícios/avanços paralelos, mas conflita com a trava
    # exclusiva usada pelas alterações estruturais no PostgreSQL.
    consulta = select(Treinamento.id).where(Treinamento.id == treinamento_id)
    consulta = consulta.with_for_update(read=compartilhado, key_share=compartilhado)
    with session.no_autoflush:
        session.execute(consulta).scalar_one_or_none()


def _treinamentos_do_modulo(modulo):
    estado = inspect(modulo)
    ids = {modulo.treinamento_id}
    ids.update(estado.attrs.treinamento_id.history.deleted)
    for treinamento in (*estado.attrs.treinamento.history.added,
                         *estado.attrs.treinamento.history.deleted):
        if treinamento is not None:
            ids.add(treinamento.id)
    return ids - {None}


@event.listens_for(Session, "before_flush")
def proteger_estrutura(session, flush_context, instances):
    """Trava treinamentos em ordem antes de validar e escrever módulos/quiz."""
    ids = set()
    quiz_ids = _avaliacoes_alteradas(session)
    for modulo in session.new | session.dirty | session.deleted:
        if not isinstance(modulo, ModuloTreinamento):
            continue
        estado = inspect(modulo)
        estrutural = (modulo in session.new or modulo in session.deleted
                      or any(estado.attrs[nome].history.has_changes()
                             for nome in ('ordem', 'id', 'treinamento_id', 'treinamento')))
        if estrutural:
            ids.update(_treinamentos_do_modulo(modulo))
    # delete-orphan só entra em session.deleted durante o flush.
    for treinamento in session.dirty:
        if isinstance(treinamento, Treinamento) and inspect(treinamento).attrs.modulos.history.deleted:
            if treinamento.id is not None:
                ids.add(treinamento.id)
    for treinamento_id in sorted(ids | quiz_ids):
        bloquear_sequencia(treinamento_id, session)
        if estrutura_bloqueada(treinamento_id, session):
            if treinamento_id in quiz_ids:
                raise AvaliacaoTreinamentoBloqueada()
            raise EstruturaTreinamentoBloqueada()

    # Uma reassociação concorrente pode ter mudado a origem enquanto aguardamos
    # o lock. Não adquirir novos locks fora da ordem: rejeitar e exigir releitura.
    if not _avaliacoes_alteradas(session).issubset(ids | quiz_ids):
        raise AvaliacaoTreinamentoBloqueada(
            "A avaliação foi alterada simultaneamente. Recarregue e tente novamente."
        )


def _ids_relacionados(objeto, fk, relacao, session):
    estado = inspect(objeto)
    ids = {getattr(objeto, fk)}
    ids.update(estado.attrs[fk].history.deleted)
    # Atribuição a FK expirada/deferida pode não registrar history.deleted.
    # A identidade persistida preserva a origem mesmo se PK/FK forem alteradas.
    if estado.identity is not None:
        modelo = type(objeto)
        anterior = session.execute(select(getattr(modelo, fk))
                   .where(modelo.id == estado.identity[0])).scalar_one_or_none()
        ids.add(anterior)
    relacionados = [getattr(objeto, relacao)]
    relacionados.extend(estado.attrs[relacao].history.added)
    relacionados.extend(estado.attrs[relacao].history.deleted)
    ids.update(item.id for item in relacionados if item is not None)
    return ids - {None}


def _colunas_alteradas(estado):
    return any(estado.attrs[col.key].history.has_changes()
               for col in estado.mapper.column_attrs)


def _avaliacoes_alteradas(session):
    """Todos os campos do quiz são estruturais; inclui movimentos e órfãos.

    Apenas lê questões/participações: nunca trava filhos antes do treinamento.
    A trava exclusiva permanece até commit/rollback, incluindo múltiplos flushes.
    """
    ids, questao_ids = set(), set()
    with session.no_autoflush:
        for objeto in session.new | session.dirty | session.deleted:
            estado = inspect(objeto)
            if isinstance(objeto, Questao):
                alterada = (objeto in session.new or objeto in session.deleted
                            or _colunas_alteradas(estado)
                            or estado.attrs.treinamento.history.has_changes()
                            or estado.attrs.alternativas.history.has_changes())
                if alterada:
                    ids.update(_ids_relacionados(objeto, 'treinamento_id', 'treinamento', session))
            elif isinstance(objeto, Alternativa):
                alterada = (objeto in session.new or objeto in session.deleted
                            or _colunas_alteradas(estado)
                            or estado.attrs.questao.history.has_changes())
                if alterada:
                    questao_ids.update(_ids_relacionados(objeto, 'questao_id', 'questao', session))
                    # Questão nova pode ainda não ter ID/FK sincronizado.
                    if objeto.questao is not None:
                        ids.update(_ids_relacionados(objeto.questao, 'treinamento_id', 'treinamento', session))
            elif isinstance(objeto, Treinamento) and estado.attrs.questoes.history.deleted:
                if objeto.id is not None:
                    ids.add(objeto.id)
        if questao_ids:
            ids.update(session.execute(select(Questao.treinamento_id)
                       .where(Questao.id.in_(questao_ids))).scalars())
    return ids


@event.listens_for(Session, "do_orm_execute")
def impedir_escrita_quiz_em_lote(estado):
    """Bulk DML não dispara before_flush. Exigir o caminho ORM protegido."""
    if not (estado.is_update or estado.is_delete or estado.is_insert):
        return
    tabela = getattr(estado.statement, 'table', None)
    if tabela is not None and tabela.name in ('questoes', 'alternativas'):
        raise ValueError("Alterações em lote da avaliação não são permitidas; use o ORM transacional.")
