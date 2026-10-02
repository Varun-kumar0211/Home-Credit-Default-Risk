from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import AUTH_PASSWORD, AUTH_USERNAME, JWT_SECRET
from schemas import LoginRequest


auth_scheme = HTTPBearer(auto_error=False)


def issue_token(credentials: LoginRequest) -> dict:
    if credentials.username != AUTH_USERNAME or credentials.password != AUTH_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": AUTH_USERNAME, "iat": now, "exp": now + timedelta(hours=8)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 28800}


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required.")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token.") from exc
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Token subject is missing.")
    return str(subject)
