import json
import re
from tempfile import SpooledTemporaryFile
from typing import Annotated
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import current_user, require_member
from app.db import engine, one, rows
from app.file_stream import archive_chunks, blob_chunks
from app.m1_delivery import ARTIFACTS
from app.m1_sample import encode_json

router = APIRouter(prefix="/api")
User = Annotated[dict, Depends(current_user)]
VERSION_SELECT = """SELECT v.*,s.batch_id,s.filename,LOWER(HEX(s.sha)) AS source_sha256,
    html.byte_count AS html_bytes,step.byte_count AS step_bytes,report.byte_count AS report_bytes
    FROM m2_version v JOIN product_task t ON t.id=v.task_id JOIN source_file s ON s.id=t.source_id
    LEFT JOIN file_bytes html ON html.sha=v.html_sha LEFT JOIN file_bytes step ON step.sha=v.step_sha
    LEFT JOIN file_bytes report ON report.sha=v.report_sha"""


class ZipInput(BaseModel):
    version_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)


def version_row(conn, version_id):
    row = one(conn, VERSION_SELECT + " WHERE v.id=:id", id=version_id)
    if not row:
        raise HTTPException(404, "交付版本不存在")
    return row


def filename(version, kind):
    stem = "-".join(version[key] for key in ("part_number", "version", "geometry_revision"))
    if version["state"] == "partial":
        stem += "-partial"
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("._-")
    return f"{clean[:160]}.{ARTIFACTS[kind][0]}"


def passed_check(version, kind):
    if not version[f"{kind}_check"] or not version[f"{kind}_sha"] or not version["geometry_sha"]:
        return False
    check = json.loads(version[f"{kind}_check"])
    return ((check.get("passed") is True or check.get("legacy_m1") in (True, 1))
            and check.get("geometry_sha256") == version["geometry_sha"].hex()
            and (check.get("legacy_m1") in (True, 1) or check.get(f"{kind}_sha256") == version[f"{kind}_sha"].hex()))


def passed_report(version):
    return bool(version["report_sha"] and version["step_check"]
                and json.loads(version["step_check"]).get("report_sha256") == version["report_sha"].hex())


def metadata(version):
    result = {key: version[key] for key in ("id", "task_id", "project_id", "batch_id", "part_number", "version", "geometry_revision",
                                           "state", "filename", "source_sha256", "published_at")}
    result["geometry_sha256"] = version["geometry_sha"].hex() if version["geometry_sha"] else None
    result["files"] = {}
    html_ready = passed_check(version, "html")
    published = version["state"] == "published" and html_ready and passed_check(version, "step") and passed_report(version)
    if (version["state"] == "published" and not published) or (version["state"] == "partial" and not html_ready):
        result["state"] = "rejected"
    eligible = ARTIFACTS if published else ("html",) if version["state"] == "partial" and html_ready else ()
    for kind in eligible:
        result["files"][kind] = {"id": f"{version['id']}-{kind}", "filename": filename(version, kind),
                                 "sha256": version[f"{kind}_sha"].hex(), "byte_count": version[f"{kind}_bytes"]}
    return result


@router.get("/projects/{project_id}/versions")
def versions(project_id: int, user: User, batch_id: Annotated[int | None, Query(gt=0)] = None,
             part_number: Annotated[str | None, Query(max_length=160)] = None,
             version: Annotated[str | None, Query(max_length=192)] = None):
    with engine.connect() as conn:
        require_member(conn, project_id, user["id"])
        found = rows(conn, VERSION_SELECT + """ WHERE v.project_id=:project AND (:batch IS NULL OR s.batch_id=:batch)
            AND (:part IS NULL OR v.part_number=:part) AND (:version IS NULL OR v.version=:version)
            ORDER BY v.id DESC LIMIT 100""", project=project_id, batch=batch_id, part=part_number, version=version)
        return [metadata(row) for row in found]


@router.get("/versions/{version_id}")
def version_detail(version_id: int, user: User):
    with engine.connect() as conn:
        row = version_row(conn, version_id)
        require_member(conn, row["project_id"], user["id"])
        return metadata(row)


def attachment(name):
    return {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"}


@router.get("/artifacts/{version_id}-{kind}/download")
def download(version_id: int, kind: str, user: User):
    with engine.connect() as conn:
        version = version_row(conn, version_id)
        require_member(conn, version["project_id"], user["id"])
        info = metadata(version)["files"].get(kind)
        if not info:
            raise HTTPException(404, "该文件当前不可下载")
        return StreamingResponse(blob_chunks(version[f"{kind}_sha"], info["byte_count"]), media_type=ARTIFACTS[kind][1], headers={
            **attachment(info["filename"]), "X-Content-SHA256": info["sha256"], "X-Delivery-State": version["state"],
            "Content-Length": str(info["byte_count"]),
        })


@router.post("/projects/{project_id}/zip")
def package(project_id: int, data: ZipInput, user: User):
    buffer = SpooledTemporaryFile(max_size=1024 * 1024)
    manifest = []
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn, ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        require_member(conn, project_id, user["id"])
        selected = [version_row(conn, version_id) for version_id in dict.fromkeys(data.version_ids)]
        if any(row["project_id"] != project_id for row in selected):
            raise HTTPException(404, "交付版本不属于当前项目")
        for row in selected:
            entry = metadata(row)
            for kind, artifact in entry["files"].items():
                path = f"{row['id']}-{row['state']}/{artifact['filename']}"
                artifact["path"] = path
                with archive.open(path, "w") as member:
                    for chunk in blob_chunks(row[f"{kind}_sha"], artifact["byte_count"]):
                        member.write(chunk)
            if entry["files"]:
                manifest.append(entry)
        if not manifest:
            raise HTTPException(409, "所选版本没有可下载的文件")
        archive.writestr("manifest.json", encode_json({"project_id": project_id, "versions": [
            {**entry, "published_at": entry["published_at"].isoformat() + "Z" if entry["published_at"] else None} for entry in manifest]}))
    return StreamingResponse(archive_chunks(buffer), media_type="application/zip",
                             headers={**attachment(f"project-{project_id}-deliveries.zip"), "Content-Length": str(buffer.tell())})


def register_first_delivery(conn, task_id):
    conn.execute(text("""INSERT INTO m2_version
        (task_id,project_id,part_number,version,basis_sha,checklist_sha,geometry_sha,html_sha,step_sha,report_sha,
         html_check,step_check,state,published_at)
        SELECT d.task_id,b.project_id,'M12A04-03-301-M16',CONCAT('A0-M1.3-',d.task_id),
        d.basis_sha,d.checklist_sha,d.geometry_sha,d.html_sha,
        d.step_sha,d.report_sha,JSON_OBJECT('passed',true,'legacy_m1',true,'geometry_sha256',LOWER(HEX(d.geometry_sha))),
        JSON_OBJECT('passed',true,'legacy_m1',true,'geometry_sha256',LOWER(HEX(d.geometry_sha)),
            'report_sha256',LOWER(HEX(d.report_sha))),'published',d.published_at
        FROM m1_delivery d JOIN product_task t ON t.id=d.task_id JOIN source_file s ON s.id=t.source_id
        JOIN upload_batch b ON b.id=s.batch_id WHERE d.task_id=:task ON DUPLICATE KEY UPDATE id=m2_version.id"""), {"task": task_id})
