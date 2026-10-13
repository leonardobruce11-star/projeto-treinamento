from datetime import datetime, timezone

from app.extensions import db


class Campanha(db.Model):
    __tablename__ = "campanhas"

    TIPOS = {
        "treinamento": "Treinamento",
        "onboarding": "Onboarding",
        "departamental": "Departamental",
        "seguranca": "Segurança",
        "simulacao_phishing": "Simulação de phishing",
    }

    @property
    def tipo_efetivo(self):
        # Registros legados sem tipo mantêm o fluxo original.
        return self.tipo or "simulacao_phishing"

    @property
    def eh_simulacao_phishing(self):
        return self.tipo_efetivo == "simulacao_phishing"

    # ==========================================================
    # IDENTIFICAÇÃO
    # ==========================================================

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(150),
        nullable=False,
        unique=True
    )

    descricao = db.Column(
        db.Text,
        nullable=True
    )

    # ==========================================================
    # STATUS
    # ==========================================================

    status = db.Column(
        db.String(40),
        nullable=False,
        default="rascunho"
    )

    # ==========================================================
    # TIPO
    # ==========================================================

    tipo = db.Column(
        db.String(40),
        nullable=True
    )

    # ==========================================================
    # RECURSOS
    # ==========================================================

    grupo_id = db.Column(
        db.Integer,
        db.ForeignKey("grupos.id"),
        nullable=True
    )

    cenario_id = db.Column(
        db.Integer,
        db.ForeignKey("cenarios.id"),
        nullable=True
    )

    template_email_id = db.Column(
        db.Integer,
        db.ForeignKey("templates_email.id"),
        nullable=True
    )

    landing_page_id = db.Column(
        db.Integer,
        db.ForeignKey("landing_pages.id"),
        nullable=True
    )

    treinamento_id = db.Column(
        db.Integer,
        db.ForeignKey("treinamentos.id"),
        nullable=True
    )

    # ==========================================================
    # GOPHISH
    # ==========================================================

    gophish_campaign_id = db.Column(
        db.Integer,
        nullable=True
    )

    # ==========================================================
    # GOVERNANÇA - CRIAÇÃO
    # ==========================================================

    criado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    # ==========================================================
    # GOVERNANÇA - APROVAÇÃO
    # ==========================================================

    aprovado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    aprovado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    motivo_reprovacao = db.Column(
        db.Text,
        nullable=True
    )

    # ==========================================================
    # PREPARAÇÃO TÉCNICA
    # ==========================================================

    preparada_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    preparada_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # ==========================================================
    # PILOTO - PREPARAÇÃO
    # ==========================================================

    piloto_preparado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    piloto_preparado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # ==========================================================
    # PILOTO - VALIDAÇÃO
    # ==========================================================

    piloto_validado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    piloto_validado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # ==========================================================
    # DECISÃO APÓS O PILOTO
    # ==========================================================

    decisao_piloto = db.Column(
        db.String(40),
        nullable=True
    )

    decisao_piloto_por_id = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id"),
        nullable=True
    )

    decisao_piloto_em = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    observacao_decisao_piloto = db.Column(
        db.Text,
        nullable=True
    )

    # ==========================================================
    # DATAS
    # ==========================================================

    criado_em = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    data_inicio = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    data_fim = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # ==========================================================
    # RELACIONAMENTOS - RECURSOS
    # ==========================================================

    grupo = db.relationship(
        "Grupo"
    )

    cenario = db.relationship(
        "Cenario"
    )

    template_email = db.relationship(
        "TemplateEmail"
    )

    landing_page = db.relationship(
        "LandingPage"
    )

    treinamento = db.relationship(
        "Treinamento"
    )

    # ==========================================================
    # RELACIONAMENTOS - GOVERNANÇA
    # ==========================================================

    criador = db.relationship(
        "Usuario",
        foreign_keys=[criado_por_id]
    )

    aprovador = db.relationship(
        "Usuario",
        foreign_keys=[aprovado_por_id]
    )

    preparador = db.relationship(
        "Usuario",
        foreign_keys=[preparada_por_id]
    )

    piloto_preparador = db.relationship(
        "Usuario",
        foreign_keys=[piloto_preparado_por_id]
    )

    piloto_validador = db.relationship(
        "Usuario",
        foreign_keys=[piloto_validado_por_id]
    )

    decisor_piloto = db.relationship(
        "Usuario",
        foreign_keys=[decisao_piloto_por_id]
    )
