from app import create_app
from app.extensions import db
from app.models.usuario import Usuario

app = create_app()

with app.app_context():
    email = "admin@empresa.local"

    usuario = Usuario.query.filter_by(email=email).first()

    if usuario:
        usuario.definir_senha("AdminTeste123!")
        usuario.ativo = True
        print("Usuario ja existia. Senha redefinida.")
    else:
        usuario = Usuario(
            nome="Administrador TI",
            email=email,
            perfil="administrador",
            ativo=True
        )

        usuario.definir_senha("AdminTeste123!")

        db.session.add(usuario)

        print("Novo administrador criado.")

    db.session.commit()

    print("ID:", usuario.id)
    print("Email:", usuario.email)
    print("Ativo:", usuario.ativo)
    print(
        "Senha valida:",
        usuario.verificar_senha("AdminTeste123!")
    )