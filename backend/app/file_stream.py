import hashlib

from app.db import engine, one

CHUNK_BYTES = 1024 * 1024


def blob_chunks(sha, byte_count):
    digest = hashlib.sha256()
    with engine.connect() as conn:
        for offset in range(0, byte_count, CHUNK_BYTES):
            row = one(conn, "SELECT SUBSTRING(body,:offset,:length) AS chunk FROM file_bytes WHERE sha=:sha",
                      offset=offset + 1, length=min(CHUNK_BYTES, byte_count - offset), sha=sha)
            chunk = row["chunk"] if row else b""
            if len(chunk) != min(CHUNK_BYTES, byte_count - offset):
                raise ValueError("交付文件字节数不匹配")
            digest.update(chunk)
            yield chunk
    if digest.digest() != sha:
        raise ValueError("交付文件 SHA-256 不匹配")


def archive_chunks(buffer):
    try:
        buffer.seek(0)
        while chunk := buffer.read(CHUNK_BYTES):
            yield chunk
    finally:
        buffer.close()
