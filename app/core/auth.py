import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

ALGORITHM = "HS256"
AUDIENCE = "authenticated"


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class CurrentMember:
    member_id: uuid.UUID
    household_id: uuid.UUID
    claims: dict[str, Any]


def _uuid_claim(claims: dict[str, Any], name: str) -> uuid.UUID:
    value = claims.get(name)
    if not isinstance(value, str):
        raise InvalidTokenError(f"claim {name} ausente")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise InvalidTokenError(f"claim {name} não é UUID") from exc


def decode_access_token(token: str, secret: str) -> CurrentMember:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["exp", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    return CurrentMember(
        member_id=_uuid_claim(claims, "member_id"),
        household_id=_uuid_claim(claims, "household_id"),
        claims=claims,
    )


_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou ausente",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_member(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentMember:
    if not settings.supabase_jwt_secret:
        raise RuntimeError("SUPABASE_JWT_SECRET não configurada")
    if credentials is None:
        raise _unauthorized()
    try:
        return decode_access_token(credentials.credentials, settings.supabase_jwt_secret)
    except InvalidTokenError as exc:
        raise _unauthorized() from exc
