from datetime import datetime, timezone

from app.extensions import db


class TemplateEmail(db.Model):
    __tablename__ = "templates_email"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(150),
        nullable=False,
        unique=True
    )

    assunto = db.Column(
        db.String(200),
        nullable=False
    )

    conteudo_html = db.Column(
        db.Text,
        nullable=False
    )

    cenario_id = db.Column(
        db.Integer,
        db.ForeignKey("cenarios.id"),
        nullable=False
    )

    gophish_template_id = db.Column(
        db.Integer,
        nullable=True
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

    ultima_sincronizacao = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    cenario = db.relationship(
        "Cenario",
        backref="templates_email"
    )