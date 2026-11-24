import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/app")
from app import worker

assert os.environ.get("M5_QA") == "1"
process = worker.process


def configured(stage):
    fixture = Path("/evidence/fault-prepared.json")
    interrupted = json.loads(fixture.read_bytes())["interrupted"] if fixture.exists() else None
    if interrupted and stage["id"] == interrupted["stage_id"]:
        with patch.dict(os.environ, {"M1_HARNESS_URL": "http://interruption-simulator:8011",
                                    "M1_VISION_PROJECT_ID": str(interrupted["project_id"])}):
            return process(stage)
    return process(stage)


worker.process = configured
worker.main()
