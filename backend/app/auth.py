import hashlib
import os
import secrets

from fastapi import HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import text

from app.db import engine, one

password_hash = PasswordHash.recommended()
dummy_hash = password_hash.hash(secrets.token_urlsafe(32))
COOKIE = "drawing_session"


def login_user(login: str, password: str, response: Response):
    with engine.connect() as conn:
        user = one(conn, "SELECT * FROM app_user WHERE login=:login", login=login)
    valid = password_hash.verify(password, user["password_hash"] if user else dummy_hash)
    if not user or not valid:
        raise HTTPException(401, "账号或密码错误")
    token = secrets.token_urlsafe(32)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO user_session (token_sha,user_id,expires_at)
            VALUES (:sha,:user_id,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 8 HOUR))"""),
            {"sha": hashlib.sha256(token.encode()).digest(), "user_id": user["id"]})
    response.set_cookie(COOKIE, token, max_age=8 * 3600, httponly=True,
                        samesite="lax", secure=os.environ.get("COOKIE_SECURE") == "1", path="/api")
    return {"id": user["id"], "login": user["login"]}


def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    with engine.connect() as conn:
        user = one(conn, """SELECT u.id,u.login FROM user_session s
            JOIN app_user u ON u.id=s.user_id
            WHERE s.token_sha=:sha AND s.expires_at>UTC_TIMESTAMP(6)""",
            sha=hashlib.sha256(token.encode()).digest())
    if not user:
        raise HTTPException(401, "请先登录")
    return dict(user)


def logout_user(request: Request, response: Response):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM user_session WHERE token_sha=:sha"),
                     {"sha": hashlib.sha256(request.cookies.get(COOKIE, "").encode()).digest()})
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True}


def require_member(conn, project_id: int, user_id: int, write=False):
    member = one(conn, """SELECT role FROM project_member
        WHERE project_id=:project_id AND user_id=:user_id""",
        project_id=project_id, user_id=user_id)
    if not member:
        raise HTTPException(404, "项目不存在或无访问权限")
    if write and member["role"] == "reader":
        raise HTTPException(403, "当前账号只有查看权限")
