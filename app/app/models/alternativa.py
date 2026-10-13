from app.extensions import db


class Alternativa(db.Model):
    __tablename__ = "alternativas"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    questao_id = db.Column(
        db.Integer,
        db.ForeignKey("questoes.id"),
        nullable=False
    )

    texto = db.Column(
        db.Text,
        nullable=False
    )

    correta = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    questao = db.relationship(
        "Questao",
        backref=db.backref(
            "alternativas",
            cascade="all, delete-orphan"
        )
    )