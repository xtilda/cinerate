import pytest
from cinerate import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite"),
            "SECRET_KEY": "test-only",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
