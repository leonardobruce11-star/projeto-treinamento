from datetime import datetime, timezone

from app.extensions import db
from sqlalchemy.orm import validates
from app.utils.aprovacao_treinamento import validar_nota_minima_percentual


class Treinamento(db.Model):
    __tablename__ = "treinamentos"
    __table_args__ = (
        db.CheckConstraint(
            "nota_minima_percentual IS NULL OR "
            "(nota_minima_percentual > 0 AND nota_minima_percentual <= 100)",
            name="ck_treinamentos_nota_minima_percentual",
        ),
    )

    # NULL conserva a regra automática; configuração ainda sem interface.
    nota_minima_percentual = db.Column(db.Numeric(5, 2), nullable=True)

    @validates("nota_minima_percentual")
    def validar_nota_minima(self, chave, valor):
        return validar_nota_minima_percentual(valor)

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    titulo = db.Column(
        db.String(200),
        nullable=False,
        unique=True
    )

    descricao = db.Column(
        db.Text,
        nullable=True
    )

    categoria_id = db.Column(
        db.Integer,
        db.ForeignKey("categorias_treinamento.id"),
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

    categoria = db.relationship(
        "CategoriaTreinamento",
        backref=db.backref(
            "treinamentos",
            lazy=True
        )
    )
