from app.utils.resultados_quiz import resultado_representativo
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from flask_wtf.csrf import CSRFError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.treinamento import Treinamento
from app.utils.aprovacao_treinamento import avaliar_aprovacao
from app.utils.tempos_treinamento import tempos_da_participacao
from app.utils.progresso_treinamento import calcular_progresso, ordenar_modulos
from app.utils.estrutura_treinamento import bloquear_sequencia
from app.models.tempo_treinamento import TempoTreinamento
from app.models.questao import Questao
from app.models.feedback_treinamento import FeedbackTreinamento
from app.utils.feedback_treinamento import (
    registrar_feedback, FeedbackInvalido,
    ParticipacaoNaoConcluida, ParticipacaoNaoEncontrada,
)

from app.models.modulo_treinamento import (
    ModuloTreinamento,
)

from app.models.participacao_treinamento import (
    ParticipacaoTreinamento,
)

from app.models.resultado_quiz import (
    ResultadoQuiz,
)


# ==========================================================
# BLUEPRINT
# ==========================================================

portal_treinamento_bp = Blueprint(
    "portal_treinamento",
    __name__,
    url_prefix="/aprender",
)


# ==========================================================
# OBTER PARTICIPAÇÃO
# ==========================================================

def filtro_participacao(token):
    from app.simulacoes.educacao import filtro_portal
    return filtro_portal(token)


def obter_participacao(token):
    return ParticipacaoTreinamento.query.filter(filtro_participacao(token)).first()


@portal_treinamento_bp.context_processor
def token_de_acesso():
    value=(request.view_args or {}).get('token')
    return {'acesso_token':value} if value else {}


@portal_treinamento_bp.after_request
def privacidade_acesso_sessao(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


# ==========================================================
# VALIDAR PARTICIPAÇÃO / TREINAMENTO
# ==========================================================

def participacao_valida(participacao):

    return (
        participacao
        and participacao.treinamento
        and participacao.treinamento.ativo
        and participacao.colaborador
        and participacao.colaborador.ativo
    )



# ==========================================================
# HEARTBEAT: TEMPO AGREGADO, SEM ALTERAR PROGRESSO
# ==========================================================

HEARTBEAT_MAX_SEGUNDOS = 30


@portal_treinamento_bp.errorhandler(CSRFError)
def erro_csrf_portal(erro):
    if request.endpoint == "portal_treinamento.heartbeat":
        return jsonify(sucesso=False, erro="csrf_invalido"), 400
    if request.endpoint in ("portal_treinamento.quiz", "portal_treinamento.preparar_quiz"):
        return render_template("portal_treinamento/erro_quiz.html",
            mensagem="O formulário expirou ou não pôde ser validado. Atualize a avaliação para continuar."), 400
    return erro.get_response()


@portal_treinamento_bp.post(
    "/t/<token>/modulo/<int:modulo_id>/heartbeat"
)
def heartbeat(token, modulo_id):
    participacao = obter_participacao(token)
    if not participacao_valida(participacao):
        return jsonify(sucesso=False, erro="participacao_indisponivel"), 404

    dados = request.get_json(silent=True)
    segundos = dados.get("segundos") if isinstance(dados, dict) else None
    if type(segundos) is not int or not 1 <= segundos <= HEARTBEAT_MAX_SEGUNDOS:
        return jsonify(sucesso=False, erro="incremento_invalido"), 400

    try:
        # Serializa heartbeats da participação, inclusive a primeira criação.
        # O bloqueio não modifica a participação. Permite obter o delta exato
        # da resposta sem uma leitura concorrente desatualizada do agregado.
        participacao = db.session.execute(
            db.select(ParticipacaoTreinamento)
            .where(ParticipacaoTreinamento.id == participacao.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        # A sequência pode ter mudado enquanto a participação aguardava a trava.
        bloquear_sequencia(participacao.treinamento_id, db.session, compartilhado=True)
        modulos = db.session.execute(
            db.select(ModuloTreinamento).where(
                ModuloTreinamento.treinamento_id == participacao.treinamento_id,
            ).execution_options(populate_existing=True)
        ).scalars().all()
        estado = calcular_progresso(modulos, participacao.ultimo_modulo_id)
        if estado.modulo_por_id(modulo_id) is None:
            db.session.rollback()
            return jsonify(sucesso=False, erro="modulo_invalido"), 404
        if not estado.pode_acessar(modulo_id):
            db.session.rollback()
            return jsonify(sucesso=False, erro="modulo_bloqueado"), 403

        total_anterior = db.session.execute(
            db.select(TempoTreinamento.segundos_ativos).where(
                TempoTreinamento.participacao_id == participacao.id,
                TempoTreinamento.modulo_id == modulo_id,
            )
        ).scalar_one_or_none() or 0

        tabela = TempoTreinamento.__table__
        inserir = pg_insert(tabela).values(
            participacao_id=participacao.id,
            modulo_id=modulo_id,
            segundos_ativos=0,
            ultima_atividade_em=db.func.statement_timestamp(),
            criado_em=db.func.statement_timestamp(),
            atualizado_em=db.func.statement_timestamp(),
        )
        referencia = db.func.greatest(
            inserir.excluded.ultima_atividade_em,
            tabela.c.ultima_atividade_em,
        )
        decorrido = db.func.floor(db.extract(
            "epoch", referencia - tabela.c.ultima_atividade_em,
        ))
        incremento = db.case(
            (tabela.c.ultima_atividade_em.is_(None), 0),
            else_=db.cast(
                db.func.least(segundos, HEARTBEAT_MAX_SEGUNDOS, decorrido),
                db.Integer,
            ),
        )
        comando = inserir.on_conflict_do_update(
            constraint="uq_tempos_treinamento_participacao_modulo",
            set_={
                "segundos_ativos": tabela.c.segundos_ativos + incremento,
                "ultima_atividade_em": referencia,
                "atualizado_em": db.func.greatest(
                    inserir.excluded.atualizado_em, tabela.c.atualizado_em,
                ),
            },
        ).returning(tabela.c.segundos_ativos)
        total = db.session.execute(comando).scalar_one()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        # Não registrar a URL/token nem parâmetros SQL no log.
        current_app.logger.error("Falha ao registrar heartbeat de treinamento")
        return jsonify(sucesso=False, erro="falha_ao_registrar"), 500

    return jsonify(
        sucesso=True,
        incremento_efetivo=total - total_anterior,
        segundos_ativos=total,
    )


# ==========================================================
# INÍCIO
# ==========================================================

@portal_treinamento_bp.get(
    "/t/<token>"
)
def inicio(token):

    participacao = obter_participacao(
        token
    )

    if not participacao_valida(
        participacao
    ):

        return (
            render_template(
                "portal_treinamento/indisponivel.html"
            ),
            404,
        )

    treinamento = (
        participacao.treinamento
    )

    estado = calcular_progresso(treinamento.modulos, participacao.ultimo_modulo_id)
    modulos = estado.modulos

    # ======================================================
    # IMPORTANTE
    # ======================================================
    #
    # GET apenas apresenta a página.
    #
    # Não registramos mais iniciado_em aqui.
    # O início será registrado pela rota POST /iniciar.
    # ======================================================

    return render_template(
        "portal_treinamento/inicio.html",
        treinamento=treinamento,
        modulos=modulos,
        estado_progresso=estado,
        participacao=participacao,
    )


# ==========================================================
# REGISTRAR INÍCIO
# ==========================================================

@portal_treinamento_bp.post(
    "/t/<token>/iniciar"
)
def iniciar(token):

    # Serializa início com avanço e quiz; relê após esperar pela trava.
    participacao = db.session.execute(
        db.select(ParticipacaoTreinamento)
        .where(filtro_participacao(token))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()

    if not participacao_valida(
        participacao
    ):

        return (
            render_template(
                "portal_treinamento/indisponivel.html"
            ),
            404,
        )

    treinamento = (
        participacao.treinamento
    )

    bloquear_sequencia(treinamento.id, db.session, compartilhado=True)
    modulos = ordenar_modulos(treinamento.modulos)

    if not modulos:

        return render_template(
            "portal_treinamento/inicio.html",
            treinamento=treinamento,
            modulos=modulos,
            estado_progresso=calcular_progresso(modulos, participacao.ultimo_modulo_id),
            participacao=participacao,
        )

    # ======================================================
    # REGISTRAR INÍCIO SOMENTE UMA VEZ
    # ======================================================

    if participacao.iniciado_em is None:

        participacao.iniciado_em = (
            datetime.now(
                timezone.utc
            )
        )

        db.session.commit()

    # ======================================================
    # ABRIR PRIMEIRO MÓDULO
    # ======================================================

    primeiro_modulo = (
        modulos[0]
    )

    return redirect(
        url_for(
            "portal_treinamento.modulo",
            token=token,
            modulo_id=primeiro_modulo.id,
        )
    )


# ==========================================================
# MÓDULO
# ==========================================================

@portal_treinamento_bp.get(
    "/t/<token>/modulo/<int:modulo_id>"
)
def modulo(token, modulo_id):

    participacao = obter_participacao(
        token
    )

    if not participacao_valida(
        participacao
    ):

        return (
            render_template(
                "portal_treinamento/indisponivel.html"
            ),
            404,
        )

    treinamento = (
        participacao.treinamento
    )

    estado = calcular_progresso(treinamento.modulos, participacao.ultimo_modulo_id)
    modulos = estado.modulos
    modulo_atual = estado.modulo_por_id(modulo_id)

    if modulo_atual is None:
        return render_template("portal_treinamento/indisponivel.html"), 404

    if not estado.cursor_consistente:
        return render_template("portal_treinamento/indisponivel.html"), 409

    if not estado.pode_acessar(modulo_id):
        return redirect(url_for(
            "portal_treinamento.modulo",
            token=token,
            modulo_id=estado.proximo_modulo_pendente.id,
        ))

    indice_atual = next(i for i, item in enumerate(modulos) if item.id == modulo_id)

    modulo_anterior = (
        modulos[indice_atual - 1]
        if indice_atual > 0
        else None
    )

    proximo_modulo = (
        modulos[indice_atual + 1]
        if indice_atual
        < len(modulos) - 1
        else None
    )

    progresso = estado.percentual_concluido

    # ======================================================
    # IMPORTANTE
    # ======================================================
    #
    # GET não altera mais:
    #
    # participacao.ultimo_modulo_id
    # participacao.iniciado_em
    #
    # Isso será registrado somente quando o usuário
    # clicar em Próximo / Ir para o Quiz.
    # ======================================================

    tempo_total_ativo, tempos_modulos = tempos_da_participacao(participacao.id)

    return render_template(
        "portal_treinamento/modulo.html",
        tempo_total_ativo=tempo_total_ativo,
        tempo_modulo_ativo=tempos_modulos.get(modulo_atual.id, 0),
        treinamento=treinamento,
        modulo=modulo_atual,
        modulo_anterior=modulo_anterior,
        proximo_modulo=proximo_modulo,
        progresso=progresso,
        estado_progresso=estado,
        numero_modulo=indice_atual + 1,
        total_modulos=len(modulos),
        participacao=participacao,
    )


# ==========================================================
# AVANÇAR NO TREINAMENTO
# ==========================================================

@portal_treinamento_bp.post(
    "/t/<token>/modulo/<int:modulo_id>/avancar"
)
def avancar_modulo(token, modulo_id):
    try:
        # Validação e atualização serializadas por participação. populate_existing
        # substitui também um cursor já carregado antes da espera pelo bloqueio.
        participacao = db.session.execute(
            db.select(ParticipacaoTreinamento)
            .where(filtro_participacao(token))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

        if not participacao_valida(participacao):
            db.session.rollback()
            return render_template("portal_treinamento/indisponivel.html"), 404

        bloquear_sequencia(participacao.treinamento_id, db.session, compartilhado=True)
        estado = calcular_progresso(
            participacao.treinamento.modulos, participacao.ultimo_modulo_id,
        )
        if estado.modulo_por_id(modulo_id) is None:
            db.session.rollback()
            return render_template("portal_treinamento/indisponivel.html"), 404

        if not estado.cursor_consistente:
            db.session.rollback()
            return render_template("portal_treinamento/indisponivel.html"), 409

        avancar = estado.pode_avancar(modulo_id)
        if not avancar and not estado.ja_concluido(modulo_id):
            db.session.rollback()
            return render_template("portal_treinamento/indisponivel.html"), 403

        if avancar:
            if participacao.iniciado_em is None:
                participacao.iniciado_em = datetime.now(timezone.utc)
            participacao.ultimo_modulo_id = modulo_id
            estado = calcular_progresso(estado.modulos, modulo_id)

        proximo = estado.proximo_modulo_pendente
        if proximo is not None:
            destino = url_for(
                "portal_treinamento.modulo",
                token=token,
                modulo_id=proximo.id,
            )
        else:
            destino = url_for("portal_treinamento.quiz", token=token)

        if avancar:
            db.session.commit()
        else:
            # POST repetido/atrasado: não grava nada e não regride o cursor.
            db.session.rollback()
        return redirect(destino)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error("Falha ao registrar avanço de treinamento")
        return render_template("portal_treinamento/indisponivel.html"), 500


# ==========================================================
# CONCLUSÃO
# ==========================================================

@portal_treinamento_bp.get(
    "/t/<token>/conclusao"
)
def conclusao(token):
    participacao = obter_participacao(token)
    if not participacao_valida(participacao):
        return render_template("portal_treinamento/indisponivel.html"), 404

    estado = calcular_progresso(
        participacao.treinamento.modulos, participacao.ultimo_modulo_id,
    )
    if participacao.concluido_em is None:
        if not estado.cursor_consistente:
            return render_template("portal_treinamento/indisponivel.html"), 409
        if estado.proximo_modulo_pendente is not None:
            return redirect(url_for(
                "portal_treinamento.modulo", token=token,
                modulo_id=estado.proximo_modulo_pendente.id,
            ))
        return redirect(url_for("portal_treinamento.quiz", token=token))

    # Conclusão registrada é histórica: mudanças posteriores na estrutura não
    # apagam o direito de visualizá-la. A divergência não gera escrita em GET.
    if not estado.todos_modulos_concluidos:
        current_app.logger.warning(
            "Conclusão histórica com progressão divergente: participação %s",
            participacao.id,
        )
    return render_template(
        "portal_treinamento/conclusao.html",
        tem_resultado_quiz=db.session.execute(db.select(db.exists().where(
            ResultadoQuiz.participacao_id == participacao.id))).scalar_one(),
        treinamento=participacao.treinamento, participacao=participacao,
    )


# ==========================================================
# QUIZ
# ==========================================================

# UUIDs are transported only in POST bodies, never in navigation URLs.
from app.models.submissao_quiz import SubmissaoQuiz
from app.utils.submissoes_quiz import (
    preparar_submissao, corrigir_submissao, elegibilidade_quiz, ErroQuiz,
)

MENSAGENS_QUIZ = {
    "sem_quiz": "Este treinamento não possui avaliação. Use a conclusão explícita do treinamento.",
    "indisponivel": "Avaliação indisponível. Verifique seu acesso ao treinamento.",
    "ocupado": "A avaliação está ocupada. Tente enviar novamente a mesma avaliação.",
    "falha_banco": "Não foi possível confirmar o envio. Tente novamente a mesma avaliação.",
    "respostas_invalidas": "Responda todas as questões com uma opção válida antes de enviar.",
    "base_invalida": "Esta solicitação não corresponde à avaliação atual. Atualize a página.",
    "pendente_obsoleta": "Esta avaliação não pode mais ser enviada. Atualize para consultar sua situação.",
    "historica": "Seu histórico não possui classificação. Procure o responsável pelo treinamento.",
    "inconsistente": "Não foi possível liberar a avaliação. Procure o responsável pelo treinamento.",
    "aprovada": "Você já foi aprovado neste treinamento. Consulte seu resultado.",
    "numero_esgotado": "Não foi possível liberar outra avaliação. Procure o responsável pelo treinamento.",
}


def _contexto_quiz(token):
    p = obter_participacao(token)
    if not participacao_valida(p):
        return None
    t = p.treinamento
    resultados = db.session.execute(db.select(ResultadoQuiz).where(
        ResultadoQuiz.participacao_id == p.id,
    ).order_by(ResultadoQuiz.numero_tentativa, ResultadoQuiz.id)).scalars().all()
    questoes = db.session.execute(db.select(Questao).where(
        Questao.treinamento_id == t.id,
    ).order_by(Questao.ordem, Questao.id).options(selectinload(Questao.alternativas))).scalars().all()
    progresso = calcular_progresso(t.modulos, p.ultimo_modulo_id)
    elegibilidade = elegibilidade_quiz(p, t, p.colaborador, resultados, progresso,
                                      questoes, autorizada=True)
    pendente = db.session.execute(db.select(SubmissaoQuiz).where(
        SubmissaoQuiz.participacao_id == p.id, SubmissaoQuiz.estado == 'pendente',
    )).scalar_one_or_none()
    return dict(participacao=p, treinamento=t, resultados=resultados, questoes=questoes,
                progresso=progresso, elegibilidade=elegibilidade, pendente=pendente)


def _pagina_quiz(token, *, resultado_id=None, erro=None, status=200):
    ctx = _contexto_quiz(token)
    if ctx is None:
        return render_template("portal_treinamento/indisponivel.html"), 404
    if resultado_id is not None:
        resultado = next((r for r in ctx['resultados'] if r.id == resultado_id), None)
        if resultado is None:
            return render_template("portal_treinamento/indisponivel.html"), 404
    else:
        resultado = resultado_representativo(ctx['resultados'])
    if ctx['participacao'].concluido_em is None and resultado is not None and resultado_id is None:
        return render_template("portal_treinamento/indisponivel.html"), 409
    if ctx['participacao'].concluido_em is not None and resultado is None:
        return redirect(url_for('portal_treinamento.conclusao', token=token))
    ctx['erro_validacao'] = erro or (MENSAGENS_QUIZ.get(ctx['elegibilidade'].tipo)
        if ctx['elegibilidade'].tipo in ('inconsistente', 'historica') else None)
    ctx['resultado'] = resultado
    if resultado_id is not None or (resultado is not None and ctx['pendente'] is None):
        return render_template("portal_treinamento/resultado_quiz.html", **ctx), status
    if ctx['pendente'] is not None and ctx['elegibilidade'].permitido:
        return render_template("portal_treinamento/quiz.html",
                               submissao_id=ctx['pendente'].id, **ctx), status
    if resultado is not None:
        return render_template("portal_treinamento/resultado_quiz.html", **ctx), status
    if not ctx['progresso'].cursor_consistente:
        return render_template("portal_treinamento/indisponivel.html"), 409
    if not ctx['progresso'].todos_modulos_concluidos:
        proximo = ctx['progresso'].proximo_modulo_pendente
        return redirect(url_for("portal_treinamento.modulo", token=token, modulo_id=proximo.id)
                        if proximo else url_for("portal_treinamento.inicio", token=token))
    if not ctx['questoes']:
        return render_template("portal_treinamento/sem_quiz.html", **ctx), status
    return render_template("portal_treinamento/preparar_quiz.html", **ctx), status


def _erro_quiz(token, codigo, *, submissao_id=None, base=None):
    try:
        return _renderizar_erro_quiz(token, codigo, submissao_id=submissao_id, base=base)
    except SQLAlchemyError:
        # If recovery reads also fail, do not expose an unauthorised identity.
        db.session.rollback()
        return render_template("portal_treinamento/erro_quiz.html",
            mensagem="Não foi possível consultar a avaliação. Atualize para retomar quando o serviço estiver disponível."), 503


def _renderizar_erro_quiz(token, codigo, *, submissao_id=None, base=None):
    if codigo in ('indisponivel', 'nao_autorizada'):
        return render_template("portal_treinamento/indisponivel.html"), 404
    status = 400 if codigo == 'respostas_invalidas' else 503 if codigo in ('ocupado', 'falha_banco') else 409
    # Preserve the submitted identity on recoverable failures; never substitute a new pending UUID.
    ctx = _contexto_quiz(token)
    if ctx is None:
        return render_template("portal_treinamento/indisponivel.html"), 404
    mensagem = MENSAGENS_QUIZ.get(codigo, MENSAGENS_QUIZ["inconsistente"])
    if submissao_id is not None and codigo in ('respostas_invalidas', 'ocupado', 'falha_banco'):
        from uuid import UUID
        try:
            identity = UUID(submissao_id)
        except (ValueError, TypeError, AttributeError):
            return render_template("portal_treinamento/indisponivel.html"), 404
        sub = db.session.execute(db.select(SubmissaoQuiz).where(
            SubmissaoQuiz.id == identity, SubmissaoQuiz.participacao_id == ctx['participacao'].id,
        )).scalar_one_or_none()
        if sub is None:
            return render_template("portal_treinamento/indisponivel.html"), 404
        return render_template("portal_treinamento/quiz.html", submissao_id=sub.id,
                               erro_validacao=mensagem, **ctx), status
    return render_template("portal_treinamento/preparar_quiz.html", erro_validacao=mensagem,
                           repetir_preparacao=codigo in ('ocupado', 'falha_banco'),
                           base_solicitada=base, **ctx), status


@portal_treinamento_bp.post("/t/<token>/quiz/preparar")
def preparar_quiz(token):
    base = None
    try:
        valores = request.form.getlist('resultado_base_id')
        if len(valores) > 1 or (valores and (len(valores[0]) > 10 or not valores[0].isascii() or not valores[0].isdigit())):
            raise ErroQuiz('base_invalida')
        base = int(valores[0]) if valores else None
        if base is not None and not 1 <= base <= 2147483647:
            raise ErroQuiz('base_invalida')
        # Services own their transaction and all quiz writes.
        recibo = preparar_submissao(token, resultado_base_id=base)
        return redirect(url_for('portal_treinamento.resultado_quiz', token=token,
                                resultado_id=recibo.resultado_id) if recibo.estado == 'processada'
                        else url_for('portal_treinamento.quiz', token=token), code=303)
    except ErroQuiz as exc:
        return _erro_quiz(token, exc.codigo, base=base)
    except SQLAlchemyError:
        db.session.rollback()
        return render_template("portal_treinamento/indisponivel.html"), 503
    finally:
        db.session.rollback()


@portal_treinamento_bp.route("/t/<token>/quiz", methods=['GET', 'POST'])
def quiz(token):
    try:
        if request.method == 'GET':
            return _pagina_quiz(token)
        identidades = request.form.getlist('submissao_id')
        if len(identidades) != 1:
            ctx = _contexto_quiz(token)
            if ctx is None:
                return render_template("portal_treinamento/indisponivel.html"), 404
            if not ctx['questoes']:
                db.session.rollback()
                return _concluir_sem_quiz(token)
            if ctx['participacao'].concluido_em is None:
                if not ctx['progresso'].cursor_consistente:
                    return render_template("portal_treinamento/indisponivel.html"), 409
                if not ctx['progresso'].todos_modulos_concluidos:
                    return render_template("portal_treinamento/indisponivel.html"), 403
            return render_template("portal_treinamento/preparar_quiz.html", **ctx,
                erro_validacao="Este formulário precisa ser atualizado. Atualize ou retome a avaliação antes de enviar."), 409
        recibo = corrigir_submissao(token, identidades[0], request.form)
        return redirect(url_for('portal_treinamento.resultado_quiz', token=token,
                                resultado_id=recibo.resultado_id), code=303)
    except ErroQuiz as exc:
        return _erro_quiz(token, exc.codigo, submissao_id=request.form.get('submissao_id'))
    except SQLAlchemyError:
        db.session.rollback()
        return render_template("portal_treinamento/indisponivel.html"), 503
    finally:
        db.session.rollback()


@portal_treinamento_bp.get("/t/<token>/quiz/resultados/<int:resultado_id>")
def resultado_quiz(token, resultado_id):
    try:
        return _pagina_quiz(token, resultado_id=resultado_id)
    except SQLAlchemyError:
        return render_template("portal_treinamento/indisponivel.html"), 503
    finally:
        db.session.rollback()


def _concluir_sem_quiz(token):
    try:
        consulta = db.select(ParticipacaoTreinamento).where(
            filtro_participacao(token),
        )
        if request.method == "POST":
            # A mesma trava usada no avanço serializa conclusão e reenvios.
            consulta = consulta.with_for_update().execution_options(populate_existing=True)
        participacao = db.session.execute(consulta).scalar_one_or_none()
        if request.method == "POST" and participacao is not None:
            # Ordem participação -> treinamento, como progressão/heartbeat.
            # SHARE protege a estrutura e também conflita com UPDATE da nota.
            # KEY SHARE não protegeria alterações de campos não-chave.
            db.session.execute(
                db.select(Treinamento)
                .where(Treinamento.id == participacao.treinamento_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            ).scalar_one()
        if not participacao_valida(participacao):
            return render_template("portal_treinamento/indisponivel.html"), 404

        treinamento = participacao.treinamento
        estado = calcular_progresso(treinamento.modulos, participacao.ultimo_modulo_id)
        resultados_existentes = db.session.execute(
            db.select(ResultadoQuiz).where(
                ResultadoQuiz.participacao_id == participacao.id,
            ).order_by(ResultadoQuiz.numero_tentativa, ResultadoQuiz.id).execution_options(populate_existing=True)
        ).scalars().all()
        resultado_existente = resultado_representativo(resultados_existentes)

        # Reenvio de participação concluída nunca cria resultado ou muda datas.
        # Resultados históricos continuam consultáveis mesmo após edição dos módulos.
        if participacao.concluido_em is not None:
            if resultado_existente is not None:
                return redirect(url_for("portal_treinamento.resultado_quiz", token=token,
                                        resultado_id=resultado_existente.id), code=303)
            return redirect(url_for("portal_treinamento.conclusao", token=token))

        if not estado.cursor_consistente:
            return render_template("portal_treinamento/indisponivel.html"), 409
        if not estado.todos_modulos_concluidos:
            if request.method == "POST":
                return render_template("portal_treinamento/indisponivel.html"), 403
            if estado.proximo_modulo_pendente is not None:
                return redirect(url_for(
                    "portal_treinamento.modulo", token=token,
                    modulo_id=estado.proximo_modulo_pendente.id,
                ))
            return redirect(url_for("portal_treinamento.inicio", token=token))

        # Não reparar automaticamente um resultado legado sem conclusão registrada.
        if resultado_existente is not None:
            return render_template("portal_treinamento/indisponivel.html"), 409

        questoes = db.session.execute(
            db.select(Questao).where(Questao.treinamento_id == treinamento.id)
            .order_by(Questao.ordem, Questao.id)
            .options(selectinload(Questao.alternativas))
        ).scalars().all()
        if not questoes:
            if request.method == "GET":
                return render_template(
                    "portal_treinamento/sem_quiz.html",
                    treinamento=treinamento, participacao=participacao,
                )
            avaliacao = avaliar_aprovacao(0, 0, conclusao_explicita=True)
            instante = datetime.now(timezone.utc)
            participacao.concluido_em = instante
            if avaliacao.aprovado and participacao.aprovado_em is None:
                participacao.aprovado_em = instante
            db.session.commit()
            return redirect(url_for("portal_treinamento.conclusao", token=token))

        return render_template("portal_treinamento/indisponivel.html"), 409
    except SQLAlchemyError:
        return render_template("portal_treinamento/indisponivel.html"), 503
    finally:
        db.session.rollback()

@portal_treinamento_bp.route("/t/<token>/feedback", methods=["GET", "POST"])
def feedback(token):
    try:
        participacao = obter_participacao(token)
        if not participacao_valida(participacao):
            return render_template("portal_treinamento/indisponivel.html"), 404
        if participacao.concluido_em is None:
            return render_template("portal_treinamento/indisponivel.html"), 403

        erro = None
        if request.method == "POST":
            try:
                registrar_feedback(participacao.id, request.form)
            except FeedbackInvalido as exc:
                erro = str(exc)
                for campo, rotulo in (
                    ("avaliacao_geral", "avaliação geral"),
                    ("avaliacao_conteudo", "conteúdo"),
                    ("avaliacao_clareza", "clareza"),
                    ("avaliacao_utilidade", "utilidade"),
                ):
                    erro = erro.replace(campo, rotulo)
            except ParticipacaoNaoConcluida:
                return render_template("portal_treinamento/indisponivel.html"), 403
            except ParticipacaoNaoEncontrada:
                return render_template("portal_treinamento/indisponivel.html"), 404
            else:
                return redirect(url_for("portal_treinamento.feedback", token=token), code=303)

        existente = db.session.execute(db.select(FeedbackTreinamento).where(
            FeedbackTreinamento.participacao_id == participacao.id,
        )).scalar_one_or_none()
        voltar_url = url_for(
            "portal_treinamento.quiz" if db.session.execute(
                db.select(db.exists().where(ResultadoQuiz.participacao_id == participacao.id))
            ).scalar_one() else "portal_treinamento.conclusao",
            token=token,
        )
        return render_template(
            "portal_treinamento/feedback.html", treinamento=participacao.treinamento,
            participacao=participacao, feedback=existente, erro=erro,
            respostas=request.form if erro else {}, voltar_url=voltar_url,
        ), 400 if erro else 200
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error("Falha ao processar avaliação de treinamento")
        return render_template("portal_treinamento/indisponivel.html"), 500
