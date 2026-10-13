"""Serviços internos 19.2D2. Nenhuma rota pública chama este módulo.

Cada operação possui Session própria; não confirma alterações de db.session.
O chamador HTTP futuro deve validar CSRF e obter token/alias do acesso autorizado.
Alias só é aceito com grant da sessão Flask assinada, nunca com grant do formulário.
Não registrar credenciais, identidade, respostas ou parâmetros SQL.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID
from flask import has_request_context, session as browser_session
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session, selectinload
from app.extensions import db
from app.models import (ParticipacaoTreinamento as P, Treinamento as T, Colaborador as C,
                        ResultadoQuiz as R, SubmissaoQuiz as S, Questao, ModuloTreinamento)
from app.utils.aprovacao_treinamento import avaliar_aprovacao
from app.utils.progresso_treinamento import calcular_progresso
from app.utils.resultados_quiz import primeira_aprovacao, ultima_tentativa, resultado_representativo
from app.utils.painel_colaboradores import status_participacao

MAX_INTEGER = 2147483647


class ErroQuiz(ValueError):
    """Código fixo e seguro; nunca inclui entradas ou exceções SQL originais."""
    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)


@dataclass(frozen=True)
class ElegibilidadeQuiz:
    tipo: str
    permitido: bool
    motivo: str


@dataclass(frozen=True)
class ReciboSubmissao:
    submissao_id: UUID = field(repr=False)
    participacao_id: int
    estado: str
    resultado_id: int | None
    numero_tentativa: int | None
    aprovado: bool | None
    criada: bool = False


def elegibilidade_quiz(participacao, treinamento, colaborador, resultados, progresso,
                       questoes, *, autorizada=False, submissao=None):
    """Função pura; todos os dados/relações devem estar explicitamente carregados.

    Processada representa replay autorizado, não autorização para nova avaliação.
    Nunca infere classificação histórica por nota ou critério vigente.
    """
    deny = lambda tipo: ElegibilidadeQuiz(tipo, False, tipo)
    if (not autorizada or participacao is None or treinamento is None or colaborador is None
            or not treinamento.ativo or not colaborador.ativo):
        return deny('nao_autorizada')
    if submissao is not None:
        if submissao.participacao_id != participacao.id:
            return deny('nao_autorizada')
        if submissao.estado == 'processada':
            return ElegibilidadeQuiz('processada', True, 'replay')
    if not questoes:
        return deny('sem_quiz')
    primeira = primeira_aprovacao(resultados)
    if primeira is not None:
        if (participacao.concluido_em is None or participacao.aprovado_em is None
                or _utc(participacao.aprovado_em) != _utc(primeira.concluido_em)):
            return deny('inconsistente')
        return deny('aprovada')
    if participacao.aprovado_em is not None:
        return deny('inconsistente')
    if (not progresso.cursor_consistente or not progresso.todos_modulos_concluidos
            or participacao.iniciado_em is None
            or any(not q.alternativas or sum(a.correta is True for a in q.alternativas) != 1
                   for q in questoes)):
        return deny('inconsistente')
    if participacao.concluido_em is None:
        return deny('inconsistente') if resultados else ElegibilidadeQuiz('inicial', True, 'inicial')
    representante = resultado_representativo(resultados)
    if representante is None or representante.aprovado is None:
        return deny('historica')
    if ultima_tentativa(resultados).aprovado is None:
        return deny('inconsistente')
    if status_participacao(participacao, representante) != 'reprovado':
        return deny('inconsistente')
    return ElegibilidadeQuiz('refacao', True, 'refacao')


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _identidade(value):
    try:
        identity = value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ErroQuiz('indisponivel') from None
    if identity.version != 4:
        raise ErroQuiz('indisponivel')
    return identity


def _bloquear_contexto(session, token):
    """Alias: execução→alvo→ponte; todos: participação→treinamento→colaborador.

    Colaborador FOR SHARE NOWAIT evita esperar na ordem inversa aos convites
    (participação→colaborador→treinamento). Falha exige rollback antes do retry.
    """
    from app.simulacoes import educacao
    from app.simulacoes.snapshots import SimulacaoInvalida, agora
    from app.simulacoes.leases import aware
    if not isinstance(token, str) or not token:
        raise ErroQuiz('indisponivel')
    ponte = grant = None
    if token.startswith(educacao.PREFIXO):
        if not has_request_context():
            raise ErroQuiz('indisponivel')
        grant = browser_session.get('simulacao_acesso', {})
        try:
            if grant.get('alias') != token or grant.get('expira', 0) <= agora().timestamp():
                raise ErroQuiz('indisponivel')
            ponte, alvo, revisao = educacao.resolver(session, grant.get('ponte'), consumida=True)
            pid = educacao.participacao(session, alvo, revisao)
            if ponte.estado != 'consumida' or pid is None or pid != grant.get('participacao'):
                raise ErroQuiz('indisponivel')
        except (SimulacaoInvalida, ValueError, TypeError, AttributeError):
            raise ErroQuiz('indisponivel') from None
        filtro = P.id == pid
    else:
        filtro = P.token == token
    p = session.execute(select(P).where(filtro).with_for_update()
                        .execution_options(populate_existing=True)).scalar_one_or_none()
    if p is None:
        raise ErroQuiz('indisponivel')
    t = session.execute(select(T).where(T.id == p.treinamento_id).with_for_update(read=True)
                        .execution_options(populate_existing=True)).scalar_one_or_none()
    c = session.execute(select(C).where(C.id == p.colaborador_id)
                        .with_for_update(read=True, nowait=True)
                        .execution_options(populate_existing=True)).scalar_one_or_none()
    if t is None or c is None or not t.ativo or not c.ativo:
        raise ErroQuiz('indisponivel')
    if ponte is not None and (aware(ponte.expira_em) <= agora()
                             or grant.get('expira', 0) <= agora().timestamp()):
        raise ErroQuiz('indisponivel')
    return p, t, c


def _dados(session, p, t, c):
    resultados = session.execute(select(R).where(R.participacao_id == p.id)
        .order_by(R.numero_tentativa, R.id)).scalars().all()
    modulos = session.execute(select(ModuloTreinamento).where(
        ModuloTreinamento.treinamento_id == t.id)).scalars().all()
    questoes = session.execute(select(Questao).where(Questao.treinamento_id == t.id)
        .order_by(Questao.ordem, Questao.id).options(selectinload(Questao.alternativas))).scalars().all()
    e = elegibilidade_quiz(p, t, c, resultados, calcular_progresso(modulos, p.ultimo_modulo_id),
                          questoes, autorizada=True)
    return e, resultados, questoes


def _exigir_base(e, resultados, base):
    if not e.permitido:
        raise ErroQuiz(e.motivo)
    if e.tipo == 'inicial':
        if base is not None:
            raise ErroQuiz('base_invalida')
    elif base is None or ultima_tentativa(resultados).id != base:
        raise ErroQuiz('base_invalida')


def _recibo(session, sub, criada=False):
    r = session.execute(select(R).where(R.id == sub.resultado_id,
        R.participacao_id == sub.participacao_id)).scalar_one() if sub.resultado_id is not None else None
    return ReciboSubmissao(sub.id, sub.participacao_id, sub.estado, sub.resultado_id,
                          r.numero_tentativa if r else None, r.aprovado if r else None, criada)


def _erro_banco(exc):
    code = getattr(getattr(exc, 'orig', None), 'sqlstate', None)
    return ErroQuiz('ocupado' if code in ('55P03', '40P01', '57014') else 'falha_banco')


def preparar_submissao(token, resultado_base_id=None):
    """Cria/recupera intenção explícita; sem resultado, número ou alteração de datas."""
    if resultado_base_id is not None and (type(resultado_base_id) is not int or resultado_base_id <= 0):
        raise ErroQuiz('base_invalida')
    try:
        with Session(db.engine) as session, session.begin():
            p, t, c = _bloquear_contexto(session, token)
            sub = session.execute(select(S).where(S.participacao_id == p.id,
                S.resultado_base_id == resultado_base_id)).scalar_one_or_none()
            if sub is not None:
                return _recibo(session, sub)
            e, resultados, _ = _dados(session, p, t, c)
            _exigir_base(e, resultados, resultado_base_id)
            if session.execute(select(S.id).where(S.participacao_id == p.id,
                    S.estado == 'pendente')).first():
                raise ErroQuiz('pendente_obsoleta')
            sub = S(participacao_id=p.id, resultado_base_id=resultado_base_id)
            session.add(sub)
            session.flush()
            return _recibo(session, sub, True)
    except SQLAlchemyError as exc:
        raise _erro_banco(exc) from None


def _validar_respostas(questoes, dados):
    """Mapping/MultiDict; ignora metadados, nunca aceita questão/alternativa alheia."""
    if not hasattr(dados, 'get') or any(not isinstance(k, str) for k in dados):
        raise ErroQuiz('respostas_invalidas')
    campos = {f'questao_{q.id}' for q in questoes}
    if any(k.startswith('questao_') and k not in campos for k in dados):
        raise ErroQuiz('respostas_invalidas')
    respostas = []
    for q in questoes:
        campo = f'questao_{q.id}'
        valores = dados.getlist(campo) if hasattr(dados, 'getlist') else ([dados[campo]] if campo in dados else [])
        if len(valores) != 1 or type(valores[0]) not in (str, int):
            raise ErroQuiz('respostas_invalidas')
        alternativa = next((a for a in q.alternativas if str(a.id) == str(valores[0])), None)
        if alternativa is None:
            raise ErroQuiz('respostas_invalidas')
        respostas.append(alternativa)
    return respostas


def corrigir_submissao(token, submissao_id, respostas):
    """Primeiro commit válido vence; replay não interpreta as novas respostas."""
    identity = _identidade(submissao_id)
    try:
        with Session(db.engine) as session, session.begin():
            p, t, c = _bloquear_contexto(session, token)
            sub = session.execute(select(S).where(S.id == identity,
                S.participacao_id == p.id)).scalar_one_or_none()
            if sub is None:
                raise ErroQuiz('indisponivel')
            if sub.estado == 'processada':
                return _recibo(session, sub)
            e, resultados, questoes = _dados(session, p, t, c)
            _exigir_base(e, resultados, sub.resultado_base_id)
            selecionadas = _validar_respostas(questoes, respostas)
            maior = session.execute(select(func.max(R.numero_tentativa)).where(
                R.participacao_id == p.id)).scalar_one() or 0
            if maior >= MAX_INTEGER:
                raise ErroQuiz('numero_esgotado')
            a = avaliar_aprovacao(len(questoes), sum(item.correta for item in selecionadas),
                                  t.nota_minima_percentual)
            instante = datetime.now(timezone.utc)
            r = R(participacao_id=p.id, numero_tentativa=maior + 1,
                total_questoes=a.total_questoes, total_acertos=a.total_acertos,
                percentual=float(a.percentual), percentual_minimo_aplicado=a.percentual_minimo,
                minimo_acertos_aplicado=a.minimo_acertos, aprovado=a.aprovado, concluido_em=instante)
            session.add(r)
            if p.concluido_em is None:
                p.concluido_em = instante
            if a.aprovado and p.aprovado_em is None:
                p.aprovado_em = instante
            session.flush()
            sub.resultado_id = r.id
            sub.estado = 'processada'
            sub.processada_em = instante
            session.flush()
            return _recibo(session, sub)
    except IntegrityError as exc:
        # Toda escrita anterior já reverteu. Só recupera o MESMO UUID confirmado.
        try:
            with Session(db.engine) as session, session.begin():
                p, _, _ = _bloquear_contexto(session, token)
                sub = session.execute(select(S).where(S.id == identity,
                    S.participacao_id == p.id, S.estado == 'processada')).scalar_one_or_none()
                if sub is not None:
                    return _recibo(session, sub)
        except SQLAlchemyError as recovery:
            raise _erro_banco(recovery) from None
        raise _erro_banco(exc) from None
    except SQLAlchemyError as exc:
        raise _erro_banco(exc) from None
