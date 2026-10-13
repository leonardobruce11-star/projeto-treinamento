from datetime import datetime, timezone

from app.extensions import db


class Colaborador(db.Model):
    __tablename__ = "colaboradores"

    id = db.Column(db.Integer, primary_key=True)

    nome = db.Column(
        db.String(150),
        nullable=False,
    )

    email = db.Column(
        db.String(180),
        nullable=False,
        unique=True,
        index=True,
    )

    cargo = db.Column(
        db.String(100),
        nullable=True,
    )

    departamento_id = db.Column(
        db.Integer,
        db.ForeignKey("departamentos.id"),
        nullable=False,
    )

    ativo = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    departamento = db.relationship(
        "Departamento",
        backref="colaboradores",
    )