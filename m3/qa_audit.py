import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.cad_worker import Executor, ROOT, write_json
from m3.qa_cad import PROJECT, database


def main():
    folder = ROOT / "output" / "m3-step6"
    before = json.loads((folder / "before-db-recovery.json").read_text(encoding="utf-8-sig"))
    after = json.loads((folder / "after-db-recovery.json").read_text(encoding="utf-8-sig"))
    stage_ids = {item["id"] for item in after["task_stage"] if item["task_id"] == 14}
    calls = lambda audit: [item for item in audit["m1_call"] if item["stage_id"] in stage_ids]
    assert calls(before) == calls(after)
    versions = lambda audit: next(item for item in audit["m2_version"] if item["task_id"] == 14)
    assert versions(before)["geometry_sha"] == versions(after)["geometry_sha"]
    assert versions(before)["html_sha"] == versions(after)["html_sha"]
    pending = folder / "cad" / "pending"
    executor = Executor(PROJECT, folder / "cad")
    rejected = []
    for action in ("frozen", "html", "step"):
        path = next(path for path in pending.glob(f"21-*/{action}.json"))
        payload = json.loads(path.read_bytes())
        try:
            executor.mysql(action, payload["stage"], **payload["arguments"])
        except ValueError as failure:
            assert "租约" in str(failure)
            rejected.append(action)
        else:
            raise AssertionError(f"stale {action} accepted")
    prior = database("audit")
    path = next(pending.glob("23-*/step.json"))
    payload = json.loads(path.read_bytes())
    duplicate = executor.mysql("step", payload["stage"], **payload["arguments"])
    assert duplicate["reused"] is True
    final = database("audit")
    assert final == prior
    for task in (14, 16, 18, 19):
        version = next(item for item in final["m2_version"] if item["task_id"] == task)
        publications = [item for item in final["m3_publication"] if item["version_id"] == version["id"]]
        assert len(publications) == 1 and publications[0]["geometry_revision"] == version["geometry_revision"]
        for kind in ("geometry", "html", "step", "report"):
            assert publications[0][f"{kind}_sha"] == version[f"{kind}_sha"]
    for task in (18, 19):
        version = next(item for item in final["m2_version"] if item["task_id"] == task)
        assert version["geometry_revision"] == "G2"
        archive = next(item for item in final["m3_revision"] if item["version_id"] == version["id"])
        evidence = json.loads(archive["evidence"])
        assert archive["geometry_revision"] == "G1" and evidence["state"] == "rejected"
    record = {"publication_count": len(final["m3_publication"]), "stale_writes_rejected": rejected,
              "duplicate_receipt": duplicate, "model_delta_on_database_recovery": 0,
              "html_and_geometry_unchanged": True, "wrong_geometry_publications": 0, "database": final}
    write_json(folder / "final-audit.json", record)
    print(json.dumps({key: value for key, value in record.items() if key != "database"}))


if __name__ == "__main__":
    main()
