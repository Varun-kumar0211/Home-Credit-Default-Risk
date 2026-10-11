from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import JWT_SECRET
from schemas import LoginRequest, RegisterRequest


auth_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt + digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, value = encoded.split("$", 1)
        if scheme != "scrypt":
            return False
        raw = base64.b64decode(value)
        salt, expected = raw[:16], raw[16:]
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_token(credentials: LoginRequest, users) -> dict:
    user = users.get_user(credentials.username)
    if not user or not user["is_active"] or not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return issue_account_token(user)


def issue_account_token(user: dict) -> dict:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": user["username"],
            "role": user.get("role", "analyst"),
            "version": user["version"],
            "iat": now,
            "exp": now + timedelta(hours=8),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 28800}


def register_user(credentials: RegisterRequest, users) -> dict:
    if not users.create_user(credentials.username, hash_password(credentials.password), role="applicant"):
        raise HTTPException(status_code=409, detail="Username is already registered.")
    return issue_token(credentials, users)


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required.")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"],
                             options={"require": ["sub", "exp", "version"]})
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token.") from exc
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="Token subject is missing.")
    if type(payload.get("version")) is not int or payload["version"] < 0:
        raise HTTPException(status_code=401, detail="Invalid session version.")
    return payload


def validate_account(payload, users):
    user = users.get_user(payload["sub"])
    if (not user or not user["is_active"] or user["version"] != payload["version"]
            or user["role"] not in {"analyst", "reviewer", "admin", "applicant"}):
        raise HTTPException(status_code=401, detail="User session is no longer available. Log in again.")
    return user


def require_role(
    allowed_roles: tuple[str, ...] | set[str] | list[str],
    users,
):
    allowed = {role.lower() for role in allowed_roles}

    def _require_role(payload: dict = Depends(require_auth)) -> str:
        user = validate_account(payload, users)
        role = user["role"]
        if role.lower() not in allowed:
            raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")
        return user["username"]

    return _require_role
