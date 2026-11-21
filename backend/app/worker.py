import logging
import subprocess
import signal
import socket
import threading
import uuid

from sqlalchemy.exc import SQLAlchemyError

from app.db import engine, one
from app.m1_read import read_stage, save_read
from app.m2_work import fail_stage, run_stage as run_m2
from app.pdf import extract_pdf
from app.queue import claim, finish, renew

logger = logging.getLogger("worker")


def process(stage):
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(20):
            try:
                if not renew(stage):
                    return
            except SQLAlchemyError:
                logger.warning("Lease renewal failed for stage %s", stage["id"])
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if stage["stage_name"] in ("m2_read", "m2_prepare"):
            try:
                return run_m2(stage)
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
                logger.warning("M2 stage %s failed: %s", stage["id"], str(error)[:300])
                return fail_stage(stage, str(error)[:1000])
        if stage["stage_name"] == "vision_read":
            try:
                return read_stage(stage)
            except (ValueError, subprocess.SubprocessError) as error:
                logger.warning("Evidence preparation failed for stage %s: %s", stage["id"], type(error).__name__)
                return save_read(stage, {}, "read_pending", "原图指纹、解析检查点或页图渲染校验失败")
        with engine.connect() as conn:
            source = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=stage["input_sha"])
        try:
            checkpoint = extract_pdf(source["body"])
        except Exception as error:
            logger.warning("PDF parsing failed for stage %s: %s", stage["id"], type(error).__name__)
            return finish(stage, error=f"无法解析 PDF（{type(error).__name__}），请检查原文件")
        return finish(stage, checkpoint=checkpoint)
    finally:
        stopped.set()
        thread.join()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    owner = f"{socket.gethostname()}:{uuid.uuid4()}"
    logger.info("Worker started: %s", owner)
    while not stopped.is_set():
        try:
            stage = claim(owner)
            if stage:
                logger.info("Claimed stage=%s task=%s fence=%s", stage["id"], stage["task_id"], stage["fence"])
                committed = process(stage)
                logger.info("Finished stage=%s committed=%s", stage["id"], committed)
                continue
        except SQLAlchemyError:
            logger.warning("Database operation failed; work remains recoverable by lease expiry")
        stopped.wait(1)


if __name__ == "__main__":
    main()
