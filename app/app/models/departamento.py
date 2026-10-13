from app.extensions import db


class Departamento(db.Model):
    __tablename__ = "departamentos"

    id = db.Column(db.Integer, primary_key=True)

    nome = db.Column(
        db.String(100),
        nullable=False,
        unique=True,
    )

    ativo = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )