from flask import Flask
from flask_session import Session
from config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    Session(app)

    from routes import main_bp
    app.register_blueprint(main_bp)
    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
