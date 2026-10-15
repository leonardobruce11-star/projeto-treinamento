"""Cria tabela de certificados de treinamento.

Revision ID: a193b001c001
Revises: f192d2e4a911
"""

from alembic import op
import sqlalchemy as sa


revision = "a193b001c001"
down_revision = "f192d2e4a911"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "certificados_treinamento",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),

        sa.Column(
            "participacao_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "codigo_verificacao",
            sa.String(length=64),
            nullable=False,
        ),

        sa.Column(
            "nome_participante",
            sa.String(length=150),
            nullable=False,
        ),

        sa.Column(
            "titulo_treinamento",
            sa.String(length=200),
            nullable=False,
        ),

        sa.Column(
            "emitido_em",
            sa.DateTime(timezone=True),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["participacao_id"],
            ["participacoes_treinamento.id"],
        ),

        sa.UniqueConstraint(
            "participacao_id",
            name="uq_certificados_participacao",
        ),

        sa.UniqueConstraint(
            "codigo_verificacao",
            name="uq_certificados_codigo",
        ),
    )


def downgrade():
    op.drop_table("certificados_treinamento")
