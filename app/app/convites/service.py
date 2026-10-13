"""Snapshot público seguro e confirmação atômica com recibo persistente."""
import hashlib
import json
from uuid import UUID, uuid5
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.confirmacao_convites import ConfirmacaoConvites as R
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.colaborador import Colaborador as C
from app.models.treinamento import Treinamento as T
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
from app.models.auditoria import Auditoria
from app.utils.convites_treinamento import (
    _administrador, _elegibilidade, _email, _solicitar_na_sessao,
    ConviteInvalido, ConfirmacaoConflitante,
)
from app.utils.mail_convites import endereco, texto_seguro, validar_base_url, MailBloqueado

ESTADOS = dict(nao_enviado='Não enviado', pendente='Pendente', em_processamento='Em processamento',
    enviado='Enviado', falha='Falha', resultado_indeterminado='Resultado indeterminado', cancelado='Cancelado')
MOTIVOS = dict(participacao_inexistente='Participação não encontrada', colaborador_inativo='Colaborador inativo',
    treinamento_inativo='Treinamento inativo', participacao_concluida='Participação concluída',
    destinatario_invalido='E-mail inválido', destinatario_alterado='E-mail alterado; selecione novamente',
    contexto_alterado='Nome, treinamento ou campanha alterados; selecione novamente',
    envio_existente='Já existe envio; use a ação explícita de reenvio',
    sem_primeiro_envio='Não existe primeiro envio', envio_ativo='Existe envio pendente ou em processamento',
    dados_mensagem_invalidos='Nome ou título inválido para a mensagem',
    destinatario_bloqueado='Destinatário fora da lista de testes')


def verificar_configuracao(cfg):
    # Nunca escolhe/adapta SMTP, mesmo que o ambiente esteja configurado para ele.
    if cfg.backend != 'fake':
        raise MailBloqueado('18.6D permite somente MAIL_BACKEND=fake')
    if not cfg.enabled:
        raise MailBloqueado('MAIL_ENABLED está desabilitado')
    validar_base_url(cfg.base_url)
    endereco(cfg.from_address, 'MAIL_FROM_ADDRESS')


def remetente(cfg):
    return {'address': cfg.from_address, 'name': cfg.from_name, 'reply_to': cfg.reply_to}


def snapshot(session, p):
    c = session.get(C, p.colaborador_id)
    t = session.get(T, p.treinamento_id)
    try:
        email = _email(c.email)
    except ConviteInvalido:
        email = ''
    return dict(id=p.id, email=email, nome=c.nome, titulo=t.titulo,
                treinamento_id=t.id, colaborador_id=c.id, campanha_id=p.campanha_id)


def avaliar(session, item, tipo, cfg, *, bloquear=False):
    stmt = select(P).where(P.id == item['id'])
    if bloquear:
        stmt = stmt.with_for_update()
    p = session.execute(stmt).scalar_one_or_none()
    if p is None:
        return 'participacao_inexistente'
    if bloquear:
        # Mesma transação que snapshot e solicitação: impede troca de e-mail
        # ou de elegibilidade enquanto a confirmação grava o destinatário.
        for model, ident in [(C, p.colaborador_id), (T, p.treinamento_id)]:
            session.execute(select(model).where(model.id == ident).with_for_update()).scalar_one()
    email, erro = _elegibilidade(session, p)
    if erro:
        return erro
    atual = snapshot(session, p)
    if email != item['email']:
        return 'destinatario_alterado'
    if atual != item:
        return 'contexto_alterado'
    try:
        texto_seguro(atual['nome'], 'Nome')
        texto_seguro(atual['titulo'], 'Título')
        endereco(email)
    except ValueError:
        return 'dados_mensagem_invalidos'
    if cfg.test_recipients and email not in cfg.test_recipients:
        return 'destinatario_bloqueado'
    ultimo = session.execute(select(E).where(E.participacao_id == p.id)
        .order_by(E.sequencia.desc()).limit(1)).scalar_one_or_none()
    if tipo == 'primeiro_envio' and ultimo is not None:
        return 'envio_existente'
    if tipo == 'reenvio':
        if ultimo is None:
            return 'sem_primeiro_envio'
        if ultimo.estado in ('pendente', 'em_processamento'):
            return 'envio_ativo'
    return None


def confirmar(envelope, usuario_id, cfg):
    verificar_configuracao(cfg)
    ident = UUID(envelope['confirmacao'])
    digest = hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def repetir(session):
        r = session.get(R, ident)
        if r is None:
            return None
        if r.usuario_id != usuario_id or r.assinatura != digest:
            raise ConfirmacaoConflitante('Confirmação conflitante.')
        return r.resultado

    try:
        with Session(db.engine) as session, session.begin():
            _administrador(session, usuario_id)
            anterior = repetir(session)
            if anterior is not None:
                return anterior
            # UNIQUE/PK serializa duplo clique inclusive com múltiplos processos.
            recibo = R(id=ident, usuario_id=usuario_id, assinatura=digest, resultado={})
            session.add(recibo)
            session.flush()
            if envelope['remetente'] != remetente(cfg):
                raise ConviteInvalido('Remetente alterado; selecione novamente.')
            # Ordem global de locks evita deadlock entre lotes sobrepostos,
            # inclusive quando diferentes participações usam o mesmo colaborador.
            ids = sorted(i['id'] for i in envelope['itens'])
            ps = list(session.execute(select(P).where(P.id.in_(ids)).order_by(P.id)
                .with_for_update()).scalars())
            for model, campo in [(C, 'colaborador_id'), (T, 'treinamento_id')]:
                relacionados = sorted({getattr(p, campo) for p in ps})
                list(session.execute(select(model).where(model.id.in_(relacionados))
                    .order_by(model.id).with_for_update()).scalars())
            resultados = []
            for item in sorted(envelope['itens'], key=lambda i: i['id']):
                # Exclusão na prévia é definitiva para esta confirmação.
                erro = envelope['excluidos'].get(str(item['id'])) or avaliar(
                    session, item, envelope['tipo'], cfg)
                if erro:
                    resultados.append(dict(participacao_id=item['id'], motivo=erro))
                    continue
                r = _solicitar_na_sessao(session, item['id'], usuario_id,
                    uuid5(ident, str(item['id'])), envelope['tipo'])
                resultados.append(dict(participacao_id=item['id'], envio_id=r.envio_id))
            resultado = dict(selecionados=len(envelope['itens']), itens=resultados)
            recibo.resultado = resultado
            session.add(Auditoria(usuario_id=usuario_id, acao='convites_confirmados',
                objeto='confirmacao_convites', detalhes=json.dumps(resultado, sort_keys=True)))
            return resultado
    except IntegrityError:
        # A inserção concorrente espera pelo commit vencedor; rollback não
        # deixa solicitações nem auditoria parcial. Não oculta outros erros SQL.
        with Session(db.engine) as session:
            _administrador(session, usuario_id)
            anterior = repetir(session)
            if anterior is not None:
                return anterior
        raise
