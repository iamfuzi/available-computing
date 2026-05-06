from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS, get_admin_password

router = APIRouter()
bearer = HTTPBearer()


class LoginRequest(BaseModel):
    password: str


def create_token() -> str:
    payload = {"exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> bool:
    try:
        jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@router.post("/login")
def login(body: LoginRequest):
    if body.password != get_admin_password():
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"token": create_token()}
