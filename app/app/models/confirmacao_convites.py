"""Recibo idempotente da confirmação humana, sem mensagem ou destinatário."""
from app.extensions import db


class ConfirmacaoConvites(db.Model):
    __tablename__ = 'confirmacoes_convites'
    id = db.Column(db.Uuid(as_uuid=True), primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False)
    assinatura = db.Column(db.String(64), nullable=False)
    resultado = db.Column(db.JSON, nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
