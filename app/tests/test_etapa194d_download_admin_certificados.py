"""PDF administrativo real, autorização ativa e SQLite em memória, sem ambiente ONE."""
import re
import unittest
from html import unescape
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from flask import send_file
from sqlalchemy.exc import SQLAlchemyError

import test_etapa194c_detalhes_certificados as detalhes
from app.extensions import db
from app.models import CertificadoTreinamento as C, Colaborador, ParticipacaoTreinamento as P, Treinamento, Usuario
from app.services.pdf_certificados import gerar_pdf_certificado


class DownloadAdminCertificadosTest(unittest.TestCase):
    tearDown = detalhes.DetalhesCertificadosTest.tearDown
    autenticar = detalhes.DetalhesCertificadosTest.autenticar
    snapshot = detalhes.DetalhesCertificadosTest.snapshot
    adicionar = detalhes.DetalhesCertificadosTest.adicionar

    def setUp(self):
        detalhes.DetalhesCertificadosTest.setUp(self)
        self.url = '/certificados/41/pdf'

    def cabecalhos_privados(self, resposta):
        self.assertEqual(resposta.headers['Cache-Control'], 'no-store')
        self.assertEqual(resposta.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(resposta.headers['X-Content-Type-Options'], 'nosniff')

    def pdf_valido(self, resposta):
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.mimetype, 'application/pdf')
        self.assertTrue(resposta.data.startswith(b'%PDF-'))
        self.assertTrue(resposta.data.rstrip().endswith(b'%%EOF'))
        self.assertGreater(len(resposta.data), 1000)
        self.assertEqual(int(resposta.headers['Content-Length']), len(resposta.data))
        self.cabecalhos_privados(resposta)

    def test_download_administrador_ativo(self):
        self.assertFalse(self.app.config['LOGIN_DISABLED'])
        self.pdf_valido(self.client.get(self.url))

    def bloqueado(self, uid, status):
        self.autenticar(uid)
        with patch('app.certificados_admin.routes.obter_certificado_emitido') as consultar, \
                patch('app.certificados_admin.routes.gerar_pdf_certificado') as gerar:
            for metodo in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=metodo)
                self.assertEqual(r.status_code, status)
                self.assertNotIn(b'CODIGO_TESTE', r.data)
                self.assertNotIn(b'Nome hist', r.data)
                self.assertNotEqual(r.mimetype, 'application/pdf')
                self.assertNotIn('Content-Disposition', r.headers)
                self.cabecalhos_privados(r)
            consultar.assert_not_called()
            gerar.assert_not_called()

    def test_anonimo(self):
        self.bloqueado(None, 302)
        self.assertIn('/login', self.client.get(self.url).location)

    def test_operador(self):
        self.bloqueado(self.operador, 403)

    def test_administrador_inativo(self):
        self.bloqueado(self.inativo, 302)

    def test_administrador_desativado_apos_download(self):
        self.pdf_valido(self.client.get(self.url))
        with self.app.app_context():
            db.session.get(Usuario, self.uid).ativo = False
            db.session.commit()
        self.bloqueado(self.uid, 302)

    def test_certificado_inexistente(self):
        with patch('app.certificados_admin.routes.gerar_pdf_certificado') as gerar:
            for metodo in ('GET', 'HEAD'):
                r = self.client.open('/certificados/999/pdf', method=metodo)
                self.assertEqual(r.status_code, 404)
                self.assertNotEqual(r.mimetype, 'application/pdf')
                self.assertNotIn('Content-Disposition', r.headers)
                self.cabecalhos_privados(r)
            gerar.assert_not_called()

    def test_identificador_invalido(self):
        with patch('app.certificados_admin.routes.gerar_pdf_certificado') as gerar:
            for ident in ('0', '-1', 'abc', '1.5', '2147483648', '9'*80):
                with self.subTest(ident=ident):
                    self.assertEqual(self.client.get(f'/certificados/{ident}/pdf').status_code, 404)
            gerar.assert_not_called()

    def test_cabecalhos_attachment_sem_etag(self):
        r = self.client.get(self.url)
        self.assertEqual(r.headers['Content-Type'], 'application/pdf')
        self.assertEqual(r.headers['Content-Disposition'], 'attachment; filename=certificado-41.pdf')
        self.assertNotIn('ETag', r.headers)
        self.assertNotIn('Last-Modified', r.headers)
        self.cabecalhos_privados(r)

    def test_nome_seguro_independente_de_nome_e_parametros(self):
        with self.app.app_context():
            db.session.get(C, 41).nome_participante = 'João ../Sobrenome'
            db.session.commit()
        r = self.client.get(self.url, query_string={'filename': '../pessoa.pdf', 'nome': 'Privado'})
        self.pdf_valido(r)
        self.assertEqual(r.headers['Content-Disposition'], 'attachment; filename=certificado-41.pdf')
        self.assertNotIn('João', r.headers['Content-Disposition'])
        self.assertNotIn('Privado', r.headers['Content-Disposition'])

    def test_head_gera_em_memoria_sem_corpo(self):
        with patch('app.certificados_admin.routes.gerar_pdf_certificado', wraps=gerar_pdf_certificado) as gerar:
            r = self.client.head(self.url)
            gerar.assert_called_once()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'application/pdf')
        self.assertEqual(r.data, b'')
        self.assertGreater(int(r.headers['Content-Length']), 1000)
        self.assertEqual(r.headers['Content-Disposition'], 'attachment; filename=certificado-41.pdf')
        self.cabecalhos_privados(r)

    def test_send_file_recebe_apenas_buffer_em_memoria(self):
        with patch('app.certificados_admin.routes.send_file', wraps=send_file) as enviar:
            self.pdf_valido(self.client.get(self.url))
            enviar.assert_called_once()
            self.assertIsInstance(enviar.call_args.args[0], BytesIO)
            self.assertFalse(enviar.call_args.kwargs['conditional'])
            self.assertFalse(enviar.call_args.kwargs['etag'])

    def test_snapshots_historicos_entregues_ao_gerador(self):
        with self.app.app_context():
            db.session.get(Colaborador, self.cid).nome = 'Nome atual'
            db.session.get(Treinamento, self.tid).titulo = 'Título atual'
            db.session.commit()
        antes = self.snapshot()
        with patch('app.certificados_admin.routes.gerar_pdf_certificado', wraps=gerar_pdf_certificado) as gerar:
            self.pdf_valido(self.client.get(self.url))
        recibo = gerar.call_args.args[0]
        self.assertEqual(recibo.nome_participante, 'Nome histórico')
        self.assertEqual(recibo.titulo_treinamento, 'Título histórico')
        self.assertEqual(recibo.codigo_verificacao, 'CODIGO_TESTE')
        self.assertEqual(recibo.emitido_em.isoformat(), '2026-10-15T12:00:00')
        self.assertEqual(recibo.certificado_id, 41)
        self.assertFalse(recibo.criado)
        self.assertEqual(self.snapshot(), antes)

    def test_colaborador_desativado(self):
        with self.app.app_context():
            db.session.get(Colaborador, self.cid).ativo = False
            db.session.commit()
        self.pdf_valido(self.client.get(self.url))

    def test_treinamento_desativado(self):
        with self.app.app_context():
            db.session.get(Treinamento, self.tid).ativo = False
            db.session.commit()
        self.pdf_valido(self.client.get(self.url))

    def test_aprovada_sem_certificado_nao_emite(self):
        antes = self.snapshot()
        with patch('app.services.certificados.emitir_certificado') as emitir, \
                patch('app.certificados_admin.routes.gerar_pdf_certificado') as gerar:
            self.assertEqual(self.client.get(f'/certificados/{self.sem_certificado}/pdf').status_code, 404)
            emitir.assert_not_called()
            gerar.assert_not_called()
        self.assertEqual(self.snapshot(), antes)

    def test_sem_revalidacao_de_aprovacao(self):
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            p.concluido_em = p.aprovado_em = None
            db.session.commit()
        antes = self.snapshot()
        with patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            self.pdf_valido(self.client.get(self.url))
        self.assertEqual(self.snapshot(), antes)

    def test_get_head_sem_escrita_ou_emissao(self):
        antes = self.snapshot()
        self.statements.clear()
        with patch('app.services.certificados.emitir_certificado', side_effect=AssertionError('Emitiu')) as emitir, \
                patch('app.portal_treinamento.routes.emitir_certificado', side_effect=AssertionError('Emitiu')) as portal, \
                patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            for metodo in ('GET', 'HEAD'):
                for _ in range(2):
                    r = self.client.open(self.url, method=metodo)
                    self.assertEqual(r.status_code, 200)
                    if metodo == 'HEAD':
                        self.assertEqual(r.data, b'')
            emitir.assert_not_called()
            portal.assert_not_called()
        self.assertTrue(self.statements)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements))
        self.assertFalse(any('participacoes_treinamento' in sql for sql in self.statements))
        self.assertEqual(self.snapshot(), antes)

    def test_uma_consulta_sem_relacionamentos(self):
        self.statements.clear()
        self.pdf_valido(self.client.get(self.url))
        consultas = [sql for sql in self.statements if 'certificados_treinamento' in sql]
        self.assertEqual(len(consultas), 1)
        self.assertNotIn('JOIN', consultas[0])

    def test_falha_sql_sem_pdf_ou_dados_privados(self):
        antes = self.snapshot()
        with patch('app.certificados_admin.routes.obter_certificado_emitido', side_effect=SQLAlchemyError('SQL_PRIVADO')), \
                patch('app.certificados_admin.routes.gerar_pdf_certificado') as gerar:
            for metodo in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=metodo)
                self.assertEqual(r.status_code, 503)
                self.assertNotEqual(r.mimetype, 'application/pdf')
                self.assertNotIn('Content-Disposition', r.headers)
                self.assertNotIn(b'SQL_PRIVADO', r.data)
                self.cabecalhos_privados(r)
            gerar.assert_not_called()
        self.assertEqual(self.snapshot(), antes)

    def test_falhas_renderizador_sem_expor_caminhos_ou_snapshot(self):
        antes = self.snapshot()
        for erro, status in ((ValueError('CODIGO_TESTE Nome histórico'), 409),
                             (RuntimeError('/caminho/interno/PRIVADO'), 503),
                             (OSError('ARQUIVO_PRIVADO'), 503)):
            with self.subTest(erro=type(erro).__name__), \
                    patch('app.certificados_admin.routes.gerar_pdf_certificado', side_effect=erro):
                for metodo in ('GET', 'HEAD'):
                    r = self.client.open(self.url, method=metodo)
                    self.assertEqual(r.status_code, status)
                    self.assertEqual(r.mimetype, 'application/json')
                    self.assertNotIn('Content-Disposition', r.headers)
                    for privado in (b'CODIGO_TESTE', b'PRIVADO', b'Traceback'):
                        self.assertNotIn(privado, r.data)
                    self.cabecalhos_privados(r)
        self.assertEqual(self.snapshot(), antes)

    def test_snapshot_incompleto_rejeitado_pelo_gerador_real(self):
        with self.app.app_context():
            db.session.get(C, 41).nome_participante = ''
            db.session.commit()
        antes = self.snapshot()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json, {'erro': 'dados_certificado_invalidos'})
        self.assertNotIn('Content-Disposition', r.headers)
        self.assertEqual(self.snapshot(), antes)

    def test_retorno_vazio_invalido_ou_truncado_nao_e_download(self):
        for conteudo in (b'', None, '%PDF-1.4\n%%EOF', b'nao-pdf', b'%PDF-1.4\nINCOMPLETO'):
            with self.subTest(conteudo=conteudo), \
                    patch('app.certificados_admin.routes.gerar_pdf_certificado', return_value=conteudo):
                for metodo in ('GET', 'HEAD'):
                    r = self.client.open(self.url, method=metodo)
                    self.assertEqual(r.status_code, 503)
                    self.assertEqual(r.mimetype, 'application/json')
                    self.assertNotIn('Content-Disposition', r.headers)
                    self.cabecalhos_privados(r)

    def test_botao_pdf_e_retorno_com_filtros_pagina(self):
        parametros = dict(participante='Maria & João', treinamento='Dados pessoais', codigo='CODIGO', page='2')
        r = self.client.get('/certificados/41/detalhes', query_string=parametros)
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        baixar = re.search(r'href="([^"]+)" class="btn-one-primary">Baixar PDF', html)
        self.assertIsNotNone(baixar)
        self.assertEqual(baixar[1], self.url)
        voltar = re.search(r'href="([^"]+)" class="btn-one-secondary">Voltar aos certificados', html)
        retorno = urlsplit(unescape(voltar[1]))
        self.assertEqual(retorno.path, '/certificados/')
        self.assertEqual(parse_qs(retorno.query), {k: [v] for k, v in parametros.items()})
        self.pdf_valido(self.client.get(baixar[1]))

    def test_sem_botao_de_pdf_quando_consulta_detalhes_falha(self):
        with patch('app.certificados_admin.routes.obter_certificado_emitido', side_effect=SQLAlchemyError('PRIVADO')):
            r = self.client.get('/certificados/41/detalhes')
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(b'Baixar PDF', r.data)
        self.assertIn(b'Voltar aos certificados', r.data)

    def test_rota_exclusivamente_de_leitura(self):
        regra = next(r for r in self.app.url_map.iter_rules() if r.endpoint == 'certificados_admin.baixar_pdf')
        self.assertEqual(regra.methods, {'GET', 'HEAD', 'OPTIONS'})
        for url in ('/certificados/41/editar', '/certificados/41/revogar', '/certificados/41/excluir'):
            self.assertEqual(self.client.get(url).status_code, 404)


if __name__ == '__main__':
    unittest.main()
