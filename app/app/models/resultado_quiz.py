from datetime import datetime, timezone

from app.extensions import db


class ResultadoQuiz(db.Model):
    __tablename__ = "resultados_quiz"

    __table_args__ = (
        db.UniqueConstraint(
            "participacao_id", "id", name="uq_resultados_quiz_participacao_id",
        ),
        db.CheckConstraint(
            "numero_tentativa >= 1",
            name="ck_resultados_quiz_numero_tentativa",
        ),
        db.UniqueConstraint(
            "participacao_id", "numero_tentativa",
            name="uq_resultados_quiz_participacao_tentativa",
        ),
        db.CheckConstraint(
            '(percentual_minimo_aplicado IS NULL AND minimo_acertos_aplicado IS NULL AND '
            'aprovado IS NULL) OR (percentual_minimo_aplicado IS NOT NULL AND '
            'minimo_acertos_aplicado IS NOT NULL AND aprovado IS NOT NULL)',
            name="ck_resultados_quiz_avaliacao_completa",
        ),
        db.CheckConstraint(
            'aprovado IS NULL OR (total_questoes > 0 AND total_acertos BETWEEN 0 AND '
            'total_questoes AND percentual BETWEEN 0 AND 100 AND percentual_minimo_aplicado > 0 '
            'AND percentual_minimo_aplicado <= 100 AND minimo_acertos_aplicado BETWEEN 1 AND '
            'total_questoes AND 100 * CAST(minimo_acertos_aplicado AS NUMERIC) >= '
            'total_questoes * percentual_minimo_aplicado AND 100 * '
            '(CAST(minimo_acertos_aplicado AS NUMERIC) - 1) < total_questoes * '
            'percentual_minimo_aplicado AND aprovado = (total_acertos >= '
            'minimo_acertos_aplicado))',
            name="ck_resultados_quiz_avaliacao_criterio",
        ),
    )

    # Cardinalidade múltipla no banco; o portal ainda grava somente tentativa 1.
    # Default no servidor mantém INSERTs legados/SQL direto na tentativa 1.
    numero_tentativa = db.Column(
        db.Integer, nullable=False, server_default=db.text("1"),
    )

    # Snapshot do critério efetivo. Todos NULL identifica avaliação desconhecida.
    percentual_minimo_aplicado = db.Column(db.Numeric(5, 2), nullable=True)
    minimo_acertos_aplicado = db.Column(db.Integer, nullable=True)
    aprovado = db.Column(db.Boolean, nullable=True)

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    participacao_id = db.Column(
        db.Integer,
        db.ForeignKey("participacoes_treinamento.id"),
        nullable=False
    )

    total_questoes = db.Column(
        db.Integer,
        nullable=False
    )

    total_acertos = db.Column(
        db.Integer,
        nullable=False
    )

    percentual = db.Column(
        db.Float,
        nullable=False
    )

    concluido_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    participacao = db.relationship(
        "ParticipacaoTreinamento",
        backref=db.backref(
            "resultados_quiz",
            order_by="(ResultadoQuiz.numero_tentativa, ResultadoQuiz.id)",
            lazy="raise"
        )
    )
