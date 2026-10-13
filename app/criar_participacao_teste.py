import secrets

from app import create_app
from app.extensions import db
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento


app = create_app()


with app.app_context():

    colaborador = Colaborador.query.first()

    treinamento = Treinamento.query.first()

    if not colaborador:
        print("Nenhum colaborador encontrado.")
        raise SystemExit

    if not treinamento:
        print("Nenhum treinamento encontrado.")
        raise SystemExit

    token = secrets.token_urlsafe(32)

    participacao = ParticipacaoTreinamento(
        token=token,
        colaborador_id=colaborador.id,
        treinamento_id=treinamento.id
    )

    db.session.add(
        participacao
    )

    db.session.commit()

    print("Participacao criada.")
    print("Token:", token)
    print(
        "URL:",
        f"http://127.0.0.1:5000/aprender/t/{token}"
    )