from datetime import datetime, timezone

from app.extensions import db


class Grupo(db.Model):
    __tablename__ = "grupos"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(150),
        nullable=False,
        unique=True
    )

    descricao = db.Column(
        db.Text,
        nullable=True
    )

    ativo = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    gophish_group_id = db.Column(
        db.Integer,
        nullable=True
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    ultima_sincronizacao = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )