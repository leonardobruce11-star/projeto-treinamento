"""Identidade durável da avaliação; não contém respostas nem reserva tentativa."""
from datetime import datetime, timezone
from uuid import uuid4
from app.extensions import db


class SubmissaoQuiz(db.Model):
    __tablename__ = 'submissoes_quiz'
    __table_args__ = (
        db.UniqueConstraint('participacao_id', 'resultado_base_id', name='uq_submissoes_quiz_base'),
        db.UniqueConstraint('resultado_id', name='uq_submissoes_quiz_resultado'),
        db.ForeignKeyConstraint(['participacao_id', 'resultado_base_id'],
            ['resultados_quiz.participacao_id', 'resultados_quiz.id'],
            name='fk_submissoes_quiz_base', ondelete='RESTRICT'),
        db.ForeignKeyConstraint(['participacao_id', 'resultado_id'],
            ['resultados_quiz.participacao_id', 'resultados_quiz.id'],
            name='fk_submissoes_quiz_resultado', ondelete='RESTRICT'),
        db.CheckConstraint(
            "(estado = 'pendente' AND resultado_id IS NULL AND processada_em IS NULL) OR "
            "(estado = 'processada' AND resultado_id IS NOT NULL AND processada_em IS NOT NULL)",
            name='ck_submissoes_quiz_estado'),
        db.CheckConstraint('processada_em IS NULL OR processada_em >= criada_em',
            name='ck_submissoes_quiz_datas'),
        db.CheckConstraint('resultado_base_id IS NULL OR resultado_id IS NULL OR resultado_base_id <> resultado_id',
            name='ck_submissoes_quiz_resultado_distinto'),
        db.Index('uq_submissoes_quiz_inicial', 'participacao_id', unique=True,
            postgresql_where=db.text('resultado_base_id IS NULL'),
            sqlite_where=db.text('resultado_base_id IS NULL')),
        db.Index('uq_submissoes_quiz_pendente', 'participacao_id', unique=True,
            postgresql_where=db.text("estado = 'pendente'"),
            sqlite_where=db.text("estado = 'pendente'")),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid4)
    participacao_id = db.Column(db.Integer,
        db.ForeignKey('participacoes_treinamento.id', name='fk_submissoes_quiz_participacao',
                      ondelete='RESTRICT'), nullable=False)
    resultado_base_id = db.Column(db.Integer, nullable=True)
    estado = db.Column(db.String(12), nullable=False, default='pendente')
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False,
                          default=lambda: datetime.now(timezone.utc))
    resultado_id = db.Column(db.Integer, nullable=True)
    processada_em = db.Column(db.DateTime(timezone=True), nullable=True)
