import os

from app import create_app


app = create_app()


if __name__ == "__main__":

    ambiente = os.getenv(
        "APP_ENV",
        "development",
    ).lower()

    debug_ativo = (
        ambiente == "development"
    )

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=debug_ativo,
    )