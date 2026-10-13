"""19.1B: cadeia real PostgreSQL com histórico, dados novos e rollback seguro."""
from decimal import Decimal
import unittest
from alembic import command
from alembic.script import ScriptDirectory
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text, inspect
from sqlalchemy.exc import IntegrityError, DataError
from app.extensions import db
import test_aprovacao_migration as fixture


class PersistenciaMigrationPostgresTest(unittest.TestCase):
    setUpClass = fixture.AprovacaoMigrationPostgresTest.__dict__['setUpClass']
    tearDownClass = fixture.AprovacaoMigrationPostgresTest.__dict__['tearDownClass']

    def test_historico_constraints_downgrade_protegido_e_reupgrade(self):
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), ['f192d2e4a911'])
        with self.app.app_context():
            command.upgrade(self.config, 'b198a1e4a907')
            with db.engine.begin() as c:
                c.execute(text("INSERT INTO departamentos(id,nome,ativo) VALUES (1,'Histórico',true)"))
                c.execute(text("INSERT INTO colaboradores(id,nome,email,departamento_id,ativo,criado_em) VALUES (1,'Histórico','historico@example.invalid',1,true,CURRENT_TIMESTAMP)"))
                c.execute(text("INSERT INTO treinamentos(id,titulo,ativo,criado_em,nota_minima_percentual) VALUES (1,'Histórico',true,CURRENT_TIMESTAMP,20)"))
                c.execute(text("INSERT INTO modulos_treinamento(id,treinamento_id,titulo,conteudo,ordem) VALUES (1,1,'Módulo','Texto',1)"))
                for i in range(1, 5):
                    c.execute(text("INSERT INTO participacoes_treinamento(id,token,colaborador_id,treinamento_id,iniciado_em,concluido_em,ultimo_modulo_id,criado_em) VALUES (:id,:token,1,1,:inicio,:fim,:modulo,CURRENT_TIMESTAMP)"),
                              {'id': i, 'token': f'historico-{i}', 'inicio': '2026-01-01T12:00:00Z' if i < 4 else None,
                               'fim': '2026-01-02T12:00:00Z' if i < 4 else None, 'modulo': 1 if i < 4 else None})
                c.execute(text("INSERT INTO resultados_quiz(id,participacao_id,total_questoes,total_acertos,percentual,concluido_em) VALUES (1,1,6,4,66.67,CURRENT_TIMESTAMP),(2,2,0,-1,500,CURRENT_TIMESTAMP)"))
                c.execute(text("INSERT INTO feedbacks_treinamento(participacao_id,avaliacao_geral,avaliacao_conteudo,avaliacao_clareza,avaliacao_utilidade,comentario) VALUES (1,4,4,4,4,'Preservar')"))
                c.execute(text("INSERT INTO tempos_treinamento(participacao_id,modulo_id,segundos_ativos,criado_em,atualizado_em) VALUES (1,1,42,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                tabelas = ('treinamentos', 'participacoes_treinamento', 'resultados_quiz', 'feedbacks_treinamento', 'tempos_treinamento')
                antes = {t: [dict(r) for r in c.execute(text('SELECT * FROM '+t+' ORDER BY id')).mappings()] for t in tabelas}
                unique = inspect(c).get_unique_constraints('resultados_quiz')
            command.upgrade(self.config, 'head')

            def verificar_historico():
                with db.engine.connect() as c:
                    for t in tabelas:
                        colunas = ','.join(antes[t][0].keys())
                        depois = [dict(r) for r in c.execute(text('SELECT '+colunas+' FROM '+t+' ORDER BY id')).mappings()]
                        self.assertEqual(depois, antes[t], t)
                    self.assertEqual(c.execute(text('SELECT count(*) FROM participacoes_treinamento WHERE aprovado_em IS NOT NULL')).scalar_one(), 0)
                    self.assertEqual(c.execute(text('SELECT count(*) FROM resultados_quiz WHERE aprovado IS NOT NULL OR percentual_minimo_aplicado IS NOT NULL OR minimo_acertos_aplicado IS NOT NULL')).scalar_one(), 0)
            verificar_historico()
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), 'f192d2e4a911')
                pc = next(x for x in inspect(c).get_columns('participacoes_treinamento') if x['name'] == 'aprovado_em')
                self.assertTrue(pc['nullable']); self.assertIsNone(pc['default']); self.assertTrue(pc['type'].timezone)
                for name in ('percentual_minimo_aplicado', 'minimo_acertos_aplicado', 'aprovado'):
                    col = next(x for x in inspect(c).get_columns('resultados_quiz') if x['name'] == name)
                    self.assertTrue(col['nullable']); self.assertIsNone(col['default'])
                nc = next(x for x in inspect(c).get_columns('resultados_quiz') if x['name'] == 'percentual_minimo_aplicado')
                self.assertEqual((nc['type'].precision, nc['type'].scale), (5, 2))
                for original in unique:
                    if original['column_names'] == ['participacao_id']:
                        self.assertNotIn(original, inspect(c).get_unique_constraints('resultados_quiz'))
                    else:
                        self.assertIn(original, inspect(c).get_unique_constraints('resultados_quiz'))
                contexto = MigrationContext.configure(c, opts={'include_object':
                    lambda obj, name, tipo, reflected, compare_to:
                        name in ('treinamentos', 'participacoes_treinamento', 'resultados_quiz') if tipo == 'table' else True})
                self.assertEqual(compare_metadata(contexto, db.metadata), [])

            sql = text('UPDATE resultados_quiz SET percentual_minimo_aplicado=:percentual, minimo_acertos_aplicado=:minimo, aprovado=:aprovado WHERE id=1')
            invalidos = [(None, 5, False), (80, None, False), (80, 5, None),
                         (80, None, None), (None, 5, None), (None, None, False),
                         (0, 5, False), (-1, 5, False), (101, 5, False),
                         ('NaN', 5, False), ('Infinity', 5, False),
                         (80, 0, False), (80, 7, False), (80, 4, True),
                         (80, 6, False), (80, 5, True)]
            for percentual, minimo, aprovado in invalidos:
                with self.subTest(invalido=(percentual, minimo, aprovado)):
                    with self.assertRaises((IntegrityError, DataError)):
                        with db.engine.begin() as c:
                            c.execute(sql, dict(percentual=percentual, minimo=minimo, aprovado=aprovado))
            with db.engine.begin() as c:
                c.execute(sql, dict(percentual=80, minimo=5, aprovado=False))
                c.execute(text('UPDATE treinamentos SET nota_minima_percentual=100 WHERE id=1'))
            with db.engine.connect() as c:
                self.assertEqual(tuple(c.execute(text('SELECT percentual_minimo_aplicado,minimo_acertos_aplicado,aprovado FROM resultados_quiz WHERE id=1')).one()), (Decimal('80.00'), 5, False))

            def bloqueado():
                with self.assertRaisesRegex(RuntimeError, 'Downgrade bloqueado'):
                    command.downgrade(self.config, 'b198a1e4a907')
                with db.engine.connect() as c:
                    self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), 'f192d2e4a911')
                    self.assertIn('aprovado', [x['name'] for x in inspect(c).get_columns('resultados_quiz')])
            bloqueado()  # False é dado novo, também precisa proteção.
            with db.engine.begin() as c:
                c.execute(sql, dict(percentual=None, minimo=None, aprovado=None))
                c.execute(text("UPDATE participacoes_treinamento SET aprovado_em='2026-02-01T15:30:00+00' WHERE id=3"))
            bloqueado()  # Sem quiz, só o timestamp já impede downgrade.
            with db.engine.begin() as c:
                c.execute(text('UPDATE participacoes_treinamento SET aprovado_em=NULL WHERE id=3'))
                c.execute(text('UPDATE resultados_quiz SET total_acertos=5,percentual=83.33,percentual_minimo_aplicado=80,minimo_acertos_aplicado=5,aprovado=true WHERE id=1'))
            bloqueado()  # Decisão True também é preservada.
            with db.engine.begin() as c:
                c.execute(text('UPDATE resultados_quiz SET total_acertos=4,percentual=66.67,percentual_minimo_aplicado=NULL,minimo_acertos_aplicado=NULL,aprovado=NULL WHERE id=1'))
                c.execute(text('UPDATE treinamentos SET nota_minima_percentual=20 WHERE id=1'))
            verificar_historico()
            command.downgrade(self.config, 'b198a1e4a907')
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), 'b198a1e4a907')
                self.assertNotIn('aprovado_em', [x['name'] for x in inspect(c).get_columns('participacoes_treinamento')])
                self.assertNotIn('aprovado', [x['name'] for x in inspect(c).get_columns('resultados_quiz')])
                self.assertEqual(inspect(c).get_unique_constraints('resultados_quiz'), unique)
                for t in tabelas:
                    self.assertEqual([dict(r) for r in c.execute(text('SELECT * FROM '+t+' ORDER BY id')).mappings()], antes[t])
            command.upgrade(self.config, 'head')
            verificar_historico()
