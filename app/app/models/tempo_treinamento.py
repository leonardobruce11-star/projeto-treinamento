from datetime import datetime, timezone

from app.extensions import db


class TempoTreinamento(db.Model):
    """Tempo ativo agregado de uma participação em um módulo."""

    __tablename__ = "tempos_treinamento"
    __table_args__ = (
        db.UniqueConstraint(
            "participacao_id", "modulo_id",
            name="uq_tempos_treinamento_participacao_modulo",
        ),
        db.CheckConstraint(
            "segundos_ativos >= 0",
            name="ck_tempos_treinamento_segundos_ativos_nao_negativos",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    participacao_id = db.Column(
        db.Integer,
        db.ForeignKey("participacoes_treinamento.id", ondelete="CASCADE"),
        nullable=False,
    )
    modulo_id = db.Column(
        db.Integer,
        db.ForeignKey("modulos_treinamento.id", ondelete="CASCADE"),
        nullable=False,
    )
    segundos_ativos = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )
    ultima_atividade_em = db.Column(db.DateTime(timezone=True), nullable=True)
    criado_em = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    atualizado_em = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    participacao = db.relationship(
        "ParticipacaoTreinamento",
        backref=db.backref("tempos_treinamento", passive_deletes="all"),
    )
    modulo = db.relationship(
        "ModuloTreinamento",
        backref=db.backref("tempos_treinamento", passive_deletes="all"),
    )
