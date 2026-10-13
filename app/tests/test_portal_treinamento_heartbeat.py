"""Integração real: definir ONE_HEARTBEAT_TEST_DATABASE_URL para banco de testes.

Cria um schema exclusivo e o remove ao final. Não usa create_app() nem .env.
Executar: python -m unittest discover -s tests -p 'test_portal_treinamento_heartbeat.py' -v
"""
import importlib.util
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy.exc import SQLAlchemyError

from flask import Flask, jsonify
from flask_wtf.csrf import generate_csrf
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models
from app.extensions import db, csrf
from app.models.departamento import Departamento
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.resultado_quiz import ResultadoQuiz

# Carrega exatamente o arquivo ao lado desta suite; também permite validar patch.
spec = importlib.util.spec_from_file_location(
    "heartbeat_routes_under_test",
    Path(__file__).resolve().parents[1] / "app/portal_treinamento/routes.py",
)
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)


class HeartbeatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get("ONE_HEARTBEAT_TEST_DATABASE_URL")
        if not url:
            raise RuntimeError("Defina ONE_HEARTBEAT_TEST_DATABASE_URL para PostgreSQL de testes")
        cls.admin = create_engine(url)
        if cls.admin.dialect.name != "postgresql":
            raise RuntimeError("Os testes exigem PostgreSQL real")
        cls.schema = "heartbeat_test_" + uuid4().hex
        with cls.admin.begin() as connection:
            connection.execute(CreateSchema(cls.schema))
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True, SECRET_KEY="heartbeat-isolated-test-key",
            SQLALCHEMY_DATABASE_URI=url,
            SQLALCHEMY_ENGINE_OPTIONS={
                "connect_args": {"options": "-csearch_path=" + cls.schema},
            },
            WTF_CSRF_ENABLED=True,
        )
        db.init_app(cls.app)
        csrf.init_app(cls.app)
        cls.app.register_blueprint(routes.portal_treinamento_bp)

        @cls.app.get("/_test_csrf")
        def csrf_for_test():
            return jsonify(token=generate_csrf())

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.admin.begin() as connection:
            connection.execute(DropSchema(cls.schema, cascade=True))
        cls.admin.dispose()

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            department = Departamento(nome="Testes")
            db.session.add(department)
            db.session.flush()
            person = Colaborador(nome="Teste", email="teste@example.invalid",
                                 departamento_id=department.id)
            training = Treinamento(titulo="Primeiro", ativo=True)
            other = Treinamento(titulo="Outro", ativo=True)
            db.session.add_all([person, training, other])
            db.session.flush()
            module = ModuloTreinamento(treinamento_id=training.id, titulo="M1", conteudo="Texto")
            foreign = ModuloTreinamento(treinamento_id=other.id, titulo="M2", conteudo="Texto")
            db.session.add_all([module, foreign])
            db.session.flush()
            participation = ParticipacaoTreinamento(token="token-principal",
                colaborador_id=person.id, treinamento_id=training.id)
            other_participation = ParticipacaoTreinamento(token="token-outra",
                colaborador_id=person.id, treinamento_id=training.id)
            db.session.add_all([participation, other_participation])
            db.session.commit()
            self.pid, self.other_pid = participation.id, other_participation.id
            self.mid, self.foreign_mid = module.id, foreign.id
            self.training_id = training.id
        self.client = self.app.test_client()
        self.headers = {"X-CSRFToken": self.client.get("/_test_csrf").json["token"]}
        self.url = f"/aprender/t/token-principal/modulo/{self.mid}/heartbeat"

    def post(self, seconds=15, **kwargs):
        return self.client.post(self.url, json={"segundos": seconds}, headers=self.headers, **kwargs)

    def row(self):
        return db.session.execute(db.select(TempoTreinamento).where(
            TempoTreinamento.participacao_id == self.pid,
            TempoTreinamento.modulo_id == self.mid,
        )).scalar_one()

    def seed(self, age=15, total=7, null=False):
        with self.app.app_context():
            now = db.session.execute(db.select(db.func.statement_timestamp())).scalar_one()
            activity = None if null else db.session.execute(db.select(
                db.func.statement_timestamp() - text(f"interval '{age} seconds'")
            )).scalar_one()
            db.session.add(TempoTreinamento(participacao_id=self.pid, modulo_id=self.mid,
                segundos_ativos=total, ultima_atividade_em=activity,
                criado_em=now, atualizado_em=activity or now))
            db.session.commit()
            return activity

    def test_route_post_only(self):
        rule = next(rule for rule in self.app.url_map.iter_rules()
                    if rule.endpoint == "portal_treinamento.heartbeat")
        self.assertEqual(rule.rule, "/aprender/t/<token>/modulo/<int:modulo_id>/heartbeat")
        self.assertEqual(rule.methods - {"OPTIONS"}, {"POST"})
        for method in ("GET", "PUT", "PATCH", "DELETE"):
            self.assertEqual(self.client.open(self.url, method=method, headers=self.headers).status_code, 405)

    def test_first_heartbeat_zero(self):
        response = self.post(30)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, dict(sucesso=True, incremento_efetivo=0, segundos_ativos=0))
        with self.app.app_context():
            row = self.row()
            self.assertIsNotNone(row.ultima_atividade_em.tzinfo)
            self.assertEqual(row.ultima_atividade_em, row.atualizado_em)
            self.assertEqual(row.criado_em, row.atualizado_em)

    def test_elapsed_limits_increment_and_preserves_creation(self):
        old = self.seed(age=15)
        with self.app.app_context():
            created = self.row().criado_em
        response = self.post(30)
        self.assertEqual(response.json["incremento_efetivo"], 15)
        self.assertEqual(response.json["segundos_ativos"], 22)
        with self.app.app_context():
            row = self.row()
            self.assertEqual(row.criado_em, created)
            self.assertGreater(row.ultima_atividade_em, old)
            self.assertGreater(row.atualizado_em, old)
            self.assertEqual(TempoTreinamento.query.count(), 1)

    def test_client_request_limits_increment(self):
        self.seed(age=15)
        self.assertEqual(self.post(4).json["incremento_efetivo"], 4)

    def test_minimum_increment(self):
        self.seed(age=15)
        self.assertEqual(self.post(1).json["incremento_efetivo"], 1)

    def test_commit_failure_rolls_back(self):
        old = self.seed(age=15)
        with patch.object(db.session, "commit", side_effect=SQLAlchemyError("test")):
            response = self.post(30)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json, dict(sucesso=False, erro="falha_ao_registrar"))
        with self.app.app_context():
            self.assertEqual(self.row().segundos_ativos, 7)
            self.assertEqual(self.row().ultima_atividade_em, old)

    def test_long_interval_capped(self):
        self.seed(age=90)
        self.assertEqual(self.post(30).json["incremento_efetivo"], 30)

    def test_fraction_is_floored(self):
        self.seed(age=4.2)
        self.assertEqual(self.post(30).json["incremento_efetivo"], 4)

    def test_immediate_repetitions_zero(self):
        self.post(30)
        for _ in range(8):
            self.assertEqual(self.post(30).json["incremento_efetivo"], 0)
        with self.app.app_context():
            self.assertEqual(self.row().segundos_ativos, 0)

    def test_future_reference_never_regresses(self):
        future = self.seed(age=-60)
        self.assertEqual(self.post(30).json["incremento_efetivo"], 0)
        with self.app.app_context():
            self.assertEqual(self.row().ultima_atividade_em, future)
            self.assertEqual(self.row().segundos_ativos, 7)

    def test_null_reference_only_established(self):
        self.seed(null=True)
        self.assertEqual(self.post(30).json["incremento_efetivo"], 0)
        with self.app.app_context():
            self.assertEqual(self.row().segundos_ativos, 7)
            self.assertIsNotNone(self.row().ultima_atividade_em)

    def test_invalid_and_foreign_modules(self):
        for module in (999999, self.foreign_mid):
            url = f"/aprender/t/token-principal/modulo/{module}/heartbeat"
            response = self.client.post(url, json={"segundos": 15}, headers=self.headers)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json["erro"], "modulo_invalido")
        with self.app.app_context():
            self.assertEqual(TempoTreinamento.query.count(), 0)

    def test_invalid_token_and_inactive_training(self):
        response = self.client.post(self.url.replace("token-principal", "desconhecido"),
            json={"segundos": 15}, headers=self.headers)
        self.assertEqual(response.status_code, 404)
        with self.app.app_context():
            db.session.get(Treinamento, self.training_id).ativo = False
            db.session.commit()
        self.assertEqual(self.post().status_code, 404)
        with self.app.app_context():
            self.assertEqual(TempoTreinamento.query.count(), 0)

    def test_invalid_increments_do_not_change_record(self):
        old = self.seed()
        for seconds in (None, True, False, "15", 15.0, 1.5, 0, -1, 31, 999999999999999999):
            with self.subTest(seconds=seconds):
                response = self.post(seconds)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["erro"], "incremento_invalido")
        for body in ({}, [], 15, "texto"):
            self.assertEqual(self.client.post(self.url, json=body, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(self.url, data='{', content_type='application/json',
                                         headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(self.url, data='segundos=15', headers=self.headers).status_code, 400)
        with self.app.app_context():
            self.assertEqual(self.row().segundos_ativos, 7)
            self.assertEqual(self.row().ultima_atividade_em, old)

    def test_csrf_required(self):
        for headers in ({}, {"X-CSRFToken": "invalido"}):
            response = self.client.post(self.url, json={"segundos": 15}, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json["erro"], "csrf_invalido")
        with self.app.app_context():
            self.assertEqual(TempoTreinamento.query.count(), 0)

    def test_other_csrf_errors_keep_html(self):
        response = self.client.post("/aprender/t/token-principal/iniciar")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "text/html")

    def test_identity_derived_from_token_and_progress_unchanged(self):
        with self.app.app_context():
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            timestamp = db.session.execute(db.select(db.func.statement_timestamp())).scalar_one()
            p.iniciado_em = timestamp
            p.concluido_em = timestamp
            p.ultimo_modulo_id = self.mid
            db.session.add(ResultadoQuiz(participacao_id=self.pid, total_questoes=2,
                total_acertos=1, percentual=50, concluido_em=timestamp))
            db.session.commit()
            before = (p.iniciado_em, p.concluido_em, p.ultimo_modulo_id)
        response = self.client.post(self.url,
            json={"segundos": 15, "participacao_id": self.other_pid, "modulo_id": self.foreign_mid},
            headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(self.row().participacao_id, self.pid)
            p = db.session.get(ParticipacaoTreinamento, self.pid)
            self.assertEqual((p.iniciado_em, p.concluido_em, p.ultimo_modulo_id), before)
            self.assertEqual(ResultadoQuiz.query.count(), 1)
            quiz = ResultadoQuiz.query.one()
            self.assertEqual((quiz.total_questoes, quiz.total_acertos, quiz.percentual), (2, 1, 50))

    def test_blocked_module_no_creation_or_update(self):
        with self.app.app_context():
            second = ModuloTreinamento(treinamento_id=self.training_id,
                titulo="Bloqueado", conteudo="Texto", ordem=2)
            db.session.get(ModuloTreinamento, self.mid).ordem = 1
            db.session.add(second)
            db.session.commit()
            second_id = second.id
        url = f"/aprender/t/token-principal/modulo/{second_id}/heartbeat"
        for existing in (False, True):
            if existing:
                with self.app.app_context():
                    db.session.add(TempoTreinamento(participacao_id=self.pid,
                        modulo_id=second_id, segundos_ativos=7))
                    db.session.commit()
            response = self.client.post(url, json={"segundos": 15}, headers=self.headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json["erro"], "modulo_bloqueado")
            with self.app.app_context():
                rows = TempoTreinamento.query.all()
                self.assertEqual(len(rows), int(existing))
                if existing:
                    self.assertEqual(rows[0].segundos_ativos, 7)
                    self.assertIsNone(rows[0].ultima_atividade_em)
        with self.app.app_context():
            db.session.get(ParticipacaoTreinamento, self.pid).ultimo_modulo_id = self.mid
            db.session.commit()
        self.assertEqual(self.client.post(url, json={"segundos": 15}, headers=self.headers).status_code, 200)
        self.assertEqual(self.post().status_code, 200)  # revisão continua permitida

    def test_original_token_privacy_on_success_errors_and_redirect(self):
        for response in (self.post(), self.post(0),
                         self.client.post(self.url, json={"segundos": 15}),
                         self.client.post(self.url.replace('token-principal', 'desconhecido'),
                                          json={'segundos': 15}, headers=self.headers),
                         self.client.post('/aprender/t/token-principal/iniciar', headers=self.headers)):
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")

    def parallel_posts(self):
        count = 6
        barrier = Barrier(count)
        def worker(_):
            with self.app.test_client() as client:
                headers = {"X-CSRFToken": client.get("/_test_csrf").json["token"]}
                barrier.wait(timeout=10)
                response = client.post(self.url, json={"segundos": 30}, headers=headers)
                return response.status_code, response.json
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(worker, range(count)))

    def test_concurrent_first_creation(self):
        results = self.parallel_posts()
        self.assertTrue(all(status == 200 for status, _ in results), results)
        self.assertEqual(sum(body["incremento_efetivo"] for _, body in results), 0)
        with self.app.app_context():
            self.assertEqual(TempoTreinamento.query.count(), 1)
            self.assertEqual(self.row().segundos_ativos, 0)

    def test_concurrent_updates_count_elapsed_once(self):
        self.seed(age=15)
        results = self.parallel_posts()
        self.assertTrue(all(status == 200 for status, _ in results), results)
        increments = [body["incremento_efetivo"] for _, body in results]
        self.assertEqual(sum(increments), 15)
        self.assertEqual(sorted(increments), [0, 0, 0, 0, 0, 15])
        with self.app.app_context():
            self.assertEqual(TempoTreinamento.query.count(), 1)
            self.assertEqual(self.row().segundos_ativos, 22)


if __name__ == "__main__":
    unittest.main()
