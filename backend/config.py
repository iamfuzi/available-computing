import os
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/db.sqlite"

# SQLAlchemy QueuePool sizing. Each in-flight request holds one connection for
# its whole lifetime (upstream calls can run tens of seconds), and background
# jobs (flush_usage, passive health records, probes) take more — the default
# pool of 5+10 exhausted during traffic peaks and failed requests with
# QueuePool timeouts (2026-10-02). SQLite WAL handles many concurrent readers,
# so a generous ceiling is safe.
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "30"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "60"))

WHITELIST_PATH = Path(os.environ.get(
    "WHITELIST_PATH",
    str(Path(__file__).parent.parent / "whitelist" / "providers.yaml"),
))

PROVIDERS_PATH = Path(os.environ.get(
    "PROVIDERS_PATH",
    str(Path(__file__).parent.parent / "providers"),
))

# Routing profiles: named, reusable routing policies (one YAML file per
# profile). Profiles let multiple caller projects share one AC instance while
# each gets a tailored candidate pool, denylist, and fallback budget. Unlike
# providers/whitelist, profiles are optional — if the directory is empty or
# missing, all requests simply use the default per-key/per-request policy.
PROFILES_PATH = Path(os.environ.get(
    "PROFILES_PATH",
    str(Path(__file__).parent.parent / "profiles"),
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

# ZhiPu free flash models routinely take 11–30s for a minimal completion
# (glm-4.5-flash: ~11s thinking-off, ~28s thinking-on, measured 2026-09-30);
# at 10s every probe of those models timed out and they never became
# "verified" for concrete-model routing. Probes run sequentially in the
# background, so patience is cheap.
PROBE_TIMEOUT_SECONDS = int(os.environ.get("PROBE_TIMEOUT_SECONDS", "30"))
SLOW_RESPONSE_THRESHOLD_MS = int(os.environ.get("SLOW_THRESHOLD_MS", "1000"))
# How many consecutive transient upstream errors (5xx / network / timeout)
# a model must accrue before being marked "down" and evicted from the
# candidate pool. Below the threshold a failing model is only demoted to
# "slow" so it stays routable at lower priority and recovers on the next
# success. Free tiers produce sporadic 500s that don't reflect real outage.
PASSIVE_ERROR_DOWN_THRESHOLD = int(os.environ.get("PASSIVE_ERROR_DOWN_THRESHOLD", "3"))
# Delay between consecutive probes to models on the same channel, to avoid
# tripping per-provider rate limits during a probe sweep. Free tiers on
# providers like OpenRouter have very small daily budgets and a burst of
# simultaneous probes reliably returns 429 for every model.
PROBE_INTERVAL_BETWEEN_MODELS_SEC = float(os.environ.get("PROBE_INTERVAL_BETWEEN_MODELS_SEC", "2"))
PROBE_GLOBAL_CONCURRENCY = int(os.environ.get("PROBE_GLOBAL_CONCURRENCY", "5"))

# Heartbeats are only for models that have seen no real traffic for several
# days. Unknown or very small provider quotas are left untouched; baseline and
# event-triggered checks have separate budgets because they answer a concrete
# state change rather than continuously polling.
HEARTBEAT_IDLE_DAYS = int(os.environ.get("HEARTBEAT_IDLE_DAYS", "3"))
HEARTBEAT_MIN_PROVIDER_RPD = int(os.environ.get("HEARTBEAT_MIN_PROVIDER_RPD", "100"))
HEARTBEAT_BUDGET_RATIO = float(os.environ.get("HEARTBEAT_BUDGET_RATIO", "0.01"))
HEARTBEAT_REAL_TRAFFIC_RESERVE_RATIO = float(
    os.environ.get("HEARTBEAT_REAL_TRAFFIC_RESERVE_RATIO", "0.90")
)

# Event-triggered rechecks use one correlated check_run_id. Three independent
# failures inside the window are required before a free-policy change is
# persisted. The initial check is scheduled promptly but outside request I/O.
EVENT_RECHECK_MAX_ATTEMPTS = int(os.environ.get("EVENT_RECHECK_MAX_ATTEMPTS", "3"))
EVENT_RECHECK_WINDOW_MINUTES = int(os.environ.get("EVENT_RECHECK_WINDOW_MINUTES", "30"))
EVENT_RECHECK_INITIAL_DELAY_SECONDS = int(os.environ.get("EVENT_RECHECK_INITIAL_DELAY_SECONDS", "5"))
EVENT_RECHECK_RETRY_DELAY_SECONDS = int(os.environ.get("EVENT_RECHECK_RETRY_DELAY_SECONDS", "300"))

# Local proxy rate limits. API-key scoped limits are the primary guard for
# third-party integrations; IP fallback is intentionally looser so shared NATs
# do not make unrelated API keys trip over each other.
PROXY_RATE_WINDOW_SECONDS = int(os.environ.get("PROXY_RATE_WINDOW_SECONDS", "60"))
PROXY_API_KEY_RATE_LIMIT = int(os.environ.get("PROXY_API_KEY_RATE_LIMIT", "120"))
PROXY_ADMIN_RATE_LIMIT = int(os.environ.get("PROXY_ADMIN_RATE_LIMIT", "600"))
PROXY_IP_FALLBACK_RATE_LIMIT = int(os.environ.get("PROXY_IP_FALLBACK_RATE_LIMIT", "600"))
PROXY_MODEL_CONCURRENCY_LIMIT = int(os.environ.get("PROXY_MODEL_CONCURRENCY_LIMIT", "2"))
# Embedding/rerank requests are sub-second and cheap; give them their own,
# higher per-model concurrency budget instead of sharing the chat limit.
PROXY_EMBEDDING_CONCURRENCY_LIMIT = int(
    os.environ.get("PROXY_EMBEDDING_CONCURRENCY_LIMIT", "4")
)
# How long a request may queue for a busy model slot before giving up with
# all_candidates_busy. 0 keeps the old fail-fast behaviour.
PROXY_SLOT_QUEUE_TIMEOUT_SECONDS = float(
    os.environ.get("PROXY_SLOT_QUEUE_TIMEOUT_SECONDS", "15")
)
# Free-tier models almost always carry per-key RPM caps, but several free
# providers (zhipu, xfyun) never send rate-limit headers, so the observed
# per-model budget never engages and bursts eat live 429s. These two floors
# close that gap BEFORE the request leaves:
#   PROXY_DEFAULT_MODEL_RPM — fallback per-model RPM when no observed/manual
#     rpm exists on the model. 0 = off.
#   PROXY_PROVIDER_RPM — sliding-window cap shared by ALL models of one
#     channel (they share the upstream API key's real limit). 0 = off.
PROXY_DEFAULT_MODEL_RPM = int(os.environ.get("PROXY_DEFAULT_MODEL_RPM", "0"))
PROXY_PROVIDER_RPM = int(os.environ.get("PROXY_PROVIDER_RPM", "0"))
# Upstream timeout for passthrough endpoints (embeddings/rerank/images).
# These normally answer in well under 2s; a 120s hold keeps the model's
# concurrency slots hostage during upstream brown-outs and queues every
# later request into its own timeout.
PROXY_PASSTHROUGH_TIMEOUT_SECONDS = float(
    os.environ.get("PROXY_PASSTHROUGH_TIMEOUT_SECONDS", "60")
)

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
