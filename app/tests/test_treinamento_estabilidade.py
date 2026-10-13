"""Progresso visual e estabilidade estrutural em bancos isolados."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timezone
from flask_login import LoginManager
from sqlalchemy import event
from app.extensions import db
from app.models.treinamento import Treinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.treinamentos.routes import treinamentos_bp
from app.utils.progresso_treinamento import calcular_progresso
from app.utils.estrutura_treinamento import EstruturaTreinamentoBloqueada, estrutura_bloqueada
import test_portal_treinamento_progresso as fixture


class EstabilidadeTest(unittest.TestCase):
    postgres = False
    tearDownClass = fixture.ProgressoTest.__dict__['tearDownClass']
    request = fixture.ProgressoTest.request
    state = fixture.ProgressoTest.state

    @classmethod
    def setUpClass(cls):
        fixture.ProgressoTest.setUpClass.__func__(cls)
        cls.app.config['LOGIN_DISABLED'] = True
        manager = LoginManager(cls.app)
        manager.user_loader(lambda user_id: None)
        cls.app.register_blueprint(treinamentos_bp)
        # Apenas os destinos da navegação compartilhada; nenhuma rota real é substituída.
        for index, endpoint in enumerate(('dashboard.index', 'departamentos.listar', 'colaboradores.listar',
                                         'grupos.listar', 'cenarios.listar', 'templates_email.listar',
                                         'landing_pages.listar', 'campanhas.listar', 'categorias_treinamento.listar',
                                         'gophish.index', 'auth.logout')):
            cls.app.add_url_rule(f'/_menu/{index}', endpoint=endpoint, view_func=lambda: '')

    def setUp(self):
        fixture.ProgressoTest.setUp(self)
        with self.app.app_context():
            self.tid = db.session.get(ParticipacaoTreinamento, self.pid).treinamento_id
        self.add_url = f'/treinamentos/{self.tid}/modulos/novo'
        self.edit_url = f'/treinamentos/modulos/{self.ids[0]}/editar'

    def module_rows(self):
        with self.app.app_context():
            return [(m.id, m.treinamento_id, m.ordem, m.titulo, m.conteudo) for m in
                    ModuloTreinamento.query.order_by(ModuloTreinamento.id).all()]

    def start(self):
        return self.client.post('/aprender/t/principal/iniciar', headers=self.headers)

    def admin_add(self, client=None, headers=None):
        return (client or self.client).post(self.add_url, headers=headers or self.headers,
                                          data={'titulo': 'Novo', 'conteudo': 'Texto novo', 'ordem': 40})

    def admin_edit(self, **values):
        data = dict(titulo='Título atualizado', conteudo='Conteúdo atualizado', ordem=10)
        data.update(values)
        return self.client.post(self.edit_url, data=data, headers=self.headers)

    def test_first_module_zero_completed(self):
        text = self.request(method='GET').get_data(as_text=True)
        self.assertIn('aria-valuenow="0"', text)
        self.assertIn('0 de 3 módulos concluídos', text)
        self.assertIn('Próximo módulo pendente', text)
        self.assertIsNone(self.state()[0])

    def test_bar_uses_completed_count(self):
        self.request()
        text = self.request(1, 'GET').get_data(as_text=True)
        self.assertIn('aria-valuenow="33"', text)
        self.assertIn('1 de 3 módulos concluídos', text)

    def test_review_keeps_progress(self):
        self.request(); self.request(1)
        before = self.state()
        text = self.request(method='GET').get_data(as_text=True)
        self.assertIn('aria-valuenow="66"', text)
        self.assertIn('Concluído · Em revisão', text)
        self.assertNotIn(f'/modulo/{self.ids[0]}/avancar', text)
        self.assertEqual(self.state(), before)

    def test_three_of_five_review_sixty_percent(self):
        with self.app.app_context():
            db.session.add_all([ModuloTreinamento(treinamento_id=self.tid, titulo=f'Extra{i}', conteudo='Texto', ordem=i)
                                for i in (40, 50)])
            db.session.commit()
        for i in range(3): self.request(i)
        text = self.request(method='GET').get_data(as_text=True)
        self.assertIn('aria-valuenow="60"', text)
        self.assertIn('3 de 5 módulos concluídos', text)

    def test_last_module_completed(self):
        for i in range(3): self.request(i)
        text = self.request(2, 'GET').get_data(as_text=True)
        self.assertIn('aria-valuenow="100"', text)
        self.assertIn('3 de 3 módulos concluídos', text)
        self.assertIn('Ir para avaliação', text)

    def test_single_module_progress(self):
        text = self.request(method='GET', token='unico', mid=self.foreign).get_data(as_text=True)
        self.assertIn('aria-valuenow="0"', text)
        self.request(token='unico', mid=self.foreign)
        text = self.request(method='GET', token='unico', mid=self.foreign).get_data(as_text=True)
        self.assertIn('aria-valuenow="100"', text)

    def test_empty_training_progress(self):
        response = self.client.get('/aprender/t/vazio')
        self.assertEqual(response.status_code, 200)
        self.assertIn('0 de 0 módulos concluídos', response.get_data(as_text=True))
        state = calcular_progresso([], None)
        self.assertEqual(state.percentual_concluido, 0)
        self.assertFalse(state.todos_modulos_concluidos)

    def test_duplicate_order_id_tiebreak(self):
        with self.app.app_context():
            for mid in self.ids: db.session.get(ModuloTreinamento, mid).ordem = 1
            db.session.commit()
            modules = list(reversed(ModuloTreinamento.query.filter_by(treinamento_id=self.tid).all()))
            state = calcular_progresso(modules, self.ids[0])
            self.assertEqual([m.id for m in state.modulos], sorted(self.ids))
            self.assertEqual(state.percentual_concluido, 33)
        self.request()
        self.assertIn('aria-valuenow="33"', self.request(method='GET').get_data(as_text=True))

    def test_initial_page_statuses_and_count(self):
        self.request()
        before = self.state()
        response = self.client.get('/aprender/t/principal')
        text = response.get_data(as_text=True)
        self.assertIn('1 de 3 módulos concluídos', text)
        for label in ('Concluído', 'Próximo módulo pendente', 'Bloqueado'): self.assertIn(label, text)
        self.assertEqual(self.state(), before)

    def test_future_has_no_navigation_link(self):
        text = self.request(method='GET').get_data(as_text=True)
        self.assertNotIn(f'href="/aprender/t/principal/modulo/{self.ids[2]}"', text)
        self.assertEqual(self.request(2, 'GET').status_code, 302)
        self.assertEqual(self.request(2).status_code, 403)

    def test_content_edit_allowed_after_start(self):
        self.start()
        before = self.state()
        self.assertEqual(self.admin_edit().status_code, 302)
        row = next(row for row in self.module_rows() if row[0] == self.ids[0])
        self.assertEqual(row[2:], (10, 'Título atualizado', 'Conteúdo atualizado'))
        self.assertEqual(self.state(), before)

    def test_reorder_blocked_atomic_after_start(self):
        self.start()
        before = self.module_rows(), self.state()
        self.assertEqual(self.admin_edit(ordem=99).status_code, 302)
        self.assertEqual((self.module_rows(), self.state()), before)
        with self.client.session_transaction() as session:
            self.assertIn('Não é permitido adicionar, remover ou reordenar', session['_flashes'][-1][1])

    def test_add_blocked_after_start(self):
        self.start()
        before = self.module_rows(), self.state()
        self.assertEqual(self.client.get(self.add_url).status_code, 302)
        self.assertEqual(self.admin_add().status_code, 302)
        self.assertEqual((self.module_rows(), self.state()), before)

    def test_delete_blocked_after_start(self):
        self.start()
        before = self.module_rows(), self.state()
        with self.app.app_context():
            db.session.delete(db.session.get(ModuloTreinamento, self.ids[2]))
            with self.assertRaises(EstruturaTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual((self.module_rows(), self.state()), before)

    def test_orphan_removal_blocked(self):
        self.start()
        before = self.module_rows()
        with self.app.app_context():
            training = db.session.get(Treinamento, self.tid)
            training.modulos.remove(db.session.get(ModuloTreinamento, self.ids[2]))
            with self.assertRaises(EstruturaTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.module_rows(), before)

    def test_structural_changes_allowed_before_start(self):
        # A participação já existe, mas ainda não começou. Tempo ativo não é autorização.
        self.assertEqual(self.admin_add().status_code, 302)
        self.assertEqual(self.admin_edit(ordem=35).status_code, 302)
        with self.app.app_context():
            self.assertFalse(estrutura_bloqueada(self.tid, db.session))
            db.session.delete(db.session.get(ModuloTreinamento, self.ids[2]))
            db.session.commit()
        self.assertNotIn(self.ids[2], [row[0] for row in self.module_rows()])
        self.assertIsNone(self.state()[1])

    def test_cursor_legacy_freezes_structure(self):
        with self.app.app_context():
            db.session.get(ParticipacaoTreinamento, self.pid).ultimo_modulo_id = self.ids[0]
            db.session.commit()
        before = self.module_rows()
        self.admin_add()
        self.assertEqual(self.module_rows(), before)

    def test_conclusion_legacy_freezes_structure(self):
        with self.app.app_context():
            db.session.get(ParticipacaoTreinamento, self.pid).concluido_em = datetime.now(timezone.utc)
            db.session.commit()
        before = self.module_rows()
        self.admin_edit(ordem=2)
        self.assertEqual(self.module_rows(), before)

    def test_move_module_blocked(self):
        self.start()
        before = self.module_rows()
        with self.app.app_context():
            foreign = db.session.get(ModuloTreinamento, self.foreign)
            db.session.get(ModuloTreinamento, self.ids[2]).treinamento_id = foreign.treinamento_id
            with self.assertRaises(EstruturaTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.module_rows(), before)

    def test_id_tiebreak_cannot_be_changed_after_start(self):
        self.start()
        before = self.module_rows()
        with self.app.app_context():
            db.session.get(ModuloTreinamento, self.ids[2]).id = 9999
            with self.assertRaises(EstruturaTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.module_rows(), before)

    def test_admin_order_readonly_message(self):
        self.start()
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('readonly aria-describedby="estrutura-bloqueada"', response.get_data(as_text=True))
        self.assertIn('Título e conteúdo continuam editáveis', response.get_data(as_text=True))

    def test_templates_compile(self):
        for template in ('portal_treinamento/inicio.html', 'portal_treinamento/modulo.html',
                         'treinamentos/editar_modulo.html'):
            self.app.jinja_env.get_template(template)


class EstabilidadePostgresTest(EstabilidadeTest):
    postgres = True

    def race_start_and_add(self, start_first, advance=False, order=40, expected_portal=302):
        locked, contender, release = Event(), Event(), Event()
        with self.app.app_context(): engine = db.engine
        target = 'FOR KEY SHARE' if start_first else 'FOR UPDATE'
        other = 'FOR UPDATE' if start_first else 'FOR KEY SHARE'
        def after_execute(conn, cursor, statement, params, context, many):
            if 'FROM treinamentos' in statement and target in statement and not locked.is_set():
                locked.set()
                if not release.wait(timeout=10): raise TimeoutError('Não liberou transação de teste')
        def before_execute(conn, cursor, statement, params, context, many):
            if 'FROM treinamentos' in statement and other in statement: contender.set()
        def start_worker():
            client = self.app.test_client()
            headers = {'X-CSRFToken': client.get('/_csrf').json['token']}
            url = f'/aprender/t/principal/modulo/{self.ids[0]}/avancar' if advance else '/aprender/t/principal/iniciar'
            return client.post(url, headers=headers).status_code
        def admin_worker():
            client = self.app.test_client()
            headers = {'X-CSRFToken': client.get('/_csrf').json['token']}
            return client.post(self.add_url, headers=headers, data={'titulo': 'Novo', 'conteudo': 'Texto novo', 'ordem': order}).status_code
        event.listen(engine, 'before_cursor_execute', before_execute)
        event.listen(engine, 'after_cursor_execute', after_execute)
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            first = pool.submit(start_worker if start_first else admin_worker)
            self.assertTrue(locked.wait(timeout=10))
            second = pool.submit(admin_worker if start_first else start_worker)
            self.assertTrue(contender.wait(timeout=10))
            release.set()
            self.assertEqual(first.result(timeout=10), expected_portal if start_first else 302)
            self.assertEqual(second.result(timeout=10), 302 if start_first else expected_portal)
        finally:
            release.set()
            pool.shutdown(wait=True)
            event.remove(engine, 'before_cursor_execute', before_execute)
            event.remove(engine, 'after_cursor_execute', after_execute)

    def test_start_wins_add_is_blocked(self):
        before = self.module_rows()
        self.race_start_and_add(start_first=True)
        self.assertEqual(self.module_rows(), before)
        self.assertIsNotNone(self.state()[1])

    def test_structure_wins_start_sees_new_sequence(self):
        self.race_start_and_add(start_first=False)
        self.assertEqual(len([row for row in self.module_rows() if row[1] == self.tid]), 4)
        self.assertIsNotNone(self.state()[1])
        self.assertIn('0 de 4 módulos concluídos', self.request(method='GET').get_data(as_text=True))

    def test_first_advance_wins_add_is_blocked(self):
        before = self.module_rows()
        self.race_start_and_add(start_first=True, advance=True)
        self.assertEqual(self.module_rows(), before)
        self.assertEqual(self.state()[0], self.ids[0])

    def test_structure_wins_advance_checks_new_first(self):
        self.race_start_and_add(start_first=False, advance=True, order=5, expected_portal=403)
        self.assertIsNone(self.state()[0])
        self.assertIsNone(self.state()[1])
        self.assertEqual(len([row for row in self.module_rows() if row[1] == self.tid]), 4)


if __name__ == '__main__': unittest.main()
