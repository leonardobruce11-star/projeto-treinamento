from app.extensions import db


class Questao(db.Model):
    __tablename__ = "questoes"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    treinamento_id = db.Column(
        db.Integer,
        db.ForeignKey("treinamentos.id"),
        nullable=False
    )

    enunciado = db.Column(
        db.Text,
        nullable=False
    )

    ordem = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    treinamento = db.relationship(
        "Treinamento",
        backref=db.backref(
            "questoes",
            order_by="Questao.ordem",
            cascade="all, delete-orphan"
        )
    )