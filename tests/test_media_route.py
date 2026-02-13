from app import create_app


class TestConfig:
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = None
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def test_media_route_returns_404_when_upload_folder_missing():
    app = create_app(TestConfig)
    client = app.test_client()

    response = client.get("/media/example.png")

    assert response.status_code == 404
