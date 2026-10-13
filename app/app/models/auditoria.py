from datetime import datetime, timezone

from app.extensions import db


class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    usuario_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True,
    )

    acao = db.Column(
        db.String(100),
        nullable=False,
    )

    objeto = db.Column(
        db.String(100),
        nullable=True,
    )

    objeto_id = db.Column(
        db.Integer,
        nullable=True,
    )

    detalhes = db.Column(
        db.Text,
        nullable=True,
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ======================================================
    # RELACIONAMENTO
    # ======================================================

    usuario = db.relationship(
        "Usuario",
        foreign_keys=[usuario_id]
    )