import logging
import signal
import socket
import threading
import uuid

from sqlalchemy.exc import SQLAlchemyError

from app.db import engine, one
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
