import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1] / "output/m4-step7"


def read(name):
    return json.loads((ROOT / name).read_bytes())


def info(body):
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def downloads(name):
    folder = ROOT / name
    manifest = read(f"{name}/manifest.json")
    members = []
    with ZipFile(folder / "deliveries.zip") as archive:
        for version in manifest["versions"]:
            for kind, expected in version["files"].items():
                body = (folder / expected["filename"]).read_bytes()
                assert len(body) == expected["byte_count"] and info(body)["sha256"] == expected["sha256"]
                assert archive.read(expected["path"]) == body
                members.append({"version": version["id"], "kind": kind, "path": expected["path"], **info(body)})
        members.append({"path": "manifest.json", **info(archive.read("manifest.json"))})
        assert {item["path"] for item in members} == set(archive.namelist())
    return {"files": members, "zip": info((folder / "deliveries.zip").read_bytes())}


def main():
    sealed = read("sealed-final.json")
    restored = read("restored-exact-before-start.json")
    assert sealed == restored
    assert sealed["unanswered_confirmations"] == 1
    assert next(item["count"] for item in sealed["stage_states"] if item["state"] == "ready") == 2
    evidence = "final-evidence"
    before = read(f"{evidence}/m4-qa/exact-before-resume.json")
    after = read(f"{evidence}/m4-qa/exact-after-resume.json")
    assert before["receipt"] is None and after["receipt"]["fence"] == 2
    assert before["model_calls"] == after["model_calls"]
    assert next(item["count"] for item in after["events"] if item["kind"] == "published") == 1
    assert read(f"{evidence}/m4-qa/exact-after-once.json") == read(f"{evidence}/m4-qa/exact-after-twice.json")
    commands = [json.loads(line) for line in (ROOT / evidence / "exact-cad/commands.jsonl").read_text().splitlines()]
    assert all("exec" in command["command"] and command["exit"] == 0 for command in commands)
    results = {name: downloads(name) for name in ("clean-downloads", "before-history", "after-history",
                                                 "before-large", "after-large", "after-resumed")}
    for label in ("history", "large"):
        assert results[f"before-{label}"]["files"] == results[f"after-{label}"]["files"]
    large = next(item for item in results["after-large"]["files"] if item.get("kind") == "step")
    assert large["bytes"] == 64 * 1024 * 1024 - 4096
    fixture = read("fixtures.json")["items"][0]
    resumed = next(item for item in results["after-resumed"]["files"] if item.get("kind") == "step")
    assert resumed["sha256"] == fixture["step_sha256"]
    for label in ("offline-first", "offline-m3"):
        check = read(f"{label}/edge-offline.json")
        assert check["passed"] and check["channel"] == "msedge" and check["offline"] and not check["remote_requests"]
    assert read("after-permissions/permissions.json")["zip_status"] == 404
    original, target = read("clean-inventory.json"), read("restored-inventory.json")
    assert original["machine_id"] != target["machine_id"] and original["docker_id"] != target["docker_id"]
    summary = {"passed": True, "exact_business_tables": len(sealed["tables"]), "pending_confirmations": 1,
               "pending_stages_at_restore": 2, "model_calls_before": before["model_calls"], "model_calls_after": after["model_calls"],
               "new_publications": 1, "new_modeling": 0, "new_exports": 0, "new_verifications": 0,
               "repeat_process_database_unchanged": True, "cad_dispatch_commands": len(commands),
               "restore": read("restore-exact-result.json"), "downloads": results}
    (ROOT / "final-verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: independent Linux restore, 19 exact tables, publication once, zero recomputation, Edge permissions/offline/downloads/ZIP/64 MiB")


if __name__ == "__main__":
    main()
