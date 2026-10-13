from flask import Blueprint, jsonify
from sqlalchemy import text
from app.extensions import db
from app.integracoes.gophish.service import servico_gophish

health_bp = Blueprint('health', __name__)


@health_bp.get('/health')
def health():
    try:
        db.session.execute(text('SELECT 1'))
        database_status = 'connected'
    except Exception:
        db.session.rollback()
        database_status = 'disconnected'
    # Saúde principal não depende de rede ou de configuração opcional.
    # O estado abaixo é de configuração, nunca uma afirmação de conectividade.
    return jsonify(status='ok' if database_status == 'connected' else 'error',
                   database=database_status, gophish=servico_gophish().estado_local()), (200 if database_status == 'connected' else 503)
