import os

from sqlalchemy import text

from app.auth import password_hash
from app.db import engine, one


def main():
    login = os.environ["BOOTSTRAP_LOGIN"]
    password = os.environ["BOOTSTRAP_PASSWORD"]
    if len(password) < 12:
        raise ValueError("BOOTSTRAP_PASSWORD must contain at least 12 characters")
    with engine.begin() as conn:
        existing = one(conn, "SELECT id FROM app_user WHERE login=:login", login=login)
        if existing:
            print("Initial account already exists; credentials unchanged.")
            return
        user_id = conn.execute(text("""INSERT INTO app_user (login,password_hash,is_admin)
            VALUES (:login,:password_hash,TRUE)"""),
            {"login": login, "password_hash": password_hash.hash(password)}).lastrowid
        project_id = conn.execute(text("INSERT INTO project (name) VALUES ('M12 首件项目')")).lastrowid
        conn.execute(text("""INSERT INTO project_member (project_id,user_id,role)
            VALUES (:project_id,:user_id,'admin')"""), {"project_id": project_id, "user_id": user_id})
    print("Initial account and project created.")


if __name__ == "__main__":
    main()
