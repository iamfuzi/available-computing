from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from database import create_db_and_tables
from api import auth, channels, models, pool, settings
from ws.events import router as ws_router
from services.scheduler import init_scheduler, shutdown_scheduler


def get_key_fn(channel):
    """Decrypt a channel's API key using the admin password."""
    import base64
    from sqlmodel import Session
    from database import engine
    from models import Setting
    from services.crypto import decrypt
    from config import get_admin_password

    with Session(engine) as session:
        salt_row = session.get(Setting, "crypto_salt")
        if not salt_row:
            raise RuntimeError("No crypto salt found")
        salt = base64.b64decode(salt_row.value)
    return decrypt(channel.api_key_enc, get_admin_password(), salt)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    init_scheduler(get_key_fn)
    yield
    shutdown_scheduler()


app = FastAPI(title="Available Computing", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(channels.router, prefix="/api/v1/channels", tags=["channels"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(pool.router, prefix="/api/v1/pool", tags=["pool"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])

# WebSocket
app.include_router(ws_router)

# Serve frontend static files in production
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
