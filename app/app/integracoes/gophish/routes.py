from flask import Blueprint, render_template
from .policy import administrador, bloquear_mutacao_avulsa
from .service import servico_gophish

gophish_bp = Blueprint('gophish', __name__, url_prefix='/integracoes/gophish')


@gophish_bp.get('/')
@administrador
def index():
    # Diagnóstico administrativo apenas local; visitar a página não faz HTTP.
    return render_template('integracoes/gophish_status.html', estado=servico_gophish().estado_local())


@gophish_bp.post('/grupo-teste')
@administrador
def criar_grupo_teste():
    bloquear_mutacao_avulsa()


@gophish_bp.after_request
def privacidade(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response
