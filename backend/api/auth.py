import hmac
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS, get_admin_password

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
