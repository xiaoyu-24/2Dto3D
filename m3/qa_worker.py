import os
import sys
import time

sys.path.insert(0, "/app")
from app import m2_work, worker

assert os.environ.get("M3_QA") == "1"
original = m2_work.save_read


def checkpoint_then_wait(stage, evidence, images, final=False):
    committed = original(stage, evidence, images, final)
    if committed and not final:
        print(f"QA checkpoint committed stage={stage['id']} fence={stage['fence']}", flush=True)
        time.sleep(600)
    return committed


m2_work.save_read = checkpoint_then_wait
worker.main()
