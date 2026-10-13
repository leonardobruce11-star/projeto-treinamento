"""Upgrade/downgrade completos em schema PostgreSQL exclusivo de testes.

Defina ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL para um banco descartável.
Nenhuma configuração ou conexão do DEV é usada.
"""
import os
from pathlib import Path
import unittest
from uuid import uuid4
from flask import Flask
from flask_migrate import Migrate
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy.schema import CreateSchema, DropSchema
from app.extensions import db
import app.models


class AprovacaoMigrationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('ONE_APROVACAO_MIGRATION_TEST_DATABASE_URL')
        if not url:
            raise unittest.SkipTest('PostgreSQL temporário de migration não configurado')
        cls.admin = create_engine(url)
        if cls.admin.dialect.name != 'postgresql':
            raise RuntimeError('A migration deve ser validada em PostgreSQL')
        cls.schema = 'aprovacao_migration_' + uuid4().hex
        with cls.admin.begin() as c:
            c.execute(CreateSchema(cls.schema))
        cls.app = Flask(__name__)
        cls.app.config.update(SQLALCHEMY_DATABASE_URI=url,
                             SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {
                                 'options': '-csearch_path=' + cls.schema}})
        db.init_app(cls.app)
        Migrate(cls.app, db)
        directory = Path(__file__).resolve().parents[1] / 'migrations'
        cls.config = Config(str(directory / 'alembic.ini'))
        cls.config.set_main_option('script_location', str(directory))

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.admin.begin() as c:
            c.execute(DropSchema(cls.schema, cascade=True))
        cls.admin.dispose()

    def test_upgrade_constraints_preservacao_downgrade_e_reupgrade(self):
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), ['f192d2e4a911'])
        with self.app.app_context():
            command.upgrade(self.config, 'a187d7e4a906')
            with db.engine.begin() as c:
                c.execute(text("INSERT INTO treinamentos (titulo,ativo,criado_em) "
                               "VALUES ('Histórico',true,CURRENT_TIMESTAMP)"))
                antes = tuple(c.execute(text('SELECT id,titulo,ativo,criado_em FROM treinamentos')).one())
            command.upgrade(self.config, 'b198a1e4a907')
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                                 'b198a1e4a907')
                depois = c.execute(text('SELECT id,titulo,ativo,criado_em,nota_minima_percentual FROM treinamentos')).one()
                self.assertEqual(tuple(depois[:4]), antes)
                self.assertIsNone(depois[4])
                coluna = next(x for x in inspect(c).get_columns('treinamentos')
                              if x['name'] == 'nota_minima_percentual')
                self.assertTrue(coluna['nullable'])
                self.assertIsNone(coluna['default'])
                self.assertEqual((coluna['type'].precision, coluna['type'].scale), (5, 2))
                self.assertIn('ck_treinamentos_nota_minima_percentual',
                              [x['name'] for x in inspect(c).get_check_constraints('treinamentos')])
                contexto = MigrationContext.configure(c, opts={'include_object':
                    lambda obj, name, tipo, reflected, compare_to:
                        name == 'treinamentos' if tipo == 'table' else True})
                self.assertEqual(compare_metadata(contexto, db.metadata), [])
            for valor in ('0', '-1', '100.01', '101', 'NaN', 'Infinity', '-Infinity'):
                with self.subTest(invalido=valor):
                    with self.assertRaises((IntegrityError, DataError)):
                        with db.engine.begin() as c:
                            c.execute(text('UPDATE treinamentos SET nota_minima_percentual=:valor'),
                                      {'valor': valor})
            for valor in ('0.01', '70.01', '100.00'):
                with self.subTest(valido=valor):
                    with db.engine.begin() as c:
                        c.execute(text('UPDATE treinamentos SET nota_minima_percentual=:valor'), {'valor': valor})
            with self.assertRaisesRegex(RuntimeError, 'Downgrade bloqueado'):
                command.downgrade(self.config, 'a187d7e4a906')
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                                 'b198a1e4a907')
                self.assertEqual(str(c.execute(text('SELECT nota_minima_percentual FROM treinamentos')).scalar_one()),
                                 '100.00')
            with db.engine.begin() as c:
                c.execute(text('UPDATE treinamentos SET nota_minima_percentual=NULL'))
            command.downgrade(self.config, 'a187d7e4a906')
            with db.engine.connect() as c:
                self.assertNotIn('nota_minima_percentual', [x['name'] for x in inspect(c).get_columns('treinamentos')])
                self.assertEqual(tuple(c.execute(text('SELECT id,titulo,ativo,criado_em FROM treinamentos')).one()), antes)
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                                 'a187d7e4a906')
            command.upgrade(self.config, 'b198a1e4a907')
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
                                 'b198a1e4a907')
                self.assertIsNone(c.execute(text('SELECT nota_minima_percentual FROM treinamentos')).scalar_one())
