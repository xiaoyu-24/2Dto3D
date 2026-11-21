import os
import uuid
from pathlib import Path

import pymysql
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

os.environ["DB_NAME"] = "drawings_test_" + uuid.uuid4().hex
admin = pymysql.connect(host=os.environ["DB_HOST"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
with admin.cursor() as cursor:
    cursor.execute(f"CREATE DATABASE `{os.environ['DB_NAME']}` CHARACTER SET utf8mb4")

from app.auth import password_hash
from app.db import engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def database():
    command.upgrade(Config("alembic.ini"), "head")
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()
    with admin.cursor() as cursor:
        cursor.execute(f"DROP DATABASE `{os.environ['DB_NAME']}`")
    admin.close()


@pytest.fixture
def account():
    suffix = uuid.uuid4().hex
    login = f"user-{suffix}"
    password = "M12-test-password-only"
    with engine.begin() as conn:
        user_id = conn.execute(text("INSERT INTO app_user (login,password_hash) VALUES (:login,:hash)"),
                               {"login": login, "hash": password_hash.hash(password)}).lastrowid
        project_id = conn.execute(text("INSERT INTO project (name) VALUES (:name)"), {"name": suffix}).lastrowid
        conn.execute(text("INSERT INTO project_member (project_id,user_id,role) VALUES (:p,:u,'operator')"),
                     {"p": project_id, "u": user_id})
    return {"login": login, "password": password, "id": user_id, "project": project_id}


@pytest.fixture
def client(account):
    with TestClient(app, headers={"X-Requested-With": "drawing-web"}) as client:
        response = client.post("/api/login", json={"login": account["login"], "password": account["password"]})
        assert response.status_code == 200
        yield client


@pytest.fixture(scope="session")
def m12():
    return Path(os.environ.get("M12_PDF", "/fixtures/m12.pdf")).read_bytes()
