"""Validação e criação idempotente; não altera a participação nem sua sessão."""
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.feedback_treinamento import FeedbackTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento

CAMPOS_NOTAS = ("avaliacao_geral", "avaliacao_conteudo", "avaliacao_clareza", "avaliacao_utilidade")


class FeedbackInvalido(ValueError):
    pass


class ParticipacaoNaoConcluida(ValueError):
    pass


class ParticipacaoNaoEncontrada(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoFeedback:
    feedback_id: int
    criado: bool


def validar_feedback(dados):
    """Aceita mapping ou MultiDict. Campos repetidos e notas não inteiras falham."""
    def valor_unico(campo, obrigatorio=False):
        valores = dados.getlist(campo) if hasattr(dados, "getlist") else ([dados[campo]] if campo in dados else [])
        if len(valores) > 1 or (obrigatorio and len(valores) != 1):
            raise FeedbackInvalido(f"Informe uma única resposta para {campo}.")
        return valores[0] if valores else None

    notas = {}
    for campo in CAMPOS_NOTAS:
        valor = valor_unico(campo, True)
        if type(valor) is int and 1 <= valor <= 5:
            notas[campo] = valor
        elif isinstance(valor, str) and valor.strip() in ("1", "2", "3", "4", "5"):
            notas[campo] = int(valor.strip())
        else:
            raise FeedbackInvalido(f"{campo} deve ser um inteiro de 1 a 5.")
    comentario = valor_unico("comentario")
    if comentario is not None:
        if not isinstance(comentario, str) or len(comentario) > 2000 or "\x00" in comentario:
            raise FeedbackInvalido("Comentário deve ser texto válido de até 2000 caracteres.")
        comentario = comentario.strip() or None
    return {**notas, "comentario": comentario}


def registrar_feedback(participacao_id, dados):
    """Transação própria: FOR UPDATE na participação, primeiro envio prevalece.

    Não faz commit/rollback de alterações pendentes em db.session. O chamador
    deve fornecer ID de participação persistida; autorização por token e CSRF
    pertencem à futura rota. Falhas revertem esta transação e são propagadas.
    """
    if type(participacao_id) is not int or participacao_id <= 0:
        raise ParticipacaoNaoEncontrada("Participação não encontrada.")
    with Session(db.engine) as session, session.begin():
        participacao = session.execute(select(ParticipacaoTreinamento).where(
            ParticipacaoTreinamento.id == participacao_id,
        ).with_for_update()).scalar_one_or_none()
        if participacao is None:
            raise ParticipacaoNaoEncontrada("Participação não encontrada.")
        if participacao.concluido_em is None:
            raise ParticipacaoNaoConcluida("Conclua o treinamento antes de avaliar.")
        existente = session.execute(select(FeedbackTreinamento.id).where(
            FeedbackTreinamento.participacao_id == participacao_id,
        )).scalar_one_or_none()
        if existente is not None:
            return ResultadoFeedback(existente, False)
        valores = validar_feedback(dados)
        feedback = FeedbackTreinamento(participacao_id=participacao_id, **valores)
        session.add(feedback)
        session.flush()
        return ResultadoFeedback(feedback.id, True)
