from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

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

    senha_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    perfil = db.Column(
        db.String(30),
        nullable=False,
        default="administrador",
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

    def definir_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def verificar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)