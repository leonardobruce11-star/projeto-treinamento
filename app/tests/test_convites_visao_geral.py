"""Página central administrativa: somente leitura, sem transporte ou produção."""
import unittest
import re
from uuid import uuid4
from datetime import datetime, timezone
from dataclasses import replace
from unittest.mock import patch
from flask import g
from sqlalchemy import select
from app.extensions import db
from app.models.participacao_treinamento import ParticipacaoTreinamento as P
from app.models.envio_convite_treinamento import EnvioConviteTreinamento as E
from app.models.tentativa_envio_convite import TentativaEnvioConvite as T
from app.models.confirmacao_convites import ConfirmacaoConvites as R
from app.models.auditoria import Auditoria
from app.models.campanha import Campanha
from app.models.usuario import Usuario
import test_admin_convites as fixture


class VisaoGeralTest(unittest.TestCase):
    postgres = False
    setUp = fixture.AdminConvitesTest.setUp
    tearDown = fixture.AdminConvitesTest.tearDown

    def snapshot(self):
        return {m.__tablename__: [tuple(row) for row in db.session.execute(
            select(m.__table__).order_by(m.id))] for m in [P, E, T, R, Auditoria]}

    def add(self, estado='pendente', sequencia=1):
        agora = datetime.now(timezone.utc)
        envio = E(participacao_id=self.pid, sequencia=sequencia,
            tipo='primeiro_envio' if sequencia == 1 else 'reenvio',
            solicitado_por_usuario_id=self.uid, destinatario='destinatario-original@example.com',
            identificador_confirmacao=uuid4(), estado=estado)
        if estado != 'pendente':
            envio.processamento_iniciado_em = agora if estado != 'cancelado' else None
            envio.finalizado_em = agora if estado != 'em_processamento' else None
        db.session.add(envio); db.session.flush()
        return envio.id

    def overview(self, query=''):
        response = self.client.get('/convites/' + query)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def test_empty_with_existing_participations_disabled_mail_no_mutation(self):
        self.app.extensions['mail_convites_config'] = replace(self.cfg, enabled=False, base_url='', from_address='')
        antes = self.snapshot()
        with patch('socket.create_connection') as network, patch('app.convites.routes.snapshot') as snapshot, \
                patch('app.convites.routes.avaliar') as evaluate:
            page = self.overview()
        self.assertIn('Nenhuma solicitação de convite registrada.', page.get_data(as_text=True))
        self.assertEqual(self.snapshot(), antes)
        network.assert_not_called(); snapshot.assert_not_called(); evaluate.assert_not_called()
        self.assertNotIn('name="escolha"', page.get_data(as_text=True))
        self.assertEqual(page.headers['Cache-Control'], 'no-store')

    def test_all_states_snapshot_recipient_history_and_no_secrets_or_mutations(self):
        for n, estado in enumerate(['pendente', 'em_processamento', 'enviado', 'falha', 'resultado_indeterminado', 'cancelado'], 1):
            self.add(estado, n)
        db.session.commit(); antes = self.snapshot()
        with patch('app.utils.transporte_convites.TransporteFalso.enviar') as send, \
                patch('app.utils.convites_treinamento.reservar_proximo_envio') as reserve, \
                self.assertNoLogs(level='WARNING'):
            page = self.overview()
        text = page.get_data(as_text=True)
        for estado in ['Pendente', 'Em processamento', 'Enviado', 'Falha', 'Resultado indeterminado', 'Cancelado']:
            self.assertIn(estado, text)
        for esperado in ['destinatario-original@example.com', 'Mesmo treinamento', 'Primeiro envio', 'Reenvio', f'/convites/?participacao_id={self.pid}']:
            self.assertIn(esperado, text)
        for segredo in ['TOKEN_SENSIVEL_COMPLETO_NAO_REGISTRAR', '/aprender/t/', 'HASH_NAO_REGISTRAR']:
            self.assertNotIn(segredo, text)
        send.assert_not_called(); reserve.assert_not_called(); self.assertEqual(self.snapshot(), antes)
        # Consulta geral não carrega o token nem objetos completos da participação.
        consultas=[s for s in self.statements if 'envios_convite_treinamento JOIN' in s]
        self.assertTrue(consultas)
        self.assertNotIn('participacoes_treinamento.token', consultas[-1])

    def test_pagination_order_and_no_duplicates(self):
        ids = [self.add(sequencia=n) for n in range(1, 103)]
        db.session.commit()
        first = self.overview().get_data(as_text=True)
        second = self.overview('?pagina=2').get_data(as_text=True)
        def rows(text):
            return [int(i) for i in re.findall(r'<td>(\d+)</td><td>', text)]
        self.assertEqual(rows(first), list(reversed(ids))[0:100])
        self.assertEqual(rows(second), list(reversed(ids))[100:])
        self.assertIn('/convites/?pagina=2', first)
        self.assertIn('Página anterior', second)
        self.assertNotIn('Próxima página', second)

    def test_existing_contexts_preserved_and_invalid_filters(self):
        p = db.session.get(P, self.pid)
        campanha = Campanha(nome='Contexto', treinamento_id=p.treinamento_id)
        db.session.add(campanha); db.session.flush(); p.campanha_id=campanha.id
        cid=campanha.id; tid=p.treinamento_id; db.session.commit()
        for query in [f'?campanha_id={cid}', f'?treinamento_id={tid}', f'?participacao_id={self.pid}']:
            text = self.overview(query).get_data(as_text=True)
            self.assertIn('name="escolha"', text)
            self.assertIn('Enviar convites selecionados', text)
        for query in ['?campanha_id=1&treinamento_id=1', '?participacao_id=', '?treinamento_id=abc', '?campanha_id=0', '?pagina=0']:
            self.assertEqual(self.client.get('/convites/'+query).status_code, 400)
        self.assertEqual(self.client.get('/convites/?participacao_id=999999').status_code, 404)
        # O padrão existente trata pagina não numérica como a primeira página.
        self.overview('?pagina=abc')

    def test_authentication_role_and_inactive_user(self):
        self.overview()
        usuario=db.session.get(Usuario,self.uid);usuario.perfil='operador';db.session.commit()
        self.assertEqual(self.client.get('/convites/').status_code,403)
        usuario.perfil='administrador';usuario.ativo=False;db.session.commit()
        self.assertEqual(self.client.get('/convites/').status_code,403)
        with self.client.session_transaction() as sess:
            sess.pop('_user_id',None)
        g.pop('_login_user',None)
        self.assertEqual(self.client.get('/convites/').status_code,302)

    def test_sidebar_order_active_in_overview_and_context(self):
        self.add();db.session.commit()
        for query in ['',f'?participacao_id={self.pid}']:
            text=self.overview(query).get_data(as_text=True)
            nav=text.split('<nav class="sidebar-nav">')[1].split('</nav>')[0]
            labels=['Treinamentos','Feedback dos treinamentos','Convites','Categorias']
            positions=[nav.index('>'+label+'<') if '>'+label+'<' in nav else nav.index(label) for label in labels]
            self.assertEqual(positions, sorted(positions))
            self.assertRegex(nav,r'href="/convites/"\s+class="nav-item active"')


class VisaoGeralPostgresTest(VisaoGeralTest):
    postgres = True
