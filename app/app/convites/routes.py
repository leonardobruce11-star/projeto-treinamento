"""Rotas administrativas: GET consulta; todas as ações usam POST e CSRF global."""
from uuid import uuid4
from functools import wraps
from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
from app.models.tentativa_envio_convite import TentativaEnvioConvite as T
from app.models.colaborador import Colaborador
from app.models.campanha import Campanha
from app.models.treinamento import Treinamento
from app.convites.service import (
    snapshot, avaliar, confirmar, ESTADOS, MOTIVOS, verificar_configuracao, remetente,
)
from app.utils.convites_treinamento import cancelar_envio, ConviteInvalido

convites_bp = Blueprint('convites', __name__, url_prefix='/convites')


def administrador(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.ativo or current_user.perfil != 'administrador':
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


def assinador():
    return URLSafeTimedSerializer(current_app.secret_key, salt='convites-18.6d')


def ler_assinado(valor, finalidade):
    try:
        data = assinador().loads(valor, max_age=1800)
        if data['usuario'] != current_user.id or data['finalidade'] != finalidade:
            raise ValueError()
        return data
    except (BadData, ValueError, TypeError, KeyError):
        abort(400, description='Seleção inválida ou expirada; selecione novamente.')


def config():
    return current_app.extensions['mail_convites_config']


@convites_bp.after_request
def privacidade(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


def visao_geral(pagina):
    # Consulta somente solicitações existentes, sem carregar token ou compor mensagem.
    with Session(db.engine) as session:
        envios = list(session.execute(select(
            E.id, E.participacao_id, E.sequencia, E.tipo, E.destinatario,
            E.estado, E.criado_em, Colaborador.nome.label('colaborador'),
            Treinamento.titulo.label('treinamento'))
            .join(P, P.id == E.participacao_id)
            .join(Colaborador, Colaborador.id == P.colaborador_id)
            .join(Treinamento, Treinamento.id == P.treinamento_id)
            .order_by(E.id.desc()).offset((pagina - 1) * 100).limit(101)).mappings())
    return render_template('convites/visao_geral.html', envios=envios[:100],
                           pagina=pagina, more=len(envios) > 100, estados=ESTADOS)


@convites_bp.get('/')
@administrador
def listar():
    campos = [k for k in ('campanha_id', 'treinamento_id', 'participacao_id') if k in request.args]
    if len(campos) > 1:
        abort(400, description='Informe uma campanha, treinamento ou participação.')
    page = request.args.get('pagina', 1, type=int)
    if not page or page < 1:
        abort(400)
    if not campos:
        return visao_geral(page)
    campo = campos[0]
    ident = request.args.get(campo, type=int)
    if not ident or ident <= 0:
        abort(400)
    scope = {campo: ident}
    bloqueio = None
    try:
        verificar_configuracao(config())
    except ValueError:
        bloqueio = 'Convites bloqueados: configure MAIL_ENABLED, backend fake, BASE_URL e remetente institucional.'
    with Session(db.engine) as session:
        model = {'campanha_id': Campanha, 'treinamento_id': Treinamento, 'participacao_id': P}[campo]
        if session.get(model, ident) is None:
            abort(404)
        condition = P.id == ident if campo == 'participacao_id' else getattr(P, campo) == ident
        ps = list(session.execute(select(P).where(condition).order_by(P.id)
            .offset((page - 1) * 100).limit(101)).scalars())
        more = len(ps) > 100
        rows = []
        for p in ps[:100]:
            item = snapshot(session, p)
            ultimo = session.execute(select(E).where(E.participacao_id == p.id)
                .order_by(E.sequencia.desc()).limit(1)).scalar_one_or_none()
            tipo = 'reenvio' if ultimo else 'primeiro_envio'
            motivo = avaliar(session, item, tipo, config())
            rows.append(dict(item=item, estado=ESTADOS[ultimo.estado if ultimo else 'nao_enviado'],
                tipo=tipo, motivo=MOTIVOS.get(motivo),
                escolha=assinador().dumps(dict(usuario=current_user.id, finalidade='selecao',
                    item=item, escopo=scope))))
        history = []
        if campo == 'participacao_id':
            for e in session.execute(select(E).where(E.participacao_id == ident).order_by(E.sequencia)).scalars():
                erro = session.execute(select(T.codigo_erro).where(T.envio_id == e.id)
                    .order_by(T.numero_tentativa.desc()).limit(1)).scalar_one_or_none()
                history.append(dict(id=e.id, sequencia=e.sequencia, tipo=e.tipo, destinatario=e.destinatario,
                    solicitado=e.criado_em, processado=e.finalizado_em or e.processamento_iniciado_em,
                    estado=ESTADOS[e.estado], pendente=e.estado == 'pendente', erro=erro))
    return render_template('convites/listar.html', rows=rows, history=history, scope=scope,
                           pagina=page, more=more, bloqueio=bloqueio)


@convites_bp.post('/selecionar')
@administrador
def selecionar():
    tipo = request.form.get('tipo')
    escolhas = request.form.getlist('escolha')
    if tipo not in ('primeiro_envio', 'reenvio') or not 1 <= len(escolhas) <= 100:
        abort(400, description='Selecione de 1 a 100 participações e uma ação explícita.')
    try:
        verificar_configuracao(config())
    except ValueError:
        abort(409, description='Configuração fake indisponível.')
    dados = [ler_assinado(v, 'selecao') for v in escolhas]
    if any(v['escopo'] != dados[0]['escopo'] for v in dados):
        abort(400, description='Selecione participações do mesmo contexto.')
    itens = {v['item']['id']: v['item'] for v in dados}
    envelope = dict(usuario=current_user.id, finalidade='confirmacao', confirmacao=str(uuid4()),
        tipo=tipo, itens=list(itens.values()), remetente=remetente(config()), excluidos={})
    with Session(db.engine) as session:
        for item in envelope['itens']:
            erro = avaliar(session, item, tipo, config())
            if erro:
                envelope['excluidos'][str(item['id'])] = erro
    # Prévia sem ler token nem construir URL individual.
    return render_template('convites/confirmar.html', envelope=envelope,
        confirmacao=assinador().dumps(envelope), motivos=MOTIVOS)


@convites_bp.post('/confirmar')
@administrador
def confirmar_post():
    envelope = ler_assinado(request.form.get('confirmacao', ''), 'confirmacao')
    try:
        resultado = confirmar(envelope, current_user.id, config())
    except ConviteInvalido:
        abort(409, description='Confirmação conflitante ou configuração alterada; selecione novamente.')
    except ValueError:
        abort(409, description='Configuração fake indisponível.')
    return render_template('convites/resultado.html', resultado=resultado, motivos=MOTIVOS)


@convites_bp.post('/<int:envio_id>/cancelar')
@administrador
def cancelar(envio_id):
    try:
        cancelar_envio(envio_id, current_user.id)
    except ConviteInvalido:
        abort(409, description='Somente solicitações pendentes podem ser canceladas.')
    with Session(db.engine) as session:
        pid = session.execute(select(E.participacao_id).where(E.id == envio_id)).scalar_one()
    return render_template('convites/cancelado.html', participacao_id=pid)
