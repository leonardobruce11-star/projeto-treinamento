"""Feedback único e imutável por participação.

Revision ID: c185b7e4a902
Revises: b182a6d4e901
"""
from alembic import op
import sqlalchemy as sa

revision = "c185b7e4a902"
down_revision = "b182a6d4e901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedbacks_treinamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("participacao_id", sa.Integer(), nullable=False),
        *(sa.Column(campo, sa.SmallInteger(), nullable=False) for campo in (
            "avaliacao_geral", "avaliacao_conteudo", "avaliacao_clareza", "avaliacao_utilidade")),
        sa.Column("comentario", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["participacao_id"], ["participacoes_treinamento.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("participacao_id", name="uq_feedback_participacao"),
        *(sa.CheckConstraint(f"{campo} BETWEEN 1 AND 5", name=f"ck_feedback_{campo}")
          for campo in ("avaliacao_geral", "avaliacao_conteudo", "avaliacao_clareza", "avaliacao_utilidade")),
        sa.CheckConstraint("comentario IS NULL OR length(comentario) <= 2000",
                           name="ck_feedback_comentario_tamanho"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION impedir_edicao_feedback_treinamento() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Uma avaliação enviada não pode ser editada.'
                    USING ERRCODE = '23514';
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER feedback_treinamento_imutavel
            BEFORE UPDATE ON feedbacks_treinamento
            FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
            EXECUTE FUNCTION impedir_edicao_feedback_treinamento()
        """)


def downgrade():
    op.drop_table("feedbacks_treinamento")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION impedir_edicao_feedback_treinamento()")
