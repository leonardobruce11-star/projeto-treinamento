"""Listagem real, banco em memória e autorização habilitada; sem ambiente ONE."""
import re
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from flask import render_template, template_rendered
from jinja2 import StrictUndefined
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

import test_etapa194a_consulta_certificados as consulta
from app.extensions import db
from app.models import CertificadoTreinamento as C, Colaborador, ParticipacaoTreinamento as P, Treinamento
from app.services.consulta_certificados import listar_certificados_emitidos


class ListagemCertificadosTest(unittest.TestCase):
    autenticar = consulta.ConsultaCertificadosAdminTest.autenticar
    snapshot = consulta.ConsultaCertificadosAdminTest.snapshot
    tearDown = consulta.ConsultaCertificadosAdminTest.tearDown

    def setUp(self):
        consulta.ConsultaCertificadosAdminTest.setUp(self)
        self.url = '/certificados/'
        self.app.jinja_env.undefined = StrictUndefined
        self.contextos = []
        def capturar(sender, template, context, **kwargs):
            self.contextos.append(context)
        template_rendered.connect(capturar, self.app, weak=False)
        self.addCleanup(template_rendered.disconnect, capturar, self.app)
        # Renderiza a navegação real, inclusive para perfis sem acesso à listagem.
        self.app.add_url_rule('/_menu_teste', view_func=lambda: render_template('base.html'))

    def get(self, **parametros):
        resposta = self.client.get(self.url, query_string=parametros)
        self.assertEqual(resposta.status_code, 200, resposta.get_data(as_text=True))
        return resposta.get_data(as_text=True), self.contextos[-1]['resultado']

    def adicionar(self, quantidade, nome='Nome histórico', titulo='Título histórico', codigo='LOTE', instante=None):
        with self.app.app_context():
            for indice in range(quantidade):
                p = P(colaborador_id=self.cid, treinamento_id=self.tid, token=f'TOKEN_LOTE_{codigo}_{indice}')
                db.session.add(p)
                db.session.flush()
                db.session.add(C(participacao_id=p.id, nome_participante=nome, titulo_treinamento=titulo,
                                 codigo_verificacao=f'{codigo}-{indice}',
                                 emitido_em=instante or datetime(2026, 10, 16, 12)))
            db.session.commit()

    def test_administrador_ativo_e_cabecalhos(self):
        self.assertFalse(self.app.config['LOGIN_DISABLED'])
        html, resultado = self.get()
        self.assertIn('<h1>Certificados emitidos</h1>', html)
        self.assertEqual(resultado.total, 1)
        for valor in ('Nome histórico', 'Título histórico', 'CODIGO_TESTE', '15/10/2026 12:00', '>41<'):
            self.assertIn(valor, html)
        self.assertIn('Data de emissão (UTC)', html)
        self.assertIn('Voltar ao painel administrativo', html)
        r = self.client.get(self.url)
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        self.assertEqual(r.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('TOKEN_PRIVADO', html)
        self.assertNotIn('/aprender/', html)
        self.assertNotIn('/certificados/41', html)
        self.assertNotIn('/pdf', html)

    def test_anonimo(self):
        self.verificar_bloqueio(None, 302)

    def test_operador(self):
        self.verificar_bloqueio(self.operador, 403)

    def test_administrador_inativo(self):
        self.verificar_bloqueio(self.inativo, 302)

    def verificar_bloqueio(self, uid, status):
        self.autenticar(uid)
        with patch('app.certificados_admin.routes.listar_certificados_emitidos') as consultar:
            for method in ('GET', 'HEAD'):
                r = self.client.open(self.url, method=method)
                self.assertEqual(r.status_code, status)
                self.assertNotIn(b'CODIGO_TESTE', r.data)
                self.assertEqual(r.headers['Cache-Control'], 'no-store')
            consultar.assert_not_called()

    def test_listagem_vazia(self):
        with self.app.app_context():
            db.session.delete(db.session.get(C, 41))
            db.session.commit()
        html, resultado = self.get()
        self.assertIn('Nenhum certificado emitido.', html)
        self.assertEqual((resultado.total, resultado.pagina, resultado.paginas, resultado.itens), (0, 1, 1, ()))
        self.assertNotIn('rel="next"', html)

    def test_sem_resultados_para_filtro(self):
        html, resultado = self.get(participante='Pessoa ausente')
        self.assertEqual(resultado.total, 0)
        self.assertIn('Nenhum certificado encontrado para os filtros selecionados.', html)
        self.assertIn('value="Pessoa ausente"', html)

    def test_participacao_aprovada_sem_certificado_nao_aparece(self):
        antes = self.snapshot()
        _, resultado = self.get()
        self.assertEqual([c.participacao_id for c in resultado.itens], [self.pid])
        self.assertNotIn(self.sem_certificado, [c.participacao_id for c in resultado.itens])
        self.assertEqual(self.snapshot(), antes)

    def test_snapshot_e_cadastros_inativos(self):
        with self.app.app_context():
            c = db.session.get(Colaborador, self.cid)
            t = db.session.get(Treinamento, self.tid)
            c.nome, c.ativo = 'Nome atual', False
            t.titulo, t.ativo = 'Título atual', False
            db.session.get(P, self.pid).aprovado_em = None
            db.session.commit()
        antes = self.snapshot()
        html, resultado = self.get(participante='Nome histórico', treinamento='Título histórico')
        self.assertEqual(resultado.total, 1)
        self.assertIn('Nome histórico', html)
        self.assertNotIn('Nome atual', html)
        self.assertEqual(self.get(participante='Nome atual')[1].total, 0)
        self.assertEqual(self.snapshot(), antes)

    def test_pesquisa_participante(self):
        self.adicionar(1, nome='Mariana Costa', codigo='PESSOA')
        _, resultado = self.get(participante='mArIaNa')
        self.assertEqual([c.nome_participante for c in resultado.itens], ['Mariana Costa'])

    def test_pesquisa_treinamento(self):
        self.adicionar(1, titulo='Proteção de dados', codigo='CURSO')
        _, resultado = self.get(treinamento='dados')
        self.assertEqual([c.titulo_treinamento for c in resultado.itens], ['Proteção de dados'])

    def test_pesquisa_codigo(self):
        self.adicionar(1, codigo='VERIFICACAO')
        _, resultado = self.get(codigo='verificacao-0')
        self.assertEqual([c.codigo_verificacao for c in resultado.itens], ['VERIFICACAO-0'])

    def test_filtros_combinados(self):
        self.adicionar(1, nome='Mariana', titulo='Dados', codigo='COMBINADO')
        self.assertEqual(self.get(participante='Mariana', treinamento='Dados', codigo='COMBINADO')[1].total, 1)
        self.assertEqual(self.get(participante='Mariana', treinamento='Outro')[1].total, 0)

    def test_curingas_literais_e_injecao(self):
        self.adicionar(1, nome='Pessoa 100%_!', titulo='Curso %_!', codigo='LITERAL%_!')
        for chave in ('participante', 'treinamento', 'codigo'):
            with self.subTest(chave=chave):
                self.assertEqual(self.get(**{chave: '%_!'})[1].total, 1)
                valor = "' OR 1=1 --"
                self.statements.clear()
                self.assertEqual(self.get(**{chave: valor})[1].total, 0)
                self.assertFalse(any(valor in sql for sql in self.statements))

    def test_escape_html_em_dados_e_formulario(self):
        nome = '<script>alert(1)</script>'
        titulo = '<img src=x onerror=alert(2)>'
        codigo = '<svg onload=alert(3)>'
        with self.app.app_context():
            c = db.session.get(C, 41)
            c.nome_participante, c.titulo_treinamento, c.codigo_verificacao = nome, titulo, codigo
            db.session.commit()
        html, _ = self.get(participante=nome, treinamento=titulo, codigo=codigo)
        for valor in (nome, titulo, codigo):
            self.assertNotIn(valor, html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img', html)
        self.assertIn('&lt;svg', html)

    def test_paginacao_e_ordenacao_deterministica(self):
        # 45 documentos com mesma data + o documento original mais antigo.
        self.adicionar(45)
        with self.app.app_context():
            esperados = list(db.session.execute(select(C.id).order_by(C.emitido_em.desc(), C.id.desc())).scalars())
        ids = []
        for pagina, tamanho in ((1, 20), (2, 20), (3, 6)):
            html, resultado = self.get(page=str(pagina))
            self.assertEqual((len(resultado.itens), resultado.total, resultado.paginas), (tamanho, 46, 3))
            ids.extend(c.certificado_id for c in resultado.itens)
            self.assertEqual('rel="prev"' in html, pagina > 1)
            self.assertEqual('rel="next"' in html, pagina < 3)
        self.assertEqual(ids, esperados)
        self.assertEqual(len(set(ids)), 46)
        # A data prevalece sobre o ID maior.
        self.assertEqual(ids[-1], 41)

    def test_filtros_preservados_nos_links(self):
        self.adicionar(41, nome='Maria & João', titulo='Dados pessoais', codigo='FILTRO')
        filtros = dict(participante='Maria & João', treinamento='Dados pessoais', codigo='FILTRO')
        html, _ = self.get(page='2', **filtros)
        links = re.findall(r'rel="(prev|next)" href="([^"]+)"', html)
        self.assertEqual(len(links), 2)
        for direcao, link in links:
            parsed = urlsplit(unescape(link))
            self.assertEqual(parsed.path, self.url)
            self.assertEqual(parse_qs(parsed.query), dict(
                {k: [v] for k, v in filtros.items()}, page=['1' if direcao == 'prev' else '3']))
        for chave, valor in filtros.items():
            self.assertIn(f'name="{chave}"', html)
            self.assertIn(f'value="{valor.replace("&", "&amp;")}"', html)

    def test_paginas_invalidas(self):
        for valor in ('', '0', '-1', 'abc', '1.5', ' 1 ', '١', '2147483648', '9'*1000):
            with self.subTest(valor=valor), patch('app.services.consulta_certificados.Session') as session:
                self.assertEqual(self.client.get(self.url, query_string={'page': valor}).status_code, 400)
                session.assert_not_called()
        self.assertEqual(self.client.get(self.url+'?page=1&page=2').status_code, 400)

    def test_pagina_alem_do_total(self):
        self.adicionar(20)
        _, resultado = self.get(page='2147483647')
        self.assertEqual((resultado.pagina, resultado.paginas, len(resultado.itens)), (2, 2, 1))

    def test_filtros_invalidos_e_repetidos(self):
        for chave, limite in (('participante', 150), ('treinamento', 200), ('codigo', 64)):
            for valor in ('X'*(limite+1), '\x00', '\n', '\x7f'):
                with self.subTest(chave=chave, valor=valor), patch('app.services.consulta_certificados.Session') as session:
                    self.assertEqual(self.client.get(self.url, query_string={chave: valor}).status_code, 400)
                    session.assert_not_called()
            self.assertEqual(self.client.get(self.url, query_string=[(chave, 'A'), (chave, 'B')]).status_code, 400)

    def test_espacos_limites_e_tamanho_fixo(self):
        _, resultado = self.get(participante=' Nome histórico ', treinamento='  ', per_page='1000')
        self.assertEqual(resultado.filtros.participante, 'Nome histórico')
        self.assertEqual(resultado.filtros.treinamento, '')
        self.assertEqual(resultado.total, 1)
        for chave, limite in (('participante', 150), ('treinamento', 200), ('codigo', 64)):
            self.assertEqual(self.client.get(self.url, query_string={chave: 'X'*limite}).status_code, 200)

    def test_consulta_limitada_sem_n_mais_um(self):
        self.adicionar(45)
        self.statements.clear()
        _, resultado = self.get()
        consultas = [sql for sql in self.statements if 'certificados_treinamento' in sql]
        self.assertEqual(len(consultas), 2)
        self.assertIn('count(', consultas[0].lower())
        self.assertIn('LIMIT', consultas[1])
        self.assertEqual(len(resultado.itens), 20)
        self.assertFalse(any('JOIN' in sql.upper() or 'participacoes_treinamento' in sql for sql in consultas))
        with self.assertRaises(FrozenInstanceError):
            resultado.itens[0].nome_participante = 'Alterado'

    def test_get_head_sem_escrita_emissao_ou_revalidacao(self):
        antes = self.snapshot()
        self.statements.clear()
        with patch('app.services.certificados.emitir_certificado', side_effect=AssertionError('Emitiu')) as emissor, \
                patch('app.portal_treinamento.routes.emitir_certificado', side_effect=AssertionError('Emitiu')), \
                patch('app.services.certificados._validar_elegibilidade', side_effect=AssertionError('Revalidou')):
            for metodo in ('GET', 'HEAD'):
                for params in ({}, {'participante': 'histórico'}, {'codigo': 'ausente'}):
                    r = self.client.open(self.url, method=metodo, query_string=params)
                    self.assertEqual(r.status_code, 200)
                    if metodo == 'HEAD':
                        self.assertEqual(r.data, b'')
            emissor.assert_not_called()
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements), self.statements)
        self.assertFalse(any('participacoes_treinamento' in sql for sql in self.statements))
        self.assertEqual(self.snapshot(), antes)

    def test_servico_sem_autoflush(self):
        with self.app.app_context():
            c = db.session.get(Colaborador, self.cid)
            c.nome = 'Alteração pendente'
            self.statements.clear()
            resultado = listar_certificados_emitidos()
            self.assertEqual(resultado.itens[0].nome_participante, 'Nome histórico')
            self.assertEqual(len(self.statements), 2)
            self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements))
            self.assertIn(c, db.session.dirty)
            db.session.rollback()

    def test_servico_valida_entradas_antes_de_consultar(self):
        with self.app.app_context(), patch('app.services.consulta_certificados.Session') as session:
            for pagina in (0, -1, True, '1', 1.5, 2147483648):
                with self.subTest(pagina=pagina), self.assertRaises(ValueError):
                    listar_certificados_emitidos(pagina=pagina)
            for filtros in ({'participante': None}, {'treinamento': 42}, {'codigo': 'X'*65}):
                with self.subTest(filtros=filtros), self.assertRaises(ValueError):
                    listar_certificados_emitidos(**filtros)
            session.assert_not_called()

    def test_falha_banco(self):
        with patch('app.certificados_admin.routes.listar_certificados_emitidos', side_effect=SQLAlchemyError('PRIVADO')):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 503)
        self.assertIn('Consulta indisponível', r.get_data(as_text=True))
        self.assertNotIn(b'PRIVADO', r.data)
        self.assertNotIn(b'CODIGO_TESTE', r.data)
        self.assertEqual(r.headers['Cache-Control'], 'no-store')

    def test_menu_visibilidade_e_rota_ativa(self):
        html, _ = self.get()
        self.assertRegex(html, r'href="/certificados/"\s+class="nav-item active"')
        for uid, visivel in ((self.uid, True), (self.operador, False), (self.inativo, False), (None, False)):
            self.autenticar(uid)
            html = self.client.get('/_menu_teste').get_data(as_text=True)
            self.assertEqual('href="/certificados/"' in html, visivel)


if __name__ == '__main__':
    unittest.main()
