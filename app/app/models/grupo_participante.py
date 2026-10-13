from datetime import datetime, timezone

from app.extensions import db


class GrupoParticipante(db.Model):
    __tablename__ = "grupo_participantes"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    grupo_id = db.Column(
        db.Integer,
        db.ForeignKey("grupos.id"),
        nullable=False
    )

    colaborador_id = db.Column(
        db.Integer,
        db.ForeignKey("colaboradores.id"),
        nullable=False
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    grupo = db.relationship(
        "Grupo",
        backref="participantes"
    )

    colaborador = db.relationship(
        "Colaborador"
    )

    __table_args__ = (
        db.UniqueConstraint(
            "grupo_id",
            "colaborador_id",
            name="uq_grupo_colaborador"
        ),
    )