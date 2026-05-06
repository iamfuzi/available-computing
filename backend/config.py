import os
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/db.sqlite"

WHITELIST_PATH = Path(os.environ.get(
    "WHITELIST_PATH",
    str(Path(__file__).parent.parent / "whitelist" / "providers.yaml"),
))

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

PROBE_TIMEOUT_SECONDS = 10
SLOW_RESPONSE_THRESHOLD_MS = int(os.environ.get("SLOW_THRESHOLD_MS", "1000"))


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
