"""Emissão interna e idempotente de certificados de treinamento.

Não implementa autorização HTTP. O chamador deve obter a participação
a partir de um acesso previamente autorizado.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from secrets import token_urlsafe

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    CertificadoTreinamento,
    Colaborador,
    ParticipacaoTreinamento,
    Questao,
    ResultadoQuiz,
    Treinamento,
)


class ErroCertificado(ValueError):
    """Erro controlado, sem exposição de informações do banco."""

    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)


@dataclass(frozen=True)
class ReciboCertificado:
    certificado_id: int
    participacao_id: int
    codigo_verificacao: str
    nome_participante: str
    titulo_treinamento: str
    emitido_em: datetime
    criado: bool = False


def _utc(valor):
    if valor is None:
        return None
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def _recibo(certificado, criado=False):
    return ReciboCertificado(
        certificado_id=certificado.id,
        participacao_id=certificado.participacao_id,
        codigo_verificacao=certificado.codigo_verificacao,
        nome_participante=certificado.nome_participante,
        titulo_treinamento=certificado.titulo_treinamento,
        emitido_em=certificado.emitido_em,
        criado=criado,
    )


def recibo_de_certificado(certificado):
    """Copia somente os dados históricos de um certificado persistido.

    Aceita instâncias persistentes ou desanexadas, nunca objetos novos ou
    removidos. Não valida aprovação, não altera dados e não autoriza acesso:
    o chamador deve aplicar a autorização antes de expor o recibo.
    """
    if not isinstance(certificado, CertificadoTreinamento):
        raise ErroCertificado("certificado_invalido")
    estado = inspect(certificado)
    if (
        not (estado.persistent or estado.detached)
        or estado.was_deleted
        or (estado.session is not None and certificado in estado.session.deleted)
    ):
        raise ErroCertificado("certificado_invalido")
    return _recibo(certificado)


def _validar_elegibilidade(session, participacao):
    conclusao = _utc(participacao.concluido_em)
    aprovacao = _utc(participacao.aprovado_em)

    if conclusao is None or aprovacao is None:
        raise ErroCertificado("nao_elegivel")

    if aprovacao < conclusao:
        raise ErroCertificado("historico_inconsistente")

    resultados = session.execute(
        select(ResultadoQuiz)
        .where(ResultadoQuiz.participacao_id == participacao.id)
        .order_by(
            ResultadoQuiz.numero_tentativa,
            ResultadoQuiz.id,
        )
    ).scalars().all()

    if resultados:
        # Com quiz, exige aprovação efetivamente persistida.
        aprovados = [
            resultado
            for resultado in resultados
            if resultado.aprovado is True
        ]

        if not aprovados:
            raise ErroCertificado("nao_elegivel")

        primeira = aprovados[0]

        if _utc(primeira.concluido_em) != aprovacao:
            raise ErroCertificado("historico_inconsistente")

        return

    # Sem resultados, somente admite o fluxo explícito sem quiz.
    # A plataforma registra conclusão e aprovação no mesmo instante.
    if aprovacao != conclusao:
        raise ErroCertificado("historico_inconsistente")

    possui_questoes = session.execute(
        select(Questao.id)
        .where(Questao.treinamento_id == participacao.treinamento_id)
        .limit(1)
    ).first()

    if possui_questoes is not None:
        raise ErroCertificado("historico_inconsistente")


def emitir_certificado(participacao_id):
    """Emite ou recupera um certificado em transação independente.

    Esta função é exclusivamente interna. Não aceitar participacao_id
    diretamente de requisições HTTP sem autorização prévia.
    """
    if type(participacao_id) is not int or participacao_id <= 0:
        raise ErroCertificado("participacao_invalida")

    try:
        with Session(db.engine) as session, session.begin():
            # Serializa emissões da mesma participação.
            participacao = session.execute(
                select(ParticipacaoTreinamento)
                .where(ParticipacaoTreinamento.id == participacao_id)
                .with_for_update()
            ).scalar_one_or_none()

            if participacao is None:
                raise ErroCertificado("participacao_indisponivel")

            existente = session.execute(
                select(CertificadoTreinamento)
                .where(
                    CertificadoTreinamento.participacao_id
                    == participacao.id
                )
            ).scalar_one_or_none()

            # Certificados emitidos são históricos.
            if existente is not None:
                return _recibo(existente)

            # Mesma ordem básica de bloqueios do fluxo do quiz:
            # participação -> treinamento -> colaborador.
            treinamento = session.execute(
                select(Treinamento)
                .where(Treinamento.id == participacao.treinamento_id)
                .with_for_update(read=True)
            ).scalar_one_or_none()

            colaborador = session.execute(
                select(Colaborador)
                .where(Colaborador.id == participacao.colaborador_id)
                .with_for_update(read=True, nowait=True)
            ).scalar_one_or_none()

            if treinamento is None or colaborador is None:
                raise ErroCertificado("participacao_indisponivel")

            _validar_elegibilidade(session, participacao)

            certificado = CertificadoTreinamento(
                participacao_id=participacao.id,
                codigo_verificacao=token_urlsafe(24),
                nome_participante=colaborador.nome,
                titulo_treinamento=treinamento.titulo,
                emitido_em=datetime.now(timezone.utc),
            )

            session.add(certificado)
            session.flush()

            return _recibo(certificado, criado=True)

    except SQLAlchemyError:
        # A transação é revertida pelo gerenciador de contexto.
        raise ErroCertificado("falha_banco") from None


def consultar_certificado(participacao):
    """Consulta autorizada pelo chamador, sem emissão ou alteração histórica.

    Retorna o recibo existente ou None se a primeira emissão estiver disponível.
    Compartilha a validação histórica com o serviço de emissão.
    """
    if participacao.concluido_em is None or participacao.aprovado_em is None:
        raise ErroCertificado("nao_elegivel")
    existente = db.session.execute(
        select(CertificadoTreinamento).where(
            CertificadoTreinamento.participacao_id == participacao.id
        )
    ).scalar_one_or_none()
    if existente is not None:
        return _recibo(existente)
    _validar_elegibilidade(db.session, participacao)
    return None
