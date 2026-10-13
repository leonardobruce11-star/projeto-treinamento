from datetime import datetime, timezone

from app.extensions import db


class Cenario(db.Model):
    __tablename__ = "cenarios"

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

    nivel = db.Column(
        db.String(20),
        nullable=False,
        default="basico"
    )

    ativo = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )