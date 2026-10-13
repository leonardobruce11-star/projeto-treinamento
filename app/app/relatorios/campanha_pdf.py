from app.utils.resultados_quiz import resultado_representativo, resultados_carregados
from app.utils.painel_colaboradores import situacao_participacao
from io import BytesIO
from xml.sax.saxutils import escape
from app.utils.tempo import formatar_tempo_ativo
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)


# ==========================================================
# FUNÇÕES AUXILIARES
# ==========================================================

def formatar_data(valor):

    if not valor:
        return "-"

    if isinstance(valor, datetime):
        return valor.strftime(
            "%d/%m/%Y %H:%M"
        )

    return str(valor)


def texto(valor):

    if valor is None:
        return "-"

    valor = str(valor).strip()

    if not valor:
        return "-"

    return valor


def nome_usuario(usuario):

    if usuario:
        return texto(usuario.nome)

    return "-"


def status_formatado(status):

    if not status:
        return "-"

    return (
        status
        .replace("_", " ")
        .title()
    )


def desenhar_rodape(canvas, doc):

    canvas.saveState()

    largura, _ = A4

    canvas.setFont(
        "Helvetica",
        8
    )

    canvas.drawString(
        2 * cm,
        1.2 * cm,
        "Relatorio de campanha - SGSI"
    )

    canvas.drawRightString(
        largura - (2 * cm),
        1.2 * cm,
        f"Pagina {doc.page}"
    )

    canvas.restoreState()


# ==========================================================
# GERADOR DO PDF
# ==========================================================

def gerar_relatorio_campanha_pdf(
    campanha,
    participacoes,
    indicadores,
    auditorias,
    tempos_ativos=None,
):

    tempos_ativos = tempos_ativos or {}
    buffer = BytesIO()

    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=2 * cm,
        title=f"Relatorio - {campanha.nome}",
        author="Sistema SGSI",
    )

    estilos = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle(
        "TituloRelatorio",
        parent=estilos["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=18,
    )

    estilo_subtitulo = ParagraphStyle(
        "SubtituloRelatorio",
        parent=estilos["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        spaceBefore=12,
        spaceAfter=10,
    )

    estilo_normal = ParagraphStyle(
        "TextoRelatorio",
        parent=estilos["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
    )

    estilo_pequeno = ParagraphStyle(
        "TextoPequeno",
        parent=estilo_normal,
        fontSize=8,
        leading=11,
    )

    elementos = []

    # ======================================================
    # CAPA / IDENTIFICAÇÃO
    # ======================================================

    elementos.append(
        Paragraph(
            "RELATORIO CONSOLIDADO DA CAMPANHA",
            estilo_titulo
        )
    )

    elementos.append(
        Paragraph(
            f"<b>Campanha:</b> {texto(campanha.nome)}",
            estilo_normal
        )
    )

    elementos.append(
        Paragraph(
            (
                "<b>Status:</b> "
                f"{status_formatado(campanha.status)}"
            ),
            estilo_normal
        )
    )

    elementos.append(
        Paragraph(
            (
                "<b>Gerado em:</b> "
                f"{datetime.now().strftime('%d/%m/%Y %H:%M')}"
            ),
            estilo_normal
        )
    )

    elementos.append(
        Spacer(
            1,
            16
        )
    )

    # ======================================================
    # RESUMO EXECUTIVO
    # ======================================================

    elementos.append(
        Paragraph(
            "1. Resumo executivo",
            estilo_subtitulo
        )
    )

    dados_indicadores = [

        [
            "Indicador",
            "Resultado",
        ],
        [
            "Participantes",
            indicadores.get(
                "participantes",
                0
            ),
        ],
        [
            "Nao iniciados",
            indicadores.get(
                "nao_iniciados",
                0
            ),
        ],
        [
            "Em andamento",
            indicadores.get(
                "em_andamento",
                0
            ),
        ],
        [
            "Concluidos",
            indicadores.get(
                "concluidos",
                0
            ),
        ],
        [
            "Quizzes realizados",
            indicadores.get(
                "quizzes",
                0
            ),
        ],
        [
            "Taxa de conclusao",
            (
                f"{indicadores.get('taxa_conclusao', 0)}%"
                if indicadores.get("participantes", 0) > 0
                else "Sem dados"
            ),
        ],
        [
            "Media do quiz",
            (
                f"{indicadores.get('media_quiz', 0)}%"
                if indicadores.get("quizzes", 0) > 0
                else "Sem dados"
            ),
        ],
    ]

    tabela_indicadores = Table(
        dados_indicadores,
        colWidths=[
            11 * cm,
            5 * cm,
        ],
        repeatRows=1,
    )

    tabela_indicadores.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#E9ECEF")
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold"
            ),
            (
                "FONTNAME",
                (0, 1),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CCCCCC")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
        ])
    )

    elementos.append(
        tabela_indicadores
    )

    # ======================================================
    # CONFIGURAÇÃO
    # ======================================================

    elementos.append(
        Paragraph(
            "2. Configuracao da campanha",
            estilo_subtitulo
        )
    )

    dados_configuracao = [
        [
            "Item",
            "Configuracao",
        ],
        [
            "Grupo",
            (
                texto(campanha.grupo.nome)
                if campanha.grupo
                else "-"
            ),
        ],
        [
            "Cenario",
            (
                texto(campanha.cenario.nome)
                if campanha.cenario
                else "-"
            ),
        ],
        [
            "Template",
            (
                texto(campanha.template_email.nome)
                if campanha.template_email
                else "-"
            ),
        ],
        [
            "Landing Page",
            (
                texto(campanha.landing_page.nome)
                if campanha.landing_page
                else "-"
            ),
        ],
        [
            "Treinamento",
            (
                texto(campanha.treinamento.titulo)
                if campanha.treinamento
                else "-"
            ),
        ],
    ]

    tabela_configuracao = Table(
        dados_configuracao,
        colWidths=[
            5 * cm,
            11 * cm,
        ],
        repeatRows=1,
    )

    tabela_configuracao.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#E9ECEF")
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold"
            ),
            (
                "FONTNAME",
                (0, 1),
                (0, -1),
                "Helvetica-Bold"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CCCCCC")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                9
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
        ])
    )

    elementos.append(
        tabela_configuracao
    )

    # ======================================================
    # GOVERNANÇA
    # ======================================================

    elementos.append(
        Paragraph(
            "3. Governanca",
            estilo_subtitulo
        )
    )

    dados_governanca = [
        [
            "Etapa",
            "Responsavel",
            "Data",
        ],
        [
            "Criacao",
            nome_usuario(
                campanha.criador
            ),
            formatar_data(
                campanha.criado_em
            ),
        ],
        [
            "Aprovacao",
            nome_usuario(
                campanha.aprovador
            ),
            formatar_data(
                campanha.aprovado_em
            ),
        ],
        [
            "Preparacao tecnica",
            nome_usuario(
                campanha.preparador
            ),
            formatar_data(
                campanha.preparada_em
            ),
        ],
        [
            "Preparacao do piloto",
            nome_usuario(
                campanha.piloto_preparador
            ),
            formatar_data(
                campanha.piloto_preparado_em
            ),
        ],
        [
            "Validacao do piloto",
            nome_usuario(
                campanha.piloto_validador
            ),
            formatar_data(
                campanha.piloto_validado_em
            ),
        ],
        [
            "Decisao administrativa",
            nome_usuario(
                campanha.decisor_piloto
            ),
            formatar_data(
                campanha.decisao_piloto_em
            ),
        ],
    ]

    tabela_governanca = Table(
        dados_governanca,
        colWidths=[
            5 * cm,
            6 * cm,
            5 * cm,
        ],
        repeatRows=1,
    )

    tabela_governanca.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#E9ECEF")
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CCCCCC")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
        ])
    )

    elementos.append(
        tabela_governanca
    )

    # ======================================================
    # DECISÃO
    # ======================================================

    elementos.append(
        Paragraph(
            "4. Decisao do piloto",
            estilo_subtitulo
        )
    )

    if (
        campanha.decisao_piloto
        == "aprovado_continuidade"
    ):

        decisao = (
            "Piloto aprovado para continuidade"
        )

    elif (
        campanha.decisao_piloto
        == "ajustes_necessarios"
    ):

        decisao = (
            "Necessita ajustes"
        )

    else:

        decisao = (
            "Ainda nao registrada"
        )

    elementos.append(
        Paragraph(
            f"<b>Decisao:</b> {decisao}",
            estilo_normal
        )
    )

    elementos.append(
        Spacer(
            1,
            6
        )
    )

    elementos.append(
        Paragraph(
            (
                "<b>Justificativa:</b> "
                f"{texto(campanha.observacao_decisao_piloto)}"
            ),
            estilo_normal
        )
    )

    # ======================================================
    # RESULTADOS DO PILOTO
    # ======================================================

    elementos.append(
        Paragraph(
            "5. Resultados do piloto",
            estilo_subtitulo
        )
    )

    dados_participantes = [
        [
            "Participante",
            "Departamento",
            "Status",
            "Quiz",
            "Tempo ativo",
        ]
    ]

    for participacao in participacoes:

        colaborador = (
            participacao.colaborador
        )

        nome_colaborador = (
            texto(colaborador.nome)
            if colaborador
            else "-"
        )

        departamento = "-"

        if (
            colaborador
            and colaborador.departamento
        ):

            departamento = texto(
                colaborador.departamento.nome
            )

        status_participacao = situacao_participacao(participacao)
        if resultado_representativo(resultados_carregados(participacao)):

            quiz = (
                f"{resultado_representativo(resultados_carregados(participacao)).percentual}%"
            )

        else:

            quiz = "-"

        dados_participantes.append([
            Paragraph(escape(nome_colaborador), estilo_pequeno),
            Paragraph(escape(departamento), estilo_pequeno),
            Paragraph(escape(status_participacao), estilo_pequeno),
            quiz,
            formatar_tempo_ativo(tempos_ativos.get(participacao.id, 0)),
        ])

    if len(dados_participantes) == 1:

        dados_participantes.append([
            "Nenhum participante",
            "-",
            "-",
            "-",
            "0 min",
        ])

    tabela_participantes = Table(
        dados_participantes,
        colWidths=[
            4.5 * cm,
            3.5 * cm,
            3.2 * cm,
            1.8 * cm,
            3 * cm,
        ],
        repeatRows=1,
    )

    tabela_participantes.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#E9ECEF")
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                "Helvetica-Bold"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#CCCCCC")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "FONTSIZE",
                (0, 0),
                (-1, -1),
                8
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
        ])
    )

    elementos.append(
        tabela_participantes
    )

    # ======================================================
    # AUDITORIA
    # ======================================================

    elementos.append(
        PageBreak()
    )

    elementos.append(
        Paragraph(
            "6. Historico de auditoria",
            estilo_subtitulo
        )
    )

    if auditorias:

        for registro in auditorias:

            acao = status_formatado(
                registro.acao
            )

            responsavel = (
                nome_usuario(
                    registro.usuario
                )
                if hasattr(
                    registro,
                    "usuario"
                )
                else "-"
            )

            elementos.append(
                Paragraph(
                    (
                        f"<b>{acao}</b>"
                        "<br/>"
                        f"Data: {formatar_data(registro.criado_em)}"
                        "<br/>"
                        f"Responsavel: {responsavel}"
                    ),
                    estilo_normal
                )
            )

            if registro.detalhes:

                elementos.append(
                    Paragraph(
                        (
                            "<b>Detalhes:</b> "
                            f"{texto(registro.detalhes)}"
                        ),
                        estilo_pequeno
                    )
                )

            elementos.append(
                Spacer(
                    1,
                    12
                )
            )

    else:

        elementos.append(
            Paragraph(
                (
                    "Nenhum registro de auditoria "
                    "foi localizado para esta campanha."
                ),
                estilo_normal
            )
        )

    # ======================================================
    # OBSERVAÇÃO FINAL
    # ======================================================

    elementos.append(
        Spacer(
            1,
            18
        )
    )

    elementos.append(
        Paragraph(
            (
                "<b>Observacao:</b> Este documento consolida "
                "informacoes registradas pelo sistema durante "
                "o ciclo administrativo e o piloto controlado "
                "da campanha."
            ),
            estilo_pequeno
        )
    )

    documento.build(
        elementos,
        onFirstPage=desenhar_rodape,
        onLaterPages=desenhar_rodape,
    )

    buffer.seek(0)

    return buffer
