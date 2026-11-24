import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m5-step8"


def read(path):
    return json.loads(path.read_bytes())


def main():
    manifest = read(OUT / "load-manifest.json")
    upload = read(OUT / "upload.json")
    results = read(OUT / "load-results.json")
    metrics = read(OUT / "metrics.json")
    database = read(OUT / "linux/database.json")
    assert len(manifest["samples"]) == len(upload["receipts"]) == len(results["results"]) == 50
    assert len({item["sha256"] for item in manifest["samples"]}) == len({item["task_id"] for item in upload["receipts"]}) == 50
    assert len(upload["http_uploads"]) == 100 and sum(item["receipt"]["reused"] for item in upload["http_uploads"]) == 50
    for sample in manifest["samples"]:
        body = (OUT / "samples" / sample["file"]).read_bytes()
        assert len(body) == sample["bytes"] and hashlib.sha256(body).hexdigest() == sample["sha256"]
    for receipt in upload["receipts"]:
        assert any(stage["task_id"] == receipt["task_id"] and stage["stage_name"] == "m2_read" and stage["attempts"] > 0
                   for stage in database["task_stage"])
    assert results["ended_unix"] >= max(item["unix"] for item in read(OUT / "route-retries.json"))
    assert metrics["model_cost"]["simulated_calls_in_load"] == 0
    for pending in metrics["model_cost"]["unreconciled"]:
        assert not any(call["stage_id"] == pending["stage_id"] and call["started_at"] > pending["started_at"]
                       for call in database["m1_call"])
    hashes = []
    versions = read(OUT / "downloads/versions.json")
    with ZipFile(OUT / "downloads/deliveries.zip") as archive:
        zip_members = [{"path": name, "byte_count": len(archive.read(name)),
                        "sha256": hashlib.sha256(archive.read(name)).hexdigest()} for name in archive.namelist()]
        zipped = json.loads(archive.read("manifest.json"))
        for version in versions:
            matching = next(item for item in zipped["versions"] if item["id"] == version["id"])
            for kind, metadata in version["files"].items():
                body = (OUT / "downloads" / metadata["filename"]).read_bytes()
                assert archive.read(matching["files"][kind]["path"]) == body
                assert len(body) == metadata["byte_count"] and hashlib.sha256(body).hexdigest() == metadata["sha256"]
                hashes.append({"version": version["id"], "kind": kind, **metadata})
    for path in (OUT / "offline-first/edge-offline.json", OUT / "offline-m5/edge-offline.json"):
        check = read(path)
        assert check["passed"] and check["offline"] and check["channel"] == "msedge"
        assert check["page_errors"] == check["remote_requests"] == []
    assert read(OUT / "permissions/permissions.json")["zip_status"] == 404
    assert "67 passed" in (OUT / "linux/regression.log").read_text()
    assert "OK" in (OUT / "harness-test.log").read_text(encoding="utf-8-sig")
    baseline = {item["path"]: item["sha256"] for item in read(OUT / "baseline/source.json")["files"]}
    runtime = read(OUT / "linux/runtime-code.json")
    for name, digest in runtime["backend"].items():
        assert baseline["backend/" + name] == digest, name
    assert runtime["harness_sha256"] == hashlib.sha256((ROOT / "m0/harness/m1_service.py").read_bytes()).hexdigest()
    blind = read(OUT / "blind-freeze.json")
    assert blind["blind_samples"] == [] and set(blind["missing"]) == {"connector", "plate", "shaft", "gear", "assembly"}
    review = {"frozen_scoring_sha256": hashlib.sha256((OUT / "blind-freeze.json").read_bytes()).hexdigest(),
              "output_lock": {"eligible_outputs": [], "sha256": hashlib.sha256(b"[]").hexdigest()},
              "categories": [{"category": category, "samples": 0, "human_annotator": None, "annotation_sha256": None,
                              "status": "not_started_missing_qualified_sample", "critical_release_count": None,
                              "automatic_correct": "0/0 not calculable", "critical_release": "0/0 not calculable",
                              "confirmation": "0/0 not calculable"} for category in blind["categories"]]}
    (OUT / "blind-review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = {"at": datetime.now(timezone.utc).isoformat(), "evidence_consistent": True,
                    "step8_passed": False, "reason": "five independently human-annotated blind categories are missing; load products remain incomplete",
                    "files": hashes, "zip_members": zip_members,
                    "zip": {"byte_count": (OUT / "downloads/deliveries.zip").stat().st_size,
                            "sha256": hashlib.sha256((OUT / "downloads/deliveries.zip").read_bytes()).hexdigest()},
                    "faults": read(OUT / "linux/faults.json"), "metrics": metrics}
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence consistency PASS; 50 unique tasks, 50 reused uploads, 12 files and ZIP hashes; STEP 8 NOT PASSED")


if __name__ == "__main__":
    main()
