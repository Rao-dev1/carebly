import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Must be set before app is imported: the app creates its tables on import.
_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmpdir, "import.db")
os.environ["ENABLE_SCHEDULER"] = "0"

import app as carebly  # noqa: E402


@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / "test.db")
    carebly.app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE=db_path,
        ADMIN_SIGNUP_CODE="",
        WHATSAPP_TOKEN="",
        WHATSAPP_PHONE_NUMBER_ID="",
        WHATSAPP_APP_SECRET="test-secret",
        WHATSAPP_VERIFY_TOKEN="verify-me",
        CRON_SECRET="cron-key",
    )
    carebly.init_db(db_path)
    yield carebly.app


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(app, role, name, email, password="password123", phone=None):
    with app.app_context():
        db = carebly.get_db()
        cur = db.execute(
            "INSERT INTO users (role, name, email, password, phone, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (role, name, email, carebly.hash_password(password), phone),
        )
        db.commit()
        return cur.lastrowid


def login(client, email, role, password="password123"):
    return client.post("/login", data={"email": email, "role": role, "password": password})
