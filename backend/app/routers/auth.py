from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import (
    clear_session_cookie,
    create_session_token,
    require_session,
    set_session_cookie,
    verify_credentials,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response):
    if not verify_credentials(body.username, body.password):
        # One message for both halves -- never reveal which was wrong.
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    set_session_cookie(response, create_session_token(body.username))
    return {"user": body.username}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(require_session)):
    return {"user": user}
