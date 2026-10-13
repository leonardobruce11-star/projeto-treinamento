"""Adiciona estrutura de tempo ativo por participação e módulo.

Revision ID: b182a6d4e901
Revises: 75449238a72e
"""
from alembic import op
import sqlalchemy as sa


revision = "b182a6d4e901"
down_revision = "75449238a72e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tempos_treinamento",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("participacao_id", sa.Integer(), nullable=False),
        sa.Column("modulo_id", sa.Integer(), nullable=False),
        sa.Column("segundos_ativos", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ultima_atividade_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["participacao_id"], ["participacoes_treinamento.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["modulo_id"], ["modulos_treinamento.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "participacao_id", "modulo_id",
            name="uq_tempos_treinamento_participacao_modulo",
        ),
        sa.CheckConstraint(
            "segundos_ativos >= 0",
            name="ck_tempos_treinamento_segundos_ativos_nao_negativos",
        ),
    )


def downgrade():
    op.drop_table("tempos_treinamento")
