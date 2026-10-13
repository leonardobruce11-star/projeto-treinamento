from app.utils.resultados_quiz import resultado_representativo, resultados_carregados
import secrets
from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from flask_login import (
    current_user,
    login_required,
)

from werkzeug.utils import secure_filename

from app.utils.painel_colaboradores import contexto_status_capacitacao, status_participacao
from app.extensions import db
from sqlalchemy.orm import selectinload
from app.models.colaborador import Colaborador
from app.utils.tempos_treinamento import totais_por_participacao

from app.integracoes.gophish.content import validar_conteudo, ConteudoInseguro

from app.relatorios.campanha_pdf import (
    gerar_relatorio_campanha_pdf,
)

from app.models.auditoria import Auditoria
from app.models.campanha import Campanha
from app.models.cenario import Cenario
from app.models.grupo import Grupo
from app.models.grupo_participante import GrupoParticipante
from app.models.landing_page import LandingPage
from app.models.participacao_treinamento import (
    ParticipacaoTreinamento,
)
from app.models.template_email import TemplateEmail
from app.models.treinamento import Treinamento


# ==========================================================
# BLUEPRINT
# ==========================================================

campanhas_bp = Blueprint(
    "campanhas",
    __name__,
    url_prefix="/campanhas",
)

campanhas_bp.context_processor(contexto_status_capacitacao)



# ==========================================================
# AUDITORIA
# ==========================================================

def registrar_auditoria(
    acao,
    campanha,
    detalhes=None,
):

    registro = Auditoria(
        usuario_id=current_user.id,
        acao=acao,
        objeto="campanha",
        objeto_id=campanha.id,
        detalhes=detalhes,
    )

    db.session.add(
        registro
    )


# ==========================================================
# CONTROLE DE ACESSO
# ==========================================================

def usuario_eh_administrador():

    return (
        current_user.is_authenticated
        and current_user.ativo
        and current_user.perfil == "administrador"
    )


def usuario_pode_editar_campanha(campanha):

    if not current_user.is_authenticated:
        return False

    if not current_user.ativo:
        return False

    if current_user.perfil == "administrador":
        return True

    if current_user.perfil == "operador":

        return (
            campanha.criado_por_id
            == current_user.id
        )

    return False


# ==========================================================
# CARREGAR RECURSOS
# ==========================================================

def carregar_recursos():

    grupos = (
        Grupo.query
        .filter_by(ativo=True)
        .order_by(Grupo.nome)
        .all()
    )

    cenarios = (
        Cenario.query
        .filter_by(ativo=True)
        .order_by(Cenario.nome)
        .all()
    )

    templates = (
        TemplateEmail.query
        .filter_by(ativo=True)
        .order_by(TemplateEmail.nome)
        .all()
    )

    landing_pages = (
        LandingPage.query
        .filter_by(ativo=True)
        .order_by(LandingPage.nome)
        .all()
    )

    treinamentos = (
        Treinamento.query
        .filter_by(ativo=True)
        .order_by(Treinamento.titulo)
        .all()
    )

    return {
        "tipos_campanha": Campanha.TIPOS,
        "grupos": grupos,
        "cenarios": cenarios,
        "templates": templates,
        "landing_pages": landing_pages,
        "treinamentos": treinamentos,
    }


# ==========================================================
# CALCULAR MÉTRICAS DO PILOTO
# ==========================================================

def carregar_participacoes_campanha(campanha_id):
    return ParticipacaoTreinamento.query.options(
        selectinload(ParticipacaoTreinamento.colaborador).selectinload(Colaborador.departamento),
        selectinload(ParticipacaoTreinamento.resultados_quiz),
        selectinload(ParticipacaoTreinamento.ultimo_modulo),
    ).filter_by(campanha_id=campanha_id).all()


def calcular_metricas_piloto(campanha):

    participacoes = carregar_participacoes_campanha(campanha.id)

    total_participantes = len(
        participacoes
    )

    total_iniciados = sum(
        1
        for participacao in participacoes
        if participacao.iniciado_em is not None
    )

    total_nao_iniciados = sum(
        1
        for participacao in participacoes
        if status_participacao(participacao, resultado_representativo(resultados_carregados(participacao))) == "nao_iniciado"
    )

    total_em_andamento = sum(
        status_participacao(p, resultados_carregados(p)) == "em_andamento"
        for p in participacoes
    )

    total_concluidos = sum(
        1
        for participacao in participacoes
        if participacao.concluido_em is not None
    )

    resultados = []

    for participacao in participacoes:

        resultado = resultado_representativo(resultados_carregados(participacao))

        if resultado:
            resultados.append(
                resultado
            )

    total_quizzes = len(
        resultados
    )

    if total_participantes > 0:

        taxa_inicio = round(
            (
                total_iniciados
                / total_participantes
            ) * 100,
            2,
        )

        taxa_conclusao = round(
            (
                total_concluidos
                / total_participantes
            ) * 100,
            2,
        )

    else:

        taxa_inicio = 0
        taxa_conclusao = 0

    if resultados:

        media_quiz = round(
            (
                sum(
                    resultado.percentual
                    for resultado in resultados
                )
                / len(resultados)
            ),
            2,
        )

    else:

        media_quiz = 0

    indicadores = {
        "participantes": total_participantes,
        "iniciados": total_iniciados,
        "nao_iniciados": total_nao_iniciados,
        "em_andamento": total_em_andamento,
        "concluidos": total_concluidos,
        "quizzes": total_quizzes,
        "taxa_inicio": taxa_inicio,
        "taxa_conclusao": taxa_conclusao,
        "media_quiz": media_quiz,
    }

    return (
        participacoes,
        resultados,
        indicadores,
    )


# ==========================================================
# VALIDAR PRONTIDÃO DA CAMPANHA
# ==========================================================

def validar_recursos_locais(campanha):
    erros = []
    recursos = [('Grupo', campanha.grupo), ('Treinamento', campanha.treinamento)]
    if campanha.eh_simulacao_phishing:
        recursos += [('Cenário', campanha.cenario), ('Template', campanha.template_email),
                     ('Landing Page', campanha.landing_page)]
    for nome, recurso in recursos:
        if not recurso or not recurso.ativo:
            erros.append(f'{nome} deve estar configurado e ativo.')
    if erros:
        return erros
    if campanha.eh_simulacao_phishing:
        if campanha.template_email.cenario_id != campanha.cenario_id:
            erros.append('O template selecionado pertence a outro cenário.')
        if not any(v.colaborador and v.colaborador.ativo for v in campanha.grupo.participantes):
            erros.append('O grupo precisa de participante ativo para a simulação.')
        for recurso in (campanha.template_email, campanha.landing_page):
            try:
                validar_conteudo(recurso.conteudo_html)
            except ConteudoInseguro:
                erros.append('Conteúdo de simulação não permitido; revise os recursos locais.')
    return erros


def validar_prontidao(campanha):

    erros = []
    avisos = []

    # ======================================================
    # RECURSOS LOCAIS
    # ======================================================

    if not campanha.grupo:
        erros.append(
            "Grupo não configurado."
        )

    if campanha.eh_simulacao_phishing and not campanha.cenario:
        erros.append(
            "Cenário não configurado."
        )

    if campanha.eh_simulacao_phishing and not campanha.template_email:
        erros.append(
            "Template de e-mail não configurado."
        )

    if campanha.eh_simulacao_phishing and not campanha.landing_page:
        erros.append(
            "Landing Page não configurada."
        )

    if not campanha.treinamento:
        erros.append(
            "Treinamento não configurado."
        )

    if erros:
        return erros, avisos

    # ======================================================
    # CENÁRIO / TEMPLATE
    # ======================================================

    if (
        campanha.eh_simulacao_phishing
        and campanha.template_email.cenario_id
        != campanha.cenario_id
    ):

        erros.append(
            (
                "O template selecionado pertence "
                "a outro cenário."
            )
        )

    # ======================================================
    # PARTICIPANTES
    # ======================================================

    total_participantes = (
        GrupoParticipante.query
        .filter_by(
            grupo_id=campanha.grupo_id
        )
        .count()
    )

    if total_participantes == 0:

        erros.append(
            "O grupo não possui participantes."
        )

    # ======================================================
    # TREINAMENTO
    # ======================================================

    total_modulos = len(
        campanha.treinamento.modulos
    )

    total_questoes = len(
        campanha.treinamento.questoes
    )

    if total_modulos == 0:

        erros.append(
            "O treinamento não possui módulos."
        )

    if total_questoes == 0:

        erros.append(
            "O treinamento não possui questões."
        )

    if not campanha.eh_simulacao_phishing:
        if total_participantes == 1:
            avisos.append("O grupo possui apenas um participante.")
        return erros, avisos

    # A prontidão nesta etapa é exclusivamente local.
    erros.extend(validar_recursos_locais(campanha))
    if total_participantes == 1:
        avisos.append('O grupo possui apenas um participante.')
    return erros, avisos


# ==========================================================
# LISTAR
# ==========================================================

@campanhas_bp.get("/")
@login_required
def listar():

    campanhas = (
        Campanha.query
        .order_by(
            Campanha.criado_em.desc()
        )
        .all()
    )

    return render_template(
        "campanhas/listar.html",
        campanhas=campanhas,
    )


# ==========================================================
# NOVA CAMPANHA
# ==========================================================

@campanhas_bp.route(
    "/nova",
    methods=[
        "GET",
        "POST",
    ],
)
@login_required
def nova():

    if current_user.perfil not in (
        "administrador",
        "operador",
    ):

        flash(
            (
                "Seu usuário não possui permissão "
                "para criar campanhas."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    recursos = carregar_recursos()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            "",
        ).strip()

        descricao = request.form.get(
            "descricao",
            "",
        ).strip()

        tipo = request.form.get("tipo", "").strip()
        if tipo not in Campanha.TIPOS:
            flash("Selecione um tipo de campanha válido.", "danger")
            return redirect(url_for("campanhas.nova"))

        if not nome:

            flash(
                "Informe o nome da campanha.",
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.nova"
                )
            )

        existente = (
            Campanha.query
            .filter_by(
                nome=nome
            )
            .first()
        )

        if existente:

            flash(
                (
                    "Já existe uma campanha "
                    "com esse nome."
                ),
                "warning",
            )

            return redirect(
                url_for(
                    "campanhas.nova"
                )
            )

        campanha = Campanha(
            nome=nome,
            descricao=descricao or None,
            tipo=tipo,
            status="rascunho",
            criado_por_id=current_user.id,
        )

        db.session.add(
            campanha
        )

        db.session.flush()

        registrar_auditoria(
            "campanha_criada",
            campanha,
            f"Campanha criada como rascunho. Tipo: {tipo}.",
        )

        db.session.commit()

        flash(
            "Campanha criada como rascunho.",
            "success",
        )

        return redirect(
            url_for(
                "campanhas.editar",
                campanha_id=campanha.id,
            )
        )

    return render_template(
        "campanhas/nova.html",
        **recursos,
    )


# ==========================================================
# EDITAR
# ==========================================================

@campanhas_bp.route(
    "/<int:campanha_id>/editar",
    methods=[
        "GET",
        "POST",
    ],
)
@login_required
def editar(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_pode_editar_campanha(
        campanha
    ):

        flash(
            (
                "Você não possui permissão para "
                "editar esta campanha."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    status_editaveis = (
        "rascunho",
        "reprovada",
        "ajustes_necessarios",
    )

    if campanha.status not in status_editaveis:

        flash(
            (
                "A campanha não pode ser editada "
                "no estado atual."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    recursos = carregar_recursos()

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            "",
        ).strip()

        descricao = request.form.get(
            "descricao",
            "",
        ).strip()

        tipo = request.form.get("tipo", "").strip()
        if tipo not in Campanha.TIPOS:
            flash("Selecione um tipo de campanha válido.", "danger")
            return redirect(url_for("campanhas.editar", campanha_id=campanha.id))

        if not nome:

            flash(
                "Informe o nome da campanha.",
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.editar",
                    campanha_id=campanha.id,
                )
            )

        existente = (
            Campanha.query
            .filter(
                Campanha.nome == nome,
                Campanha.id != campanha.id,
            )
            .first()
        )

        if existente:

            flash(
                (
                    "Já existe outra campanha "
                    "com esse nome."
                ),
                "warning",
            )

            return redirect(
                url_for(
                    "campanhas.editar",
                    campanha_id=campanha.id,
                )
            )

        # Valide todos os vínculos antes de alterar o registro.
        modelos = {
            "grupo_id": (Grupo, "Grupo"),
            "treinamento_id": (Treinamento, "Treinamento"),
        }
        if tipo == "simulacao_phishing":
            modelos.update({
                "cenario_id": (Cenario, "Cenário"),
                "template_email_id": (TemplateEmail, "Template de e-mail"),
                "landing_page_id": (LandingPage, "Landing Page"),
            })

        vinculos = {}
        selecionados = {}
        for campo, (modelo, rotulo) in modelos.items():
            valor = request.form.get(campo, "").strip()
            try:
                recurso_id = int(valor)
            except ValueError:
                recurso_id = None
            recurso = db.session.get(modelo, recurso_id) if recurso_id else None
            if not recurso or not recurso.ativo:
                flash(f"Selecione um recurso ativo e válido para {rotulo}.", "danger")
                return redirect(url_for("campanhas.editar", campanha_id=campanha.id))
            vinculos[campo] = recurso.id
            selecionados[campo] = recurso

        if tipo == "simulacao_phishing":
            if selecionados["template_email_id"].cenario_id != vinculos["cenario_id"]:
                flash("O template selecionado pertence a outro cenário.", "danger")
                return redirect(url_for("campanhas.editar", campanha_id=campanha.id))

        tipo_anterior = campanha.tipo_efetivo
        campanha.nome = nome
        campanha.descricao = descricao or None
        campanha.tipo = tipo
        for campo, recurso_id in vinculos.items():
            setattr(campanha, campo, recurso_id)

        if tipo != "simulacao_phishing":
            campanha.cenario_id = None
            campanha.template_email_id = None
            campanha.landing_page_id = None

        if campanha.status in (
            "reprovada",
            "ajustes_necessarios",
        ):

            campanha.status = "rascunho"

            campanha.motivo_reprovacao = None

            campanha.decisao_piloto = None
            campanha.decisao_piloto_por_id = None
            campanha.decisao_piloto_em = None
            campanha.observacao_decisao_piloto = None

        registrar_auditoria(
            "campanha_editada",
            campanha,
            (f"Configuração da campanha atualizada. "
             f"Tipo anterior: {tipo_anterior}. Tipo atual: {tipo}."),
        )

        db.session.commit()

        flash(
            "Campanha atualizada.",
            "success",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    return render_template(
        "campanhas/editar.html",
        campanha=campanha,
        **recursos,
    )


# ==========================================================
# DETALHE
# ==========================================================

@campanhas_bp.get(
    "/<int:campanha_id>"
)
@login_required
def detalhe(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )


          # ==========================================================
    # MÉTRICAS DA CAMPANHA
    # ==========================================================

    participacoes = carregar_participacoes_campanha(campanha.id)

    total_participantes = len(
        participacoes
    )

    total_nao_iniciados = sum(
        1
        for participacao in participacoes
        if status_participacao(participacao, resultado_representativo(resultados_carregados(participacao))) == "nao_iniciado"
    )

    total_em_andamento = sum(
        status_participacao(p, resultados_carregados(p)) == "em_andamento"
        for p in participacoes
    )

    total_concluidos = sum(
        1
        for participacao in participacoes
        if participacao.concluido_em is not None
    )

    if total_participantes > 0:

        taxa_conclusao_campanha = round(
            (
                total_concluidos
                / total_participantes
            )
            * 100,
            1,
        )

    else:

        taxa_conclusao_campanha = 0


    resultados_quiz = [
        resultado_representativo(resultados_carregados(participacao))
        for participacao in participacoes
        if resultado_representativo(resultados_carregados(participacao))
    ]

    total_quizzes_campanha = len(
        resultados_quiz
    )

    if total_quizzes_campanha > 0:

        media_quiz_campanha = round(
            sum(
                resultado.percentual
                for resultado in resultados_quiz
            )
            / total_quizzes_campanha,
            1,
        )

    else:

        media_quiz_campanha = 0


    return render_template(
        "campanhas/detalhe.html",
        campanha=campanha,
        participacoes=participacoes,
        tempos_ativos=totais_por_participacao(p.id for p in participacoes),

        total_participantes=total_participantes,
        total_nao_iniciados=total_nao_iniciados,
        total_em_andamento=total_em_andamento,
        total_concluidos=total_concluidos,

        taxa_conclusao_campanha=taxa_conclusao_campanha,

        total_quizzes_campanha=total_quizzes_campanha,
        media_quiz_campanha=media_quiz_campanha,
    )

# ==========================================================
# SOLICITAR APROVAÇÃO
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/solicitar-aprovacao"
)
@login_required
def solicitar_aprovacao(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_pode_editar_campanha(
        campanha
    ):

        flash(
            (
                "Você não possui permissão para "
                "enviar esta campanha para aprovação."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.status not in (
        "rascunho",
        "reprovada",
    ):

        flash(
            (
                "Essa campanha não pode ser "
                "enviada para aprovação."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    campos_faltantes = []

    if not campanha.grupo:
        campos_faltantes.append(
            "Grupo"
        )

    if campanha.eh_simulacao_phishing and not campanha.cenario:
        campos_faltantes.append(
            "Cenário"
        )

    if campanha.eh_simulacao_phishing and not campanha.template_email:
        campos_faltantes.append(
            "Template"
        )

    if campanha.eh_simulacao_phishing and not campanha.landing_page:
        campos_faltantes.append(
            "Landing Page"
        )

    if not campanha.treinamento:
        campos_faltantes.append(
            "Treinamento"
        )

    if campos_faltantes:

        flash(
            (
                "Complete a campanha antes de solicitar "
                "aprovação. Faltando: "
                + ", ".join(
                    campos_faltantes
                )
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.editar",
                campanha_id=campanha.id,
            )
        )

    erros = validar_recursos_locais(campanha)
    if erros:
        for erro in erros:
            flash(erro, 'danger')
        return redirect(url_for('campanhas.detalhe', campanha_id=campanha.id))

    campanha.status = (
        "aguardando_aprovacao"
    )

    registrar_auditoria(
        "campanha_enviada_aprovacao",
        campanha,
        "Campanha enviada para aprovação humana.",
    )

    db.session.commit()

    flash(
        "Campanha enviada para aprovação.",
        "success",
    )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# APROVAR CAMPANHA
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/aprovar"
)
@login_required
def aprovar(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "aprovar campanhas."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if (
        campanha.criado_por_id
        == current_user.id
    ):

        registrar_auditoria(
            "autoaprovacao_bloqueada",
            campanha,
            (
                "Tentativa de aprovação pelo "
                "próprio criador da campanha."
            ),
        )

        db.session.commit()

        flash(
            (
                "O criador da campanha não pode "
                "aprovar a própria campanha."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.tipo == 'simulacao_phishing':
        from app.simulacoes.snapshots import aprovar_simulacao, SimulacaoInvalida
        uid = current_user.id
        db.session.rollback()
        try:
            resultado = aprovar_simulacao(campanha_id, uid)
            flash('Aprovação local registrada com revisão imutável.' if resultado.criada else 'Aprovação já registrada.', 'success')
        except SimulacaoInvalida as erro:
            flash('Aprovação bloqueada: ' + erro.codigo + '.', 'warning')
        db.session.expire_all()
        return redirect(url_for('campanhas.detalhe', campanha_id=campanha_id))

    if campanha.status != "aguardando_aprovacao":

        flash(
            (
                "A campanha não está "
                "aguardando aprovação."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    # Revalidar recursos locais; nenhuma consulta/sincronização externa.
    erros = validar_recursos_locais(campanha)
    if erros:
        for erro in erros:
            flash(erro, 'danger')
        return redirect(url_for('campanhas.detalhe', campanha_id=campanha.id))

    campanha.status = "aprovada"

    campanha.aprovado_por_id = (
        current_user.id
    )

    campanha.aprovado_em = (
        datetime.now(
            timezone.utc
        )
    )

    campanha.motivo_reprovacao = None

    registrar_auditoria(
        "campanha_aprovada",
        campanha,
        "Campanha aprovada manualmente.",
    )

    db.session.commit()

    flash(
        (
            "Campanha aprovada. "
            "Nenhum envio foi realizado."
        ),
        "success",
    )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# REPROVAR CAMPANHA
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/reprovar"
)
@login_required
def reprovar(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "reprovar campanhas."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if (
        campanha.criado_por_id
        == current_user.id
    ):

        registrar_auditoria(
            "autodecisao_bloqueada",
            campanha,
            (
                "Tentativa de reprovação pelo "
                "próprio criador da campanha."
            ),
        )

        db.session.commit()

        flash(
            (
                "O criador da campanha não pode "
                "aprovar ou reprovar a própria campanha."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.status != "aguardando_aprovacao":

        flash(
            (
                "A campanha não está "
                "aguardando aprovação."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    motivo = request.form.get(
        "motivo",
        "",
    ).strip()

    if not motivo:

        flash(
            "Informe o motivo da reprovação.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    campanha.status = "reprovada"

    campanha.aprovado_por_id = None
    campanha.aprovado_em = None

    campanha.motivo_reprovacao = motivo

    registrar_auditoria(
        "campanha_reprovada",
        campanha,
        motivo,
    )

    db.session.commit()

    flash(
        "Campanha reprovada.",
        "warning",
    )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# PREPARAR CAMPANHA
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/preparar"
)
@login_required
def preparar(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "realizar a preparação técnica."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.status != "aprovada":

        flash(
            (
                "Somente campanhas aprovadas "
                "podem ser preparadas."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.eh_simulacao_phishing:
        if campanha.tipo == 'simulacao_phishing':
            return redirect(url_for('simulacoes.detalhe', campanha_id=campanha.id))
        flash('Tipo explícito simulacao_phishing e nova aprovação são necessários.', 'warning')
        return redirect(url_for('campanhas.detalhe', campanha_id=campanha.id))

    erros, avisos = validar_prontidao(
        campanha
    )

    if erros:

        for erro in erros:

            flash(
                erro,
                "danger",
            )

        registrar_auditoria(
            "preparacao_bloqueada",
            campanha,
            " | ".join(
                erros
            ),
        )

        db.session.commit()

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    campanha.status = "preparada"

    campanha.preparada_por_id = (
        current_user.id
    )

    campanha.preparada_em = (
        datetime.now(
            timezone.utc
        )
    )

    detalhes = (
        "Campanha validada tecnicamente. "
        "Nenhum envio realizado."
    )

    if avisos:

        detalhes += (
            " Avisos: "
            + " | ".join(
                avisos
            )
        )

    registrar_auditoria(
        "campanha_preparada",
        campanha,
        detalhes,
    )

    db.session.commit()

    flash(
        (
            "Campanha preparada com sucesso. "
            "Nenhum e-mail foi enviado."
        ),
        "success",
    )

    for aviso in avisos:

        flash(
            aviso,
            "warning",
        )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# CONFIGURAR PILOTO CONTROLADO
# ==========================================================

@campanhas_bp.route(
    "/<int:campanha_id>/piloto",
    methods=[
        "GET",
        "POST",
    ],
)
@login_required
def piloto(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "configurar o piloto."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    participacoes_existentes = (
        ParticipacaoTreinamento.query
        .filter_by(
            campanha_id=campanha.id
        )
        .all()
    )

    if campanha.status != "preparada":

        flash(
            (
                "O piloto desta campanha já foi "
                "configurado ou a campanha ainda "
                "não está pronta para esta etapa."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if participacoes_existentes:

        flash(
            (
                "Esta campanha já possui participantes "
                "associados ao piloto."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    participantes_grupo = (
        GrupoParticipante.query
        .filter_by(
            grupo_id=campanha.grupo_id
        )
        .all()
    )

    colaboradores = [
        participante.colaborador
        for participante in participantes_grupo
        if (
            participante.colaborador
            and participante.colaborador.ativo
           )
    ]

    if request.method == "POST":

        colaboradores_ids = (
            request.form.getlist(
                "colaboradores"
            )
        )

        if not colaboradores_ids:

            flash(
                (
                    "Selecione pelo menos um "
                    "participante para o piloto."
                ),
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.piloto",
                    campanha_id=campanha.id,
                )
            )

        if len(colaboradores_ids) > 3:

            flash(
                (
                    "O piloto controlado permite "
                    "no máximo 3 participantes."
                ),
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.piloto",
                    campanha_id=campanha.id,
                )
            )

        try:

            colaboradores_ids = [
                int(colaborador_id)
                for colaborador_id
                in colaboradores_ids
            ]

        except ValueError:

            flash(
                "Participante inválido.",
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.piloto",
                    campanha_id=campanha.id,
                )
            )

        if (
            len(colaboradores_ids)
            != len(
                set(colaboradores_ids)
            )
        ):

            flash(
                (
                    "Existem participantes "
                    "duplicados na seleção."
                ),
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.piloto",
                    campanha_id=campanha.id,
                )
            )

        ids_permitidos = {
            colaborador.id
            for colaborador in colaboradores
        }

        ids_recebidos = set(
            colaboradores_ids
        )

        if not ids_recebidos.issubset(
            ids_permitidos
        ):

            flash(
                (
                    "Um ou mais participantes "
                    "não pertencem ao grupo "
                    "desta campanha."
                ),
                "danger",
            )

            return redirect(
                url_for(
                    "campanhas.piloto",
                    campanha_id=campanha.id,
                )
            )

        total_existentes = (
            ParticipacaoTreinamento.query
            .filter(
                ParticipacaoTreinamento.campanha_id
                == campanha.id
            )
            .count()
        )

        if total_existentes > 0:

            flash(
                (
                    "O piloto já possui "
                    "participantes registrados."
                ),
                "warning",
            )

            return redirect(
                url_for(
                    "campanhas.detalhe",
                    campanha_id=campanha.id,
                )
            )

        for colaborador_id in colaboradores_ids:

            token = secrets.token_urlsafe(
                32
            )

            participacao = (
                ParticipacaoTreinamento(
                    token=token,
                    colaborador_id=colaborador_id,
                    treinamento_id=campanha.treinamento_id,
                    campanha_id=campanha.id,
                )
            )

            db.session.add(
                participacao
            )

        campanha.status = "piloto_pronto"

        campanha.piloto_preparado_por_id = (
            current_user.id
        )

        campanha.piloto_preparado_em = (
            datetime.now(
                timezone.utc
            )
        )

        registrar_auditoria(
            "piloto_preparado",
            campanha,
            (
                "Piloto controlado configurado com "
                f"{len(colaboradores_ids)} participante(s). "
                "Nenhum e-mail foi enviado."
            ),
        )

        db.session.commit()

        flash(
            (
                "Piloto controlado preparado "
                "com sucesso. Nenhum e-mail "
                "foi enviado."
            ),
            "success",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    return render_template(
        "campanhas/piloto.html",
        campanha=campanha,
        colaboradores=colaboradores,
    )


# ==========================================================
# VALIDAR PILOTO
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/validar-piloto"
)
@login_required
def validar_piloto(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "validar o piloto."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.status != "piloto_pronto":

        flash(
            (
                "A campanha não possui "
                "um piloto pendente."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    participacoes = (
        ParticipacaoTreinamento.query
        .options(selectinload(ParticipacaoTreinamento.resultados_quiz))
        .filter_by(
            campanha_id=campanha.id
        )
        .all()
    )

    if not participacoes:

        flash(
            (
                "Nenhuma participação "
                "de piloto encontrada."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if len(participacoes) > 3:

        registrar_auditoria(
            "piloto_inconsistente",
            campanha,
            (
                f"Foram encontradas "
                f"{len(participacoes)} participações "
                "vinculadas ao piloto."
            ),
        )

        db.session.commit()

        flash(
            (
                "Inconsistência detectada: "
                "o piloto possui mais de "
                "3 participantes."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    pendentes = []

    for participacao in participacoes:

        resultado = resultado_representativo(resultados_carregados(participacao))

        if (
            participacao.iniciado_em is None
            or participacao.concluido_em is None
            or resultado is None
        ):

            pendentes.append(
                participacao
            )

    if pendentes:

        flash(
            (
                "O piloto ainda possui "
                f"{len(pendentes)} participação(ões) "
                "pendente(s)."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    campanha.status = "piloto_validado"

    campanha.piloto_validado_por_id = (
        current_user.id
    )

    campanha.piloto_validado_em = (
        datetime.now(
            timezone.utc
        )
    )

    registrar_auditoria(
        "piloto_validado",
        campanha,
        (
            "Piloto controlado concluído "
            "e validado com sucesso."
        ),
    )

    db.session.commit()

    flash(
        "Piloto validado com sucesso.",
        "success",
    )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# ANÁLISE DO PILOTO
# ==========================================================

@campanhas_bp.get(
    "/<int:campanha_id>/analise-piloto"
)
@login_required
def analise_piloto(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if campanha.status not in (
        "piloto_validado",
        "piloto_aprovado",
        "ajustes_necessarios",
    ):

        flash(
            (
                "O piloto precisa estar validado "
                "antes da análise."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    (
        participacoes,
        resultados,
        metricas,
    ) = calcular_metricas_piloto(
        campanha
    )

    return render_template(
        "campanhas/analise_piloto.html",
        campanha=campanha,
        metricas=metricas,
    )


# ==========================================================
# DECISÃO DO PILOTO
# ==========================================================

@campanhas_bp.post(
    "/<int:campanha_id>/decisao-piloto"
)
@login_required
def decisao_piloto(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    if not usuario_eh_administrador():

        flash(
            (
                "Somente administradores podem "
                "registrar a decisão final do piloto."
            ),
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.detalhe",
                campanha_id=campanha.id,
            )
        )

    if campanha.status != "piloto_validado":

        flash(
            (
                "A decisão somente pode ser registrada "
                "para um piloto validado."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "campanhas.analise_piloto",
                campanha_id=campanha.id,
            )
        )

    decisao = request.form.get(
        "decisao",
        "",
    ).strip()

    observacao = request.form.get(
        "observacao",
        "",
    ).strip()

    decisoes_validas = (
        "aprovado_continuidade",
        "ajustes_necessarios",
    )

    if decisao not in decisoes_validas:

        flash(
            "Selecione uma decisão válida.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.analise_piloto",
                campanha_id=campanha.id,
            )
        )

    if not observacao:

        flash(
            "Informe a justificativa da decisão.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.analise_piloto",
                campanha_id=campanha.id,
            )
        )

    campanha.decisao_piloto = decisao

    campanha.decisao_piloto_por_id = (
        current_user.id
    )

    campanha.decisao_piloto_em = (
        datetime.now(
            timezone.utc
        )
    )

    campanha.observacao_decisao_piloto = (
        observacao
    )

    if decisao == "aprovado_continuidade":

        campanha.status = "piloto_aprovado"

        acao_auditoria = (
            "piloto_aprovado_continuidade"
        )

        mensagem = (
            "Piloto aprovado para continuidade."
        )

    else:

        campanha.status = (
            "ajustes_necessarios"
        )

        acao_auditoria = (
            "piloto_requer_ajustes"
        )

        mensagem = (
            "Piloto marcado como necessitando ajustes."
        )

    registrar_auditoria(
        acao_auditoria,
        campanha,
        observacao,
    )

    db.session.commit()

    flash(
        mensagem,
        "success",
    )

    return redirect(
        url_for(
            "campanhas.detalhe",
            campanha_id=campanha.id,
        )
    )


# ==========================================================
# RELATÓRIO CONSOLIDADO
# ==========================================================

@campanhas_bp.get(
    "/<int:campanha_id>/relatorio"
)
@login_required
def relatorio(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    (
        participacoes,
        resultados,
        indicadores,
    ) = calcular_metricas_piloto(
        campanha
    )

    return render_template(
        "campanhas/relatorio.html",
        campanha=campanha,
        participacoes=participacoes,
        tempos_ativos=totais_por_participacao(p.id for p in participacoes),
        indicadores=indicadores,
    )


# ==========================================================
# HISTÓRICO DE AUDITORIA
# ==========================================================

@campanhas_bp.get(
    "/<int:campanha_id>/auditoria"
)
@login_required
def auditoria_campanha(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    registros = (
        Auditoria.query
        .filter(
            Auditoria.objeto == "campanha",
            Auditoria.objeto_id == campanha.id,
        )
        .order_by(
            Auditoria.criado_em.asc()
        )
        .all()
    )

    return render_template(
        "campanhas/auditoria.html",
        campanha=campanha,
        registros=registros,
    )


# ==========================================================
# EXPORTAR RELATÓRIO PDF
# ==========================================================

@campanhas_bp.get(
    "/<int:campanha_id>/relatorio/pdf"
)
@login_required
def exportar_relatorio_pdf(campanha_id):

    campanha = db.session.get(
        Campanha,
        campanha_id,
    )

    if not campanha:

        flash(
            "Campanha não encontrada.",
            "danger",
        )

        return redirect(
            url_for(
                "campanhas.listar"
            )
        )

    (
        participacoes,
        resultados,
        indicadores,
    ) = calcular_metricas_piloto(
        campanha
    )

    auditorias = (
        Auditoria.query
        .filter(
            Auditoria.objeto == "campanha",
            Auditoria.objeto_id == campanha.id,
        )
        .order_by(
            Auditoria.criado_em.asc()
        )
        .all()
    )

    arquivo_pdf = (
        gerar_relatorio_campanha_pdf(
            campanha=campanha,
            participacoes=participacoes,
            indicadores=indicadores,
            auditorias=auditorias,
            tempos_ativos=totais_por_participacao(p.id for p in participacoes),
        )
    )

    nome_seguro = secure_filename(
        campanha.nome
    )

    nome_arquivo = (
        f"relatorio_campanha_"
        f"{campanha.id}_"
        f"{nome_seguro}.pdf"
    )

    registrar_auditoria(
        "relatorio_pdf_exportado",
        campanha,
        (
            "Relatório consolidado da campanha "
            "exportado em formato PDF."
        ),
    )

    db.session.commit()

    return send_file(
        arquivo_pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=nome_arquivo,
    )
