import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from database import create_db_and_tables
from api import auth, channels, models, pool, settings, apikeys
from api.proxy import router as proxy_router
from ws.events import router as ws_router, start_cleanup_task
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
    cleanup_task = await start_cleanup_task()
    yield
    cleanup_task.cancel()
    shutdown_scheduler()


app = FastAPI(title="Available Computing", lifespan=lifespan)

_cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(channels.router, prefix="/api/v1/channels", tags=["channels"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(pool.router, prefix="/api/v1/pool", tags=["pool"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(apikeys.router, prefix="/api/v1/apikeys", tags=["apikeys"])

# WebSocket
app.include_router(ws_router)

# OpenAI-compatible proxy
app.include_router(proxy_router, prefix="/v1", tags=["proxy"])

# Serve frontend static files + SPA fallback
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import FileResponse, Response

    _spa_index = _static_dir / "index.html"

    class SPAFallback(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response: Response = await call_next(request)
            if response.status_code == 404 and request.method == "GET":
                accept = request.headers.get("accept", "")
                if "text/html" in accept and _spa_index.exists():
                    return FileResponse(str(_spa_index), media_type="text/html")
            return response

    app.add_middleware(SPAFallback)
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
