import hmac
import hashlib
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel
from sqlmodel import Session, select
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS, get_admin_password
from database import get_session

router = APIRouter()
bearer = HTTPBearer()


class LoginRequest(BaseModel):
    password: str


def create_token() -> str:
    payload = {
        "sub": "admin",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> bool:
    try:
        jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def verify_token_or_apikey(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    session: Session = Depends(get_session),
) -> bool:
    token = credentials.credentials
    if token.startswith("ac_"):
        from models import ApiKey
        h = hashlib.sha256(token.encode()).hexdigest()
        api_key = session.exec(
            select(ApiKey).where(ApiKey.key_hash == h).where(ApiKey.is_active == True)
        ).first()
        if api_key:
            # Throttle last_used_at updates (skip if < 60s since last)
            if not api_key.last_used_at or (datetime.utcnow() - api_key.last_used_at).total_seconds() > 60:
                api_key.last_used_at = datetime.utcnow()
                session.add(api_key)
                session.commit()
            return True
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


_login_attempts: dict[str, list[float]] = {}


def _check_rate_limit(ip: str):
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < 300]
    _login_attempts[ip] = attempts
    if len(attempts) >= 10:
        raise HTTPException(status_code=429, detail="Too many attempts, try again later")


@router.post("/login")
def login(body: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)
    if not hmac.compare_digest(body.password, get_admin_password()):
        _login_attempts.setdefault(ip, []).append(time.time())
        raise HTTPException(status_code=401, detail="Wrong password")
    _login_attempts.pop(ip, None)
    return {"token": create_token()}
