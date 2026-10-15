"""Certificados emitidos para participações em treinamentos."""

from datetime import datetime, timezone

from app.extensions import db


class CertificadoTreinamento(db.Model):
    __tablename__ = "certificados_treinamento"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    participacao_id = db.Column(
        db.Integer,
        db.ForeignKey("participacoes_treinamento.id"),
        nullable=False,
        unique=True,
    )

    codigo_verificacao = db.Column(
        db.String(64),
        nullable=False,
        unique=True,
    )

    nome_participante = db.Column(
        db.String(150),
        nullable=False,
    )

    titulo_treinamento = db.Column(
        db.String(200),
        nullable=False,
    )

    emitido_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    participacao = db.relationship(
        "ParticipacaoTreinamento",
        backref=db.backref(
            "certificado",
            uselist=False,
        ),
    )
