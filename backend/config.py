import os
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/db.sqlite"

WHITELIST_PATH = Path(os.environ.get(
    "WHITELIST_PATH",
    str(Path(__file__).parent.parent / "whitelist" / "providers.yaml"),
))

_jwt_secret_file = os.environ.get("JWT_SECRET_FILE")
if _jwt_secret_file and Path(_jwt_secret_file).exists():
    JWT_SECRET = Path(_jwt_secret_file).read_text().strip()
else:
    JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is required. Set JWT_SECRET or JWT_SECRET_FILE env var. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

PROBE_TIMEOUT_SECONDS = 10
SLOW_RESPONSE_THRESHOLD_MS = int(os.environ.get("SLOW_THRESHOLD_MS", "1000"))

# Passive billing-failure downgrade: consecutive 401/403 (auth/billing) failures
# after which a free-flagged model is moved out of the free pool. A successful
# call resets the counter to 0.
BILLING_FAILURE_THRESHOLD = int(os.environ.get("BILLING_FAILURE_THRESHOLD", "3"))

# SiliconFlow release-notes sync: periodically decommission models that the
# upstream has officially retired, so the pool doesn't keep dead entries that
# match by name but fail every call. Set to empty to disable.
SF_RELEASE_NOTES_URL = os.environ.get(
    "SF_RELEASE_NOTES_URL",
    "https://docs.siliconflow.cn/cn/release-notes/overview.md",
)
SF_RELEASE_SYNC_ENABLED = os.environ.get("SF_RELEASE_SYNC_ENABLED", "true").lower() in ("1", "true", "yes")


def get_admin_password() -> str:
    password_file = os.environ.get("ADMIN_PASSWORD_FILE")
    if password_file and Path(password_file).exists():
        return Path(password_file).read_text().strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "Admin password not set. Use ADMIN_PASSWORD or ADMIN_PASSWORD_FILE."
        )
    return password
