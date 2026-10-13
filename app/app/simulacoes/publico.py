from flask import Blueprint,render_template,redirect,url_for
from .educacao import visao_landing,acessar
from .snapshots import SimulacaoInvalida

educacao_simulacao_bp=Blueprint('educacao_simulacao',__name__,url_prefix='/simulacao/educacao')


@educacao_simulacao_bp.after_request
def privacidade(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; style-src 'self'; img-src 'self'; script-src 'none'"
    response.headers['X-Content-Type-Options']='nosniff'
    return response


@educacao_simulacao_bp.get('/<identificador>')
def landing(identificador):
    try:view=visao_landing(identificador)
    except SimulacaoInvalida:return render_template('simulacoes/educacao.html',disponivel=False),404
    return render_template('simulacoes/educacao.html',disponivel=True,continuar=view['treinamento_disponivel'],identificador=identificador)


@educacao_simulacao_bp.post('/<identificador>/treinamento')
def treinamento(identificador):
    try:alias=acessar(identificador)
    except SimulacaoInvalida:return render_template('simulacoes/educacao.html',disponivel=False),404
    return redirect(url_for('portal_treinamento.inicio',token=alias),code=303)


from flask_wtf.csrf import CSRFError
@educacao_simulacao_bp.errorhandler(CSRFError)
def csrf_invalido(erro):
    return render_template('simulacoes/educacao.html',disponivel=False),400
