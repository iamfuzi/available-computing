import os
import base64

# Set env vars BEFORE any backend imports (config.py reads them at import time)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-for-testing-only")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import pytest
import pytest_asyncio
import httpx
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, Session

import database
from models import Channel, Model, HealthRecord, Setting
from services.crypto import encrypt, generate_salt


# ── In-memory SQLite engine (session-scoped) ──────────────────────────────

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture(scope="session", autouse=True)
def _patch_engine(test_engine):
    """Replace database.engine with our in-memory engine for the whole session."""
    database.engine = test_engine
    yield
    # No need to restore — session is ending


@pytest.fixture(autouse=True)
def _reset_tables(test_engine):
    """Drop and recreate all tables before each test."""
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Clear in-memory rate limiters between tests."""
    from api.auth import _login_attempts
    from api.proxy import _proxy_requests, _model_semaphores
    _login_attempts.clear()
    _proxy_requests.clear()
    _model_semaphores.clear()
    yield


# ── DB session fixture ────────────────────────────────────────────────────

@pytest.fixture
def db_session(test_engine):
    with Session(test_engine) as session:
        yield session


# ── Fixed crypto salt for deterministic tests ─────────────────────────────

FIXED_SALT = b"\x01" * 32


@pytest.fixture
def fixed_salt():
    return FIXED_SALT


@pytest.fixture
def sample_channel(db_session, fixed_salt):
    """Create a sample channel with encrypted API key."""
    # Ensure salt exists in settings
    db_session.add(Setting(key="crypto_salt", value=base64.b64encode(fixed_salt).decode()))
    db_session.commit()

    enc_key = encrypt("sk-test-api-key-12345", "test-admin-password", fixed_salt)
    channel = Channel(
        id="ch-001",
        provider_type="openrouter",
        name="Test OpenRouter",
        api_key_enc=enc_key,
        enabled=True,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def sample_model(db_session, sample_channel):
    """Create a sample free model."""
    model = Model(
        id="mdl-001",
        channel_id=sample_channel.id,
        model_id="test-model-free",
        display_name="Test Free Model",
        category="text",
        is_free=True,
        free_type="permanent",
        free_source="whitelist",
        health_status="healthy",
        last_response_ms=200,
        is_active=True,
    )
    db_session.add(model)
    db_session.commit()
    return model


# ── HTTP client fixture (for API integration tests) ───────────────────────

@pytest_asyncio.fixture
async def app_client(db_session, test_engine):
    """httpx.AsyncClient wired to the FastAPI app, with scheduler mocked out."""
    from unittest.mock import patch, MagicMock
    from main import app
    from database import get_session

    # Override get_session to use our test session
    app.dependency_overrides[get_session] = lambda: db_session

    # Mock the scheduler so APScheduler doesn't run in tests
    with patch("services.scheduler.scheduler") as mock_scheduler:
        mock_scheduler.add_job = MagicMock()
        mock_scheduler.start = MagicMock()
        mock_scheduler.shutdown = MagicMock()
        mock_scheduler.running = False

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    app.dependency_overrides.clear()


# ── Auth helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def auth_headers():
    """Return Authorization headers with a valid JWT."""
    from api.auth import create_token
    token = create_token()
    return {"Authorization": f"Bearer {token}"}
