from datetime import datetime, timezone

from app.extensions import db


class LandingPage(db.Model):
    __tablename__ = "landing_pages"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(150),
        nullable=False,
        unique=True
    )

    titulo = db.Column(
        db.String(200),
        nullable=False
    )

    mensagem = db.Column(
        db.Text,
        nullable=False
    )

    conteudo_html = db.Column(
        db.Text,
        nullable=False
    )

    gophish_page_id = db.Column(
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