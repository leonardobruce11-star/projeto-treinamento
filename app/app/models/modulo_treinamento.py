from app.extensions import db


class ModuloTreinamento(db.Model):
    __tablename__ = "modulos_treinamento"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    treinamento_id = db.Column(
        db.Integer,
        db.ForeignKey("treinamentos.id"),
        nullable=False
    )

    titulo = db.Column(
        db.String(200),
        nullable=False
    )

    conteudo = db.Column(
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
            "modulos",
            order_by="ModuloTreinamento.ordem",
            cascade="all, delete-orphan"
        )
    )