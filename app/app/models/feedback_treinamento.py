"""Avaliação imutável vinculada exclusivamente à participação."""
from sqlalchemy import event, inspect
from app.extensions import db


class FeedbackTreinamento(db.Model):
    __tablename__ = "feedbacks_treinamento"
    __table_args__ = (
        db.UniqueConstraint("participacao_id", name="uq_feedback_participacao"),
        *(db.CheckConstraint(f"{campo} BETWEEN 1 AND 5", name=f"ck_feedback_{campo}")
          for campo in ("avaliacao_geral", "avaliacao_conteudo", "avaliacao_clareza", "avaliacao_utilidade")),
        db.CheckConstraint("comentario IS NULL OR length(comentario) <= 2000",
                           name="ck_feedback_comentario_tamanho"),
    )

    id = db.Column(db.Integer, primary_key=True)
    participacao_id = db.Column(db.Integer, db.ForeignKey(
        "participacoes_treinamento.id", ondelete="RESTRICT"), nullable=False)
    avaliacao_geral = db.Column(db.SmallInteger, nullable=False)
    avaliacao_conteudo = db.Column(db.SmallInteger, nullable=False)
    avaliacao_clareza = db.Column(db.SmallInteger, nullable=False)
    avaliacao_utilidade = db.Column(db.SmallInteger, nullable=False)
    comentario = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False,
                          server_default=db.func.current_timestamp())
    participacao = db.relationship("ParticipacaoTreinamento", backref=db.backref(
        "feedback", uselist=False, passive_deletes="all"))


class FeedbackImutavel(ValueError):
    pass


@event.listens_for(FeedbackTreinamento, "before_update")
def impedir_edicao_feedback(mapper, connection, feedback):
    # PostgreSQL também protege UPDATE direto por trigger na migration.
    if any(atributo.history.has_changes() for atributo in inspect(feedback).attrs):
        raise FeedbackImutavel("Uma avaliação enviada não pode ser editada.")
