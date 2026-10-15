"""Etapa 19.3-C: testes isolados do gerador de PDF."""

import re
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from reportlab.pdfgen import canvas

from app.services.certificados import ReciboCertificado
from app.services import pdf_certificados


class CertificadoPDFTest(unittest.TestCase):

    def setUp(self):
        self.recibo = ReciboCertificado(
            certificado_id=123,
            participacao_id=456,
            codigo_verificacao="ONE-TESTE-CODIGO-123456",
            nome_participante="Mariana Costa",
            titulo_treinamento=(
                "Onboarding Institucional: Bem-vindo à Empresa"
            ),
            emitido_em=datetime(
                2026, 10, 15, 12, 0, tzinfo=timezone.utc
            ),
            criado=False,
        )

    def gerar(self, **alteracoes):
        dados = {
            "certificado_id": self.recibo.certificado_id,
            "participacao_id": self.recibo.participacao_id,
            "codigo_verificacao": self.recibo.codigo_verificacao,
            "nome_participante": self.recibo.nome_participante,
            "titulo_treinamento": self.recibo.titulo_treinamento,
            "emitido_em": self.recibo.emitido_em,
            "criado": False,
        }
        dados.update(alteracoes)

        return pdf_certificados.gerar_pdf_certificado(
            ReciboCertificado(**dados)
        )

    def test_01_pdf_valido(self):
        conteudo = self.gerar()

        self.assertIsInstance(conteudo, bytes)
        self.assertTrue(conteudo.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", conteudo[-1024:])
        self.assertGreater(len(conteudo), 1000)

    def test_02_pagina_unica_a4_horizontal(self):
        conteudo = self.gerar()

        paginas = re.findall(
            rb"/Type\s*/Page\b", conteudo
        )
        self.assertEqual(len(paginas), 1)

        caixa = re.search(
            rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)"
            rb"\s+([\d.]+)\s+([\d.]+)\s*\]",
            conteudo,
        )

        self.assertIsNotNone(caixa)

        x1, y1, x2, y2 = map(
            float, caixa.groups()
        )

        largura = x2 - x1
        altura = y2 - y1

        self.assertAlmostEqual(largura, 841.89, delta=1)
        self.assertAlmostEqual(altura, 595.28, delta=1)
        self.assertGreater(largura, altura)

    def test_03_campos_obrigatorios(self):
        for campo in (
            "codigo_verificacao",
            "nome_participante",
            "titulo_treinamento",
        ):
            with self.subTest(campo=campo):
                with self.assertRaises(ValueError):
                    self.gerar(**{campo: ""})

    def test_04_nome_e_titulo_extensos(self):
        conteudo = self.gerar(
            nome_participante=(
                "Mariana de Oliveira Costa Albuquerque "
                "dos Santos Ferreira"
            ),
            titulo_treinamento=(
                "Programa Corporativo de Conscientização "
                "em Segurança da Informação e Proteção "
                "de Dados no Ambiente Organizacional"
            ),
        )

        self.assertTrue(conteudo.startswith(b"%PDF-"))
        self.assertGreater(len(conteudo), 1000)

    def test_05_caracteres_acentuados(self):
        conteudo = self.gerar(
            nome_participante="João Gonçalves de Araújo",
            titulo_treinamento=(
                "Segurança da Informação: "
                "Prevenção de Ameaças e Proteção de Dados"
            ),
        )

        self.assertTrue(conteudo.startswith(b"%PDF-"))

    def test_06_dados_enviados_para_renderizacao(self):
        textos = []
        original_texto = canvas.Canvas.drawCentredString
        original_paragrafo = (
            pdf_certificados._paragrafo_centralizado
        )

        def registrar_texto(objeto, x, y, texto, *args, **kwargs):
            textos.append(str(texto))
            return original_texto(
                objeto, x, y, texto, *args, **kwargs
            )

        def registrar_paragrafo(
            pdf, texto, y, largura, tamanho, fonte
        ):
            textos.append(str(texto))
            return original_paragrafo(
                pdf, texto, y, largura, tamanho, fonte
            )

        with (
            patch.object(
                canvas.Canvas,
                "drawCentredString",
                new=registrar_texto,
            ),
            patch.object(
                pdf_certificados,
                "_paragrafo_centralizado",
                new=registrar_paragrafo,
            ),
        ):
            self.gerar()

        conteudo = "\n".join(textos)

        self.assertIn("Mariana Costa", conteudo)
        self.assertIn(
            "Onboarding Institucional", conteudo
        )
        self.assertIn(
            "ONE-TESTE-CODIGO-123456", conteudo
        )
        self.assertIn("15/10/2026", conteudo)

    def test_07_emissoes_nao_alteram_recibo(self):
        nome_antes = self.recibo.nome_participante
        codigo_antes = self.recibo.codigo_verificacao
        data_antes = self.recibo.emitido_em

        primeiro = pdf_certificados.gerar_pdf_certificado(
            self.recibo
        )
        segundo = pdf_certificados.gerar_pdf_certificado(
            self.recibo
        )

        self.assertTrue(primeiro.startswith(b"%PDF-"))
        self.assertTrue(segundo.startswith(b"%PDF-"))

        self.assertEqual(
            self.recibo.nome_participante, nome_antes
        )
        self.assertEqual(
            self.recibo.codigo_verificacao, codigo_antes
        )
        self.assertEqual(
            self.recibo.emitido_em, data_antes
        )


if __name__ == "__main__":
    unittest.main()
