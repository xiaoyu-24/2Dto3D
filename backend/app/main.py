import json
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.auth import current_user, login_user, logout_user, require_member
from app.db import engine, one, rows
from app.ingest import MAX_PDF_BYTES, accept_file, create_batch
from app.m1_routes import router as m1_router
from app.m2_routes import router as m2_router
from app.m2_versions import router as version_router

app = FastAPI(title="图纸任务平台")
app.include_router(m1_router)
app.include_router(m2_router)
app.include_router(version_router)
User = Annotated[dict, Depends(current_user)]


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    if request.method == "POST" and request.headers.get("X-Requested-With") != "drawing-web":
        return JSONResponse({"detail": "请求缺少同源校验头"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request: Request, error: SQLAlchemyError):
    return JSONResponse({"detail": "数据库暂不可用，请重试原请求以核对接收结果"}, status_code=503)


class LoginInput(BaseModel):
    login: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=1024)


class BatchInput(BaseModel):
    request_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        one(conn, "SELECT 1")
    return {"ok": True}


@app.post("/api/login")
def login(data: LoginInput, response: Response):
    return login_user(data.login, data.password, response)


@app.post("/api/logout")
def logout(request: Request, response: Response):
    return logout_user(request, response)


@app.get("/api/me")
def me(user: User):
    return user


@app.get("/api/projects")
def projects(user: User):
    with engine.connect() as conn:
        return rows(conn, """SELECT p.id,p.name,m.role FROM project p
            JOIN project_member m ON m.project_id=p.id WHERE m.user_id=:user ORDER BY p.id""", user=user["id"])


@app.post("/api/projects/{project_id}/batches")
def new_batch(project_id: int, data: BatchInput, user: User):
    return create_batch(project_id, user["id"], data.request_key)


@app.get("/api/projects/{project_id}/batches")
def batches(project_id: int, user: User):
    with engine.connect() as conn:
        require_member(conn, project_id, user["id"])
        return rows(conn, """SELECT id,created_at FROM upload_batch
            WHERE project_id=:project ORDER BY id DESC LIMIT 100""", project=project_id)


@app.post("/api/batches/{batch_id}/files", status_code=202)
def upload(batch_id: int, user: User,
           client_file_key: Annotated[str, Form(pattern=r"^[A-Za-z0-9_-]{1,96}$")],
           file: Annotated[UploadFile, File()], workflow: Annotated[Literal["m1", "m2"], Form()] = "m1"):
    filename = (file.filename or "drawing.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 255:
        raise HTTPException(422, "文件名长度须为 1–255 个字符")
    return accept_file(batch_id, user["id"], client_file_key, filename, file.file.read(MAX_PDF_BYTES + 1), workflow)


@app.get("/api/batches/{batch_id}")
def batch_detail(batch_id: int, user: User):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
        batch = one(conn, "SELECT id,project_id,created_at FROM upload_batch WHERE id=:id", id=batch_id)
        if not batch:
            raise HTTPException(404, "批次不存在")
        require_member(conn, batch["project_id"], user["id"])
        files = rows(conn, """SELECT f.id,f.filename,LOWER(HEX(f.sha)) AS sha256,f.state AS receipt,
            JSON_UNQUOTE(JSON_EXTRACT(f.selection_json,'$.workflow')) AS workflow,
            t.id AS task_id,t.state,s.state AS stage_state,s.attempts,s.error_text
            FROM source_file f JOIN product_task t ON t.source_id=f.id AND t.scope_key='file'
            JOIN task_stage s ON s.id=(SELECT MAX(id) FROM task_stage WHERE task_id=t.id)
            WHERE f.batch_id=:id ORDER BY f.id""", id=batch_id)
        products = rows(conn, """SELECT t.source_id,t.id AS task_id,p.part_number,t.state,q.state AS stage_state,q.error_text,
            v.id AS version_id,v.version FROM m2_product p JOIN product_task t ON t.id=p.task_id
            JOIN source_file f ON f.id=t.source_id LEFT JOIN m2_version v ON v.task_id=t.id
            JOIN task_stage q ON q.id=(SELECT MAX(id) FROM task_stage WHERE task_id=t.id)
            WHERE f.batch_id=:id ORDER BY t.id""", id=batch_id)
    grouped = {file["id"]: [] for file in files}
    for product in products:
        grouped[product["source_id"]].append(product)
    return {**dict(batch), "files": [{**file, "products": grouped[file["id"]]} for file in files]}


def authorized_task(conn, task_id: int, user_id: int):
    task = one(conn, """SELECT t.id,t.state,f.filename,b.project_id,s.id AS stage_id,s.state AS stage_state,
        s.attempts,s.error_text,LOWER(HEX(s.input_sha)) AS sha256,s.output_sha,
        JSON_UNQUOTE(JSON_EXTRACT(f.selection_json,'$.workflow')) AS workflow, p.part_number
        FROM product_task t JOIN source_file f ON f.id=t.source_id
        JOIN upload_batch b ON b.id=f.batch_id LEFT JOIN m2_product p ON p.task_id=t.id
        JOIN product_task shared ON shared.source_id=f.id AND shared.scope_key='file'
        JOIN task_stage s ON s.task_id=shared.id AND s.stage_name='pdf_extract'
        WHERE t.id=:id""", id=task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    require_member(conn, task["project_id"], user_id)
    return dict(task)


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: int, user: User):
    with engine.connect() as conn:
        task = authorized_task(conn, task_id, user["id"])
        output_sha = task.pop("output_sha")
        task["checkpoint"] = None
        if output_sha:
            result = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=output_sha)
            task["checkpoint"] = json.loads(result["body"])
    return task


@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: int, user: User, after: Annotated[int, Query(ge=0)] = 0):
    with engine.connect() as conn:
        authorized_task(conn, task_id, user["id"])
        events = rows(conn, """SELECT e.id,e.kind,e.fence,e.detail,e.created_at,s.stage_name FROM stage_event e
            JOIN task_stage s ON s.id=e.stage_id WHERE s.task_id=:task AND e.id>:after ORDER BY e.id LIMIT 100""",
                      task=task_id, after=after)
    return [{**row, "detail": json.loads(row["detail"])} for row in events]
