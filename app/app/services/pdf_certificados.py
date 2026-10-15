"""Geração de PDF para certificados da plataforma ONE.

O gerador é independente do banco de dados e não realiza
emissões, aprovações ou alterações de histórico.
"""

from datetime import timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


AZUL_ONE = colors.HexColor("#0056B3")
CINZA = colors.HexColor("#4B5563")
BORDA = colors.HexColor("#D4DDE6")

FONTE_REGULAR = "Helvetica"
FONTE_DESTAQUE = "Helvetica-Bold"


def _configurar_fontes():
    global FONTE_REGULAR, FONTE_DESTAQUE

    fontes = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
    ]

    for regular, destaque in fontes:
        if Path(regular).is_file() and Path(destaque).is_file():
            pdfmetrics.registerFont(TTFont("ONE-Regular", regular))
            pdfmetrics.registerFont(TTFont("ONE-Bold", destaque))
            FONTE_REGULAR = "ONE-Regular"
            FONTE_DESTAQUE = "ONE-Bold"
            break


def _paragrafo_centralizado(pdf, texto, y, largura, tamanho, fonte):
    estilo = ParagraphStyle(
        name="certificado",
        fontName=fonte,
        fontSize=tamanho,
        leading=tamanho * 1.5,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1F2937"),
        splitLongWords=True,
    )

    paragrafo = Paragraph(escape(str(texto)), estilo)
    _, altura = paragrafo.wrap(largura, 200)

    paragrafo.drawOn(
        pdf,
        (landscape(A4)[0] - largura) / 2,
        y - altura,
    )

    return y - altura


def gerar_pdf_certificado(recibo):
    """Retorna bytes de um PDF a partir de um recibo já emitido.

    Não consulta o banco, não emite certificados e não
    recalcula aprovação ou conclusão.
    """
    if not recibo.codigo_verificacao:
        raise ValueError("Certificado sem código de verificação")

    if not recibo.nome_participante or not recibo.titulo_treinamento:
        raise ValueError("Certificado com dados incompletos")

    _configurar_fontes()

    buffer = BytesIO()

    largura, altura = landscape(A4)

    pdf = canvas.Canvas(
        buffer,
        pagesize=(largura, altura),
        pageCompression=1,
    )

    pdf.setTitle("Certificado de Conclusão - ONE")
    pdf.setAuthor("ONE - Portal de Treinamentos")
    pdf.setSubject("Certificado demonstrativo de treinamento")

    # Fundo e bordas duplas
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, largura, altura, fill=1, stroke=0)

    pdf.setStrokeColor(BORDA)
    pdf.setLineWidth(5)
    pdf.rect(25, 25, largura - 50, altura - 50)

    pdf.setLineWidth(1.5)
    pdf.rect(35, 35, largura - 70, altura - 70)

    # Identidade do portal
    pdf.setFillColor(AZUL_ONE)
    pdf.setFont(FONTE_DESTAQUE, 17)
    pdf.drawCentredString(
        largura / 2,
        altura - 105,
        "ONE - PORTAL DE TREINAMENTOS",
    )

    pdf.setFillColor(CINZA)
    pdf.setFont(FONTE_REGULAR, 11)
    pdf.drawCentredString(
        largura / 2,
        altura - 155,
        "CERTIFICADO DEMONSTRATIVO DE CONCLUSÃO",
    )

    pdf.setFillColor(colors.HexColor("#1F2937"))
    pdf.setFont(FONTE_REGULAR, 13)
    pdf.drawCentredString(
        largura / 2,
        altura - 195,
        "Certificamos, para fins de demonstração do protótipo, que",
    )

    # Nome do participante
    y_nome = _paragrafo_centralizado(
        pdf,
        recibo.nome_participante,
        altura - 220,
        largura - 150,
        25,
        FONTE_DESTAQUE,
    )

    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.line(110, y_nome - 10, largura - 110, y_nome - 10)

    pdf.setFillColor(colors.HexColor("#1F2937"))
    pdf.setFont(FONTE_REGULAR, 13)
    pdf.drawCentredString(
        largura / 2,
        y_nome - 43,
        "concluiu o treinamento",
    )

    # Título do treinamento
    y_curso = _paragrafo_centralizado(
        pdf,
        recibo.titulo_treinamento,
        y_nome - 58,
        largura - 170,
        18,
        FONTE_DESTAQUE,
    )

    data_emissao = recibo.emitido_em
    if data_emissao.tzinfo is not None:
        data_emissao = data_emissao.astimezone(timezone.utc)

    data_formatada = data_emissao.strftime("%d/%m/%Y")

    pdf.setFillColor(CINZA)
    pdf.setFont(FONTE_REGULAR, 11)
    pdf.drawCentredString(
        largura / 2,
        y_curso - 30,
        f"Data de emissão (UTC): {data_formatada}",
    )

    # Código único de verificação
    pdf.setFont(FONTE_REGULAR, 9)
    pdf.drawCentredString(
        largura / 2,
        119,
        f"Código de verificação: {recibo.codigo_verificacao}",
    )

    # Aviso de demonstração
    pdf.setStrokeColor(colors.HexColor("#D1D5DB"))
    pdf.setLineWidth(1)
    pdf.rect(90, 54, largura - 180, 47)

    pdf.setFillColor(CINZA)
    pdf.setFont(FONTE_REGULAR, 8)
    pdf.drawCentredString(
        largura / 2,
        81,
        "DOCUMENTO DEMONSTRATIVO - sem validade oficial,",
    )
    pdf.drawCentredString(
        largura / 2,
        67,
        "acadêmica ou profissional. Protótipo ONE.",
    )

    pdf.showPage()
    pdf.save()

    return buffer.getvalue()
