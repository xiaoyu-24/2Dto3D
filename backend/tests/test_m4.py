import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy import event, text

from app.db import engine
from app.file_stream import CHUNK_BYTES, blob_chunks
from app.ingest import put_bytes
from test_m2 import seed_version


def test_blob_chunks_detect_missing_and_wrong_lengths():
    body = b"m4" * (CHUNK_BYTES + 17)
    with engine.begin() as conn:
        sha = put_bytes(conn, body)
    chunks = list(blob_chunks(sha, len(body)))
    assert b"".join(chunks) == body
    assert max(map(len, chunks)) == CHUNK_BYTES
    with pytest.raises(ValueError, match="字节数"):
        list(blob_chunks(sha, len(body) + 1))
    with pytest.raises(ValueError, match="SHA-256"):
        list(blob_chunks(sha, len(body) - 1))
    with pytest.raises(ValueError, match="字节数"):
        list(blob_chunks(b"\x01" * 32, 1))


def test_download_and_zip_use_bounded_reads(client, account):
    _, version, _ = seed_version(account["project"], account["id"], "M4-stream")
    body = hashlib.shake_256(b"M4 boundary").digest(2 * CHUNK_BYTES + 31)
    with engine.begin() as conn:
        sha = put_bytes(conn, body)
        conn.execute(text("""UPDATE m2_version SET step_sha=:sha,
            step_check=JSON_SET(step_check,'$.step_sha256',:hex) WHERE id=:id"""),
                     {"id": version, "sha": sha, "hex": sha.hex()})
    reads = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "SUBSTRING(body" in statement:
            reads.append(parameters["length"])

    event.listen(engine, "before_cursor_execute", capture)
    try:
        downloaded = client.get(f"/api/artifacts/{version}-step/download")
        assert downloaded.status_code == 200 and downloaded.content == body
        assert downloaded.headers["content-length"] == str(len(body))
        assert downloaded.headers["x-content-sha256"] == sha.hex()
        zipped = client.post(f"/api/projects/{account['project']}/zip", json={"version_ids": [version]})
        assert zipped.status_code == 200
        assert int(zipped.headers["content-length"]) == len(zipped.content)
        with ZipFile(BytesIO(zipped.content)) as archive:
            metadata = json.loads(archive.read("manifest.json"))["versions"][0]["files"]["step"]
            assert archive.read(metadata["path"]) == body
            assert metadata["sha256"] == sha.hex() and metadata["byte_count"] == len(body)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(reads) >= 6 and max(reads) <= CHUNK_BYTES
