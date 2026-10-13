from datetime import datetime, timezone

from app.extensions import db


class ParticipacaoTreinamento(db.Model):
    __tablename__ = "participacoes_treinamento"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    token = db.Column(
        db.String(128),
        nullable=False,
        unique=True,
        index=True
    )

    colaborador_id = db.Column(
        db.Integer,
        db.ForeignKey("colaboradores.id"),
        nullable=False
    )

    treinamento_id = db.Column(
        db.Integer,
        db.ForeignKey("treinamentos.id"),
        nullable=False
    )

    # ==========================================================
    # CAMPANHA
    # ==========================================================

    campanha_id = db.Column(
        db.Integer,
        db.ForeignKey("campanhas.id"),
        nullable=True
    )

    # ==========================================================
    # PROGRESSO
    # ==========================================================

    iniciado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    concluido_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # Primeiro instante de aprovação; independente da finalização do fluxo.
    aprovado_em = db.Column(db.DateTime(timezone=True), nullable=True)

    ultimo_modulo_id = db.Column(
        db.Integer,
        db.ForeignKey("modulos_treinamento.id"),
        nullable=True
    )

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # ==========================================================
    # RELACIONAMENTOS
    # ==========================================================

    colaborador = db.relationship(
        "Colaborador"
    )

    treinamento = db.relationship(
        "Treinamento"
    )

    campanha = db.relationship(
        "Campanha",
        backref="participacoes_treinamento"
    )

    ultimo_modulo = db.relationship(
        "ModuloTreinamento"
    )