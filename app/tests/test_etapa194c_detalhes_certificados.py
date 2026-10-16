"""Detalhes HTML com factory real, Flask-Login ativo e SQLite exclusivamente em memória."""
import re
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

import test_etapa194b_listagem_certificados as listagem
from app.extensions import db
from app.models import CertificadoTreinamento as C, Colaborador, ParticipacaoTreinamento as P, Treinamento
from app.services.consulta_certificados import obter_certificado_emitido


class DetalhesCertificadosTest(unittest.TestCase):
    tearDown = listagem.ListagemCertificadosTest.tearDown
    autenticar = listagem.ListagemCertificadosTest.autenticar
    snapshot = listagem.ListagemCertificadosTest.snapshot
    adicionar = listagem.ListagemCertificadosTest.adicionar

    def setUp(self):
        listagem.ListagemCertificadosTest.setUp(self)
        self.url = '/certificados/41/detalhes'

    def pagina(self, **parametros):
        r = self.client.get(self.url, query_string=parametros)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_data(as_text=True)

    def test_administrador_ativo(self):
        self.assertFalse(self.app.config['LOGIN_DISABLED'])
        html = self.pagina()
        for texto in ('<h1>Detalhes do certificado</h1>', 'Certificado nº 41', 'Nome histórico',
                      'Título histórico', 'CODIGO_TESTE', 'Voltar aos certificados'):
            self.assertIn(texto, html)
        self.assertNotIn('TOKEN_PRIVADO', html)
        self.assertNotIn('/aprender/', html)
        self.assertNotIn('participacao_id', html)
        self.assertIn('href="/certificados/41/pdf"', html)
        self.assertRegex(html, r'href="/certificados/"\s+class="nav-item active"')
        r = self.client.get(self.url)
        self.assertEqual(r.mimetype, 'text/html')
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        self.assertEqual(r.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')

    def bloqueado(self, uid, status):
        self.autenticar(uid)
        with patch('app.certificados_admin.routes.obter_certificado_emitido') as consulta:
            for metodo in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=metodo)
                self.assertEqual(r.status_code, status)
                self.assertNotIn(b'CODIGO_TESTE', r.data)
                self.assertNotIn(b'Nome hist', r.data)
            consulta.assert_not_called()

    def test_anonimo(self):
        self.bloqueado(None, 302)
        self.assertIn('/login', self.client.get(self.url).location)

    def test_operador(self):
        self.bloqueado(self.operador, 403)

    def test_administrador_inativo(self):
        self.bloqueado(self.inativo, 302)

    def test_inexistente(self):
        for metodo in ('GET', 'HEAD'):
            r = self.client.open('/certificados/999/detalhes', method=metodo)
            self.assertEqual(r.status_code, 404)
            self.assertNotIn(b'CODIGO_TESTE', r.data)

    def test_identificador_invalido(self):
        for valor in ('0', '-1', 'abc', '1.5', '2147483648', '9'*80):
            with self.subTest(valor=valor):
                r = self.client.get(f'/certificados/{valor}/detalhes')
                self.assertEqual(r.status_code, 404)
                self.assertNotIn(b'CODIGO_TESTE', r.data)

    def test_preservacao_snapshot(self):
        with self.app.app_context():
            db.session.get(Colaborador, self.cid).nome = 'Nome atual alterado'
            db.session.get(Treinamento, self.tid).titulo = 'Título atual alterado'
            db.session.commit()
        antes = self.snapshot()
        html = self.pagina()
        self.assertIn('Nome histórico', html)
        self.assertIn('Título histórico', html)
        self.assertNotIn('Nome atual alterado', html)
        self.assertNotIn('Título atual alterado', html)
        self.assertEqual(self.snapshot(), antes)

    def test_colaborador_desativado(self):
        with self.app.app_context():
            db.session.get(Colaborador, self.cid).ativo = False
            db.session.commit()
        self.assertIn('Nome histórico', self.pagina())

    def test_treinamento_desativado(self):
        with self.app.app_context():
            db.session.get(Treinamento, self.tid).ativo = False
            db.session.commit()
        self.assertIn('Título histórico', self.pagina())

    def test_codigo_de_verificacao(self):
        self.assertIn('<code>CODIGO_TESTE</code>', self.pagina())

    def test_data_e_hora_utc(self):
        html = self.pagina()
        self.assertIn('Data e horário de emissão (UTC)', html)
        self.assertIn('15/10/2026 12:00:00 UTC', html)
        with self.app.app_context():
            recibo = obter_certificado_emitido(41)
        # Exercita o ramo de datetime com fuso, usado em PostgreSQL.
        com_fuso = replace(recibo, emitido_em=datetime(2026, 10, 15, 9, tzinfo=timezone(timedelta(hours=-3))))
        with patch('app.certificados_admin.routes.obter_certificado_emitido', return_value=com_fuso):
            self.assertIn('15/10/2026 12:00:00 UTC', self.pagina())

    def test_escape_html(self):
        with self.app.app_context():
            c = db.session.get(C, 41)
            c.nome_participante = '<script>alert(1)</script>'
            c.titulo_treinamento = '<img src=x onerror=alert(2)>'
            c.codigo_verificacao = '<svg onload=alert(3)>'
            db.session.commit()
        html = self.pagina()
        for literal in ('<script>alert(1)', '<img src=x', '<svg onload=alert(3)'):
            self.assertNotIn(literal, html)
        for escapado in ('&lt;script&gt;', '&lt;img', '&lt;svg'):
            self.assertIn(escapado, html)

    def test_get_head_sem_escrita_ou_emissao(self):
        antes = self.snapshot()
        self.statements.clear()
        with patch('app.services.certificados.emitir_certificado', side_effect=AssertionError('Emitiu')) as emissor, \
                patch('app.portal_treinamento.routes.emitir_certificado', side_effect=AssertionError('Emitiu')), \
                patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            for metodo in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=metodo)
                self.assertEqual(r.status_code, 200)
                if metodo == 'HEAD':
                    self.assertEqual(r.data, b'')
                self.assertEqual(self.client.open('/certificados/999/detalhes', method=metodo).status_code, 404)
            emissor.assert_not_called()
        self.assertTrue(self.statements)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements))
        self.assertFalse(any('participacoes_treinamento' in sql for sql in self.statements))
        self.assertEqual(self.snapshot(), antes)

    def test_participacao_aprovada_sem_certificado(self):
        antes = self.snapshot()
        self.assertEqual(self.client.get(f'/certificados/{self.sem_certificado}/detalhes').status_code, 404)
        self.assertEqual(self.snapshot(), antes)

    def test_nao_revalida_aprovacao_divergente(self):
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            p.aprovado_em = p.concluido_em = None
            db.session.commit()
        antes = self.snapshot()
        with patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            self.assertIn('CODIGO_TESTE', self.pagina())
        self.assertEqual(self.snapshot(), antes)

    def test_link_listagem_detalhes_e_retorno_preserva_filtros_pagina(self):
        self.adicionar(41, nome='Maria & João', titulo='Dados pessoais', codigo='BUSCA')
        filtros = {'participante': 'Maria & João', 'treinamento': 'Dados pessoais', 'codigo': 'BUSCA', 'page': '2'}
        r = self.client.get('/certificados/', query_string=filtros)
        self.assertEqual(r.status_code, 200)
        resultado = self.contextos[-1]['resultado']
        ids_antes = [c.certificado_id for c in resultado.itens]
        html = r.get_data(as_text=True)
        links = re.findall(r'href="([^"]+)">Visualizar detalhes</a>', html)
        self.assertEqual(len(links), 20)
        for ident, link in zip(ids_antes, links):
            parsed = urlsplit(unescape(link))
            self.assertEqual(parsed.path, f'/certificados/{ident}/detalhes')
            self.assertEqual(parse_qs(parsed.query), {k: [v] for k, v in filtros.items()})
        detalhes = self.client.get(unescape(links[0])).get_data(as_text=True)
        voltar = re.search(r'href="([^"]+)" class="btn-one-secondary">Voltar aos certificados', detalhes)
        self.assertIsNotNone(voltar)
        retorno = unescape(voltar[1])
        self.assertEqual(urlsplit(retorno).path, '/certificados/')
        self.assertEqual(parse_qs(urlsplit(retorno).query), {k: [v] for k, v in filtros.items()})
        r = self.client.get(retorno)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c.certificado_id for c in self.contextos[-1]['resultado'].itens], ids_antes)

    def test_retorno_sem_destino_externo_ou_parametros_privados(self):
        html = self.pagina(next='https://externo.example.invalid', token='SEGREDO', participacao_id='999')
        voltar = re.search(r'href="([^"]+)" class="btn-one-secondary">Voltar aos certificados', html)
        self.assertEqual(voltar[1], '/certificados/')
        self.assertNotIn('SEGREDO', html)
        self.assertNotIn('externo.example.invalid', html)

    def test_contexto_de_retorno_invalido_nao_impede_consulta(self):
        html = self.pagina(participante='X'*151, treinamento='\x00', codigo='X'*65, page='9999999999999')
        self.assertIn('CODIGO_TESTE', html)
        voltar = re.search(r'href="([^"]+)" class="btn-one-secondary">Voltar aos certificados', html)
        self.assertEqual(voltar[1], '/certificados/')
        r = self.client.get(self.url+'?page=1&page=2&codigo=A&codigo=B')
        self.assertEqual(r.status_code, 200)
        self.assertIn('href="/certificados/" class="btn-one-secondary">Voltar aos certificados', r.get_data(as_text=True))

    def test_contrato_json_preservado(self):
        r = self.client.get('/certificados/41')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, 'application/json')
        self.assertEqual(r.json, dict(certificado_id=41, participacao_id=self.pid,
            nome_participante='Nome histórico', titulo_treinamento='Título histórico',
            codigo_verificacao='CODIGO_TESTE', emitido_em='2026-10-15T12:00:00'))

    def test_falha_banco_sem_excecao_privada(self):
        with patch('app.certificados_admin.routes.obter_certificado_emitido', side_effect=SQLAlchemyError('SQL_PRIVADO')):
            for metodo in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=metodo)
                self.assertEqual(r.status_code, 503)
                self.assertNotIn(b'SQL_PRIVADO', r.data)
                self.assertNotIn(b'CODIGO_TESTE', r.data)
                self.assertEqual(r.headers['Cache-Control'], 'no-store')
                if metodo == 'GET':
                    self.assertIn('Consulta indisponível', r.get_data(as_text=True))

    def test_uma_consulta_sem_relacionamentos(self):
        self.statements.clear()
        self.pagina()
        consultas = [sql for sql in self.statements if 'certificados_treinamento' in sql]
        self.assertEqual(len(consultas), 1)
        self.assertNotIn('JOIN', consultas[0])
        self.assertFalse(any('participacoes_treinamento' in sql for sql in self.statements))

    def test_sem_rotas_ou_botoes_de_mutacao(self):
        html = self.pagina()
        for rota in (self.url, '/certificados/41/editar', '/certificados/41/revogar'):
            if rota != self.url:
                self.assertEqual(self.client.get(rota).status_code, 404)
        regra = next(r for r in self.app.url_map.iter_rules() if r.endpoint == 'certificados_admin.detalhes')
        self.assertEqual(regra.methods, {'GET', 'HEAD', 'OPTIONS'})
        self.assertIn('Baixar PDF', html)
        for texto in ('Emitir certificado', 'Revogar', 'Excluir certificado'):
            self.assertNotIn(texto, html)


if __name__ == '__main__':
    unittest.main()
