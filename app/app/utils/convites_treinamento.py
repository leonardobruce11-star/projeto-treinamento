"""Solicitação, cancelamento e reserva em transações próprias e curtas.

Não usa db.session do chamador, não renderiza mensagens e não altera a
participação. O transporte roda após o commit da reserva. Operação destinada
a PostgreSQL; concorrência é exercitada nesse banco nos testes.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4
from email_validator import validate_email, EmailNotValidError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
from app.models.tentativa_envio_convite import TentativaEnvioConvite as T, CODIGOS_ERRO
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.usuario import Usuario
from app.models.auditoria import Auditoria
from app.utils.transporte_convites import DadosEnvio, ResultadoTransporte, TransporteConvites


class ConviteInvalido(ValueError):
    pass


class ConviteNaoAutorizado(ConviteInvalido):
    pass


class ConfirmacaoConflitante(ConviteInvalido):
    pass


class TransicaoConviteInvalida(ConviteInvalido):
    pass


@dataclass(frozen=True)
class SolicitacaoConvite:
    envio_id: int
    criado: bool


@dataclass(frozen=True)
class ReservaConvite:
    tentativa_id: int
    dados: DadosEnvio
    transportar: bool = True


def _agora():
    return datetime.now(timezone.utc)


def _id(valor):
    if type(valor) is not int or valor <= 0:
        raise ConviteInvalido('Identificação inválida.')
    return valor


def _confirmacao(valor):
    try:
        if not isinstance(valor, (str, UUID)):
            raise ValueError()
        return UUID(str(valor))
    except (ValueError, AttributeError):
        raise ConviteInvalido('Confirmação deve ser um UUID válido.') from None


def _administrador(session, usuario_id):
    usuario = session.get(Usuario, _id(usuario_id))
    if usuario is None or not usuario.ativo or usuario.perfil != 'administrador':
        raise ConviteNaoAutorizado('Administrador ativo necessário.')


def _email(valor):
    try:
        if not isinstance(valor, str) or '\r' in valor or '\n' in valor:
            raise EmailNotValidError()
        return validate_email(valor, check_deliverability=False).normalized
    except EmailNotValidError:
        raise ConviteInvalido('Destinatário inválido.') from None


def _elegibilidade(session, participacao):
    colaborador = session.get(Colaborador, participacao.colaborador_id)
    treinamento = session.get(Treinamento, participacao.treinamento_id)
    if not colaborador.ativo:
        return None, 'colaborador_inativo'
    if not treinamento.ativo:
        return None, 'treinamento_inativo'
    if participacao.concluido_em is not None:
        return None, 'participacao_concluida'
    try:
        return _email(colaborador.email), None
    except ConviteInvalido:
        return None, 'destinatario_invalido'


def _auditar(session, envio, usuario_id, acao):
    session.add(Auditoria(usuario_id=usuario_id, acao=acao,
        objeto='envio_convite_treinamento', objeto_id=envio.id,
        detalhes=f'Participação {envio.participacao_id}; sequência {envio.sequencia}.'))


def _repeticao(envio, participacao_id, usuario_id, tipo):
    if (envio.participacao_id, envio.solicitado_por_usuario_id, envio.tipo) != (
            participacao_id, usuario_id, tipo):
        raise ConfirmacaoConflitante('Confirmação já utilizada em outra solicitação.')
    return SolicitacaoConvite(envio.id, False)


def _solicitar_na_sessao(session, participacao_id, usuario_id, confirmacao, tipo):
    _administrador(session, usuario_id)
    p = session.execute(select(P).where(P.id == participacao_id)
                        .with_for_update()).scalar_one_or_none()
    if p is None:
        raise ConviteInvalido('Participação não encontrada.')
    repetido = session.execute(select(E).where(
        E.identificador_confirmacao == confirmacao)).scalar_one_or_none()
    if repetido is not None:
        return _repeticao(repetido, participacao_id, usuario_id, tipo)
    ultimo = session.execute(select(E).where(E.participacao_id == p.id)
                             .order_by(E.sequencia.desc()).limit(1)).scalar_one_or_none()
    if tipo == 'primeiro_envio' and ultimo is not None:
        primeiro = session.execute(select(E.id).where(
            E.participacao_id == p.id, E.sequencia == 1)).scalar_one()
        return SolicitacaoConvite(primeiro, False)
    if tipo == 'reenvio':
        if ultimo is None:
            raise ConviteInvalido('Solicite o primeiro envio antes do reenvio.')
        if ultimo.estado in ('pendente', 'em_processamento'):
            raise TransicaoConviteInvalida('Existe envio pendente ou em processamento.')
    destinatario, erro = _elegibilidade(session, p)
    if erro:
        raise ConviteInvalido('Participação não elegível: ' + erro + '.')
    envio = E(participacao_id=p.id, sequencia=1 if ultimo is None else ultimo.sequencia + 1,
        tipo=tipo, solicitado_por_usuario_id=usuario_id, destinatario=destinatario,
        identificador_confirmacao=confirmacao, estado='pendente', versao_mensagem='convite-v1')
    session.add(envio); session.flush()
    _auditar(session, envio, usuario_id,
             'convite_primeiro_envio_solicitado' if tipo == 'primeiro_envio' else 'convite_reenvio_solicitado')
    return SolicitacaoConvite(envio.id, True)


def _solicitar(participacao_id, usuario_id, identificador_confirmacao, tipo):
    participacao_id = _id(participacao_id)
    confirmacao = _confirmacao(identificador_confirmacao)
    try:
        with Session(db.engine) as session, session.begin():
            return _solicitar_na_sessao(session, participacao_id, usuario_id, confirmacao, tipo)
    except IntegrityError:
        # UUID global: duas participações diferentes podem disputar a mesma
        # confirmação. A transação perdedora não deixa solicitação/auditoria.
        with Session(db.engine) as session:
            existente = session.execute(select(E).where(
                E.identificador_confirmacao == confirmacao)).scalar_one_or_none()
            if existente is not None:
                return _repeticao(existente, participacao_id, usuario_id, tipo)
        raise


def solicitar_primeiro_envio(participacao_id, usuario_id, identificador_confirmacao):
    return _solicitar(participacao_id, usuario_id, identificador_confirmacao, 'primeiro_envio')


def solicitar_reenvio(participacao_id, usuario_id, identificador_confirmacao):
    """Ação distinta e explícita; nunca invocada automaticamente pelo worker."""
    return _solicitar(participacao_id, usuario_id, identificador_confirmacao, 'reenvio')


def cancelar_envio(envio_id, usuario_id):
    with Session(db.engine) as session, session.begin():
        _administrador(session, usuario_id)
        envio = session.execute(select(E).where(E.id == _id(envio_id))
                                .with_for_update()).scalar_one_or_none()
        if envio is None:
            raise ConviteInvalido('Envio não encontrado.')
        if envio.estado == 'cancelado':
            return False
        if envio.estado != 'pendente':
            raise TransicaoConviteInvalida('Somente envio pendente pode ser cancelado.')
        envio.estado = 'cancelado'; envio.finalizado_em = _agora()
        _auditar(session, envio, usuario_id, 'convite_cancelado')
        return True


def _terminar(envio, tentativa, resultado):
    agora = _agora()
    envio.estado = resultado.estado; envio.finalizado_em = agora
    tentativa.resultado = resultado.estado; tentativa.codigo_erro = resultado.codigo_erro
    tentativa.finalizado_em = agora


def reservar_proximo_envio():
    """SKIP LOCKED evita que workers disputem a mesma reserva.

    Revalida elegibilidade e destinatário. Caso tenha mudado, registra falha
    sem chamar transporte. O retorno nunca inclui token nem corpo de e-mail.
    """
    with Session(db.engine) as session, session.begin():
        envio = session.execute(select(E).where(E.estado == 'pendente')
            .order_by(E.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if envio is None:
            return None
        envio.estado = 'em_processamento'; envio.processamento_iniciado_em = _agora()
        numero = session.execute(select(func.coalesce(func.max(T.numero_tentativa), 0))
                                  .where(T.envio_id == envio.id)).scalar_one() + 1
        tentativa = T(envio_id=envio.id, numero_tentativa=numero,
                      iniciado_em=envio.processamento_iniciado_em, message_id=uuid4())
        session.add(tentativa)
        p = session.get(P, envio.participacao_id)
        destinatario, erro = _elegibilidade(session, p)
        if not erro and destinatario != envio.destinatario:
            erro = 'destinatario_alterado'
        if erro:
            _terminar(envio, tentativa, ResultadoTransporte('falha', erro))
        session.flush()
        return ReservaConvite(tentativa.id, DadosEnvio(envio.id, envio.participacao_id,
                                                      envio.destinatario, tentativa.message_id), not erro)


def finalizar_envio(reserva, resultado):
    """Finalização idempotente; nenhum resultado pode sobrescrever o anterior."""
    if not isinstance(resultado, ResultadoTransporte) or resultado.estado not in (
            'enviado', 'falha', 'resultado_indeterminado'):
        raise ConviteInvalido('Resultado de transporte inválido.')
    if (resultado.estado == 'enviado' and resultado.codigo_erro is not None) or (
            resultado.estado != 'enviado' and resultado.codigo_erro not in CODIGOS_ERRO):
        raise ConviteInvalido('Código de transporte inválido.')
    with Session(db.engine) as session, session.begin():
        envio = session.execute(select(E).where(E.id == reserva.dados.envio_id)
                                .with_for_update()).scalar_one()
        tentativa = session.get(T, reserva.tentativa_id)
        if tentativa is None or tentativa.envio_id != envio.id or tentativa.message_id != reserva.dados.message_id:
            raise ConviteInvalido('Reserva inválida.')
        if tentativa.resultado is not None:
            if (tentativa.resultado, tentativa.codigo_erro) == (resultado.estado, resultado.codigo_erro):
                return False
            raise TransicaoConviteInvalida('Resultado já finalizado.')
        if envio.estado != 'em_processamento':
            raise TransicaoConviteInvalida('Envio não está em processamento.')
        _terminar(envio, tentativa, resultado)
        return True


def processar_proximo_envio(transporte: TransporteConvites):
    reserva = reservar_proximo_envio()
    if reserva is None:
        return None
    if not reserva.transportar:
        # Devolve o ID da falha de elegibilidade. None significa fila vazia,
        # para que uma falha não interrompa o consumo dos próximos registros.
        return reserva.dados.envio_id
    try:
        resultado = transporte.enviar(reserva.dados)
    except Exception:
        # Uma exceção genérica não comprova que o transporte não aceitou.
        # Não persistir/logar str(exc), traceback ou payload potencialmente secreto.
        resultado = ResultadoTransporte('resultado_indeterminado', 'excecao_transporte')
    if not isinstance(resultado, ResultadoTransporte) or resultado.estado not in (
            'enviado', 'falha', 'resultado_indeterminado') or (
            resultado.estado == 'enviado' and resultado.codigo_erro is not None) or (
            resultado.estado != 'enviado' and resultado.codigo_erro not in CODIGOS_ERRO):
        resultado = ResultadoTransporte('resultado_indeterminado', 'retorno_invalido')
    finalizar_envio(reserva, resultado)
    return reserva.dados.envio_id
