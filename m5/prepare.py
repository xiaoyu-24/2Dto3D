import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m5-step8"
CATEGORIES = ("connector", "plate", "shaft", "gear", "assembly")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if (OUT / "load-manifest.json").exists():
        raise ValueError("Frozen load manifest already exists; reuse its retained samples")
    directory = OUT / "samples"
    directory.mkdir(parents=True, exist_ok=True)
    paths = [Path("D:/desktop/测试文件.pdf"), *sorted(Path("D:/projects/3D/测试文件").glob("*.pdf"))]
    paths += sorted(path for path in Path("D:/projects/PDFAI/storage/uploads").glob("*")
                    if path.suffix.lower() == ".pdf" and path.stat().st_size > 10000)
    originals = []
    seen = set()
    for path in paths:
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        filename = re.sub(r"^[0-9a-f]{32}_", "", path.stem)
        if path.name == "测试文件.pdf":
            targets = ["M12A04-03-301-M16", "M12A05-03-301-M16"]
        elif filename.startswith("WL-B21"):
            with fitz.open(stream=body, filetype="pdf") as document:
                candidates = re.findall(r"AUM[0-9A-Za-z/.-]+", " ".join(page.get_text() for page in document))
            targets = [max(sorted(set(candidates)), key=candidates.count)]
        else:
            targets = [filename.replace(" ", "/")]
        originals.append({"origin": str(path), "original_sha256": digest, "targets": targets,
                          "category": "connector" if len(originals) < 5 else "assembly",
                          "development_sample": len(originals) < 5})
    samples = []
    for index in range(50):
        original = originals[index % len(originals)]
        body = Path(original["origin"]).read_bytes()
        operation = "original"
        if index >= len(originals):
            document = fitz.open(stream=body, filetype="pdf")
            if index < 2 * len(originals):
                operation = "rotate-every-page-90"
                for page in document:
                    page.set_rotation((page.rotation + 90) % 360)
            else:
                operation = "append-second-drawing"
                companion = originals[(index + 1) % len(originals)]
                with fitz.open(companion["origin"]) as other:
                    document.insert_pdf(other)
            body = document.tobytes(garbage=4, deflate=True, no_new_id=True)
            document.close()
        name = f"L{index + 1:02d}.pdf"
        (directory / name).write_bytes(body)
        with fitz.open(stream=body, filetype="pdf") as document:
            entry = {**original, "id": f"L{index + 1:02d}", "file": name, "sha256": hashlib.sha256(body).hexdigest(),
                     "bytes": len(body), "pages": len(document), "rotations": [page.rotation for page in document],
                     "operation": operation, "blind_eligible": False}
        if operation == "append-second-drawing":
            entry.update(companion_original_sha256=companion["original_sha256"],
                         targets=[*entry["targets"], *companion["targets"]])
        samples.append(entry)
    assert len({sample["sha256"] for sample in samples}) == 50
    frozen = {"frozen_at": datetime.now(timezone.utc).isoformat(), "original_count": len(originals),
              "sample_count": len(samples), "originals": originals, "samples": samples,
              "dedup_rule": "same batch and client file key must reuse original source/task; repeats excluded from denominator",
              "scope": "21 existing mechanical drawings plus 29 structural load fixtures; no independently annotated blind data"}
    save(OUT / "load-manifest.json", frozen)
    scoring = {"frozen_at": frozen["frozen_at"], "categories": list(CATEGORIES), "blind_samples": [],
               "required_provenance": ["independent human annotator identity", "not used during development",
                                       "source sha256 and target part frozen before execution",
                                       "sealed annotation hash before execution; reveal only after output lock"],
               "critical_checks": ["target model", "key dimensions and drawing tolerances", "view and handedness",
                                   "valid solids and quantity", "assembly instance transforms"],
               "pass_rule": "all applicable drawing checks pass; missing evidence requires confirmation; critical published error is a release",
               "denominators": {"automatic_correct": "correct published without human supplementation / input target versions",
                                "critical_release": "published with critical errors / published versions",
                                "confirmation": "target versions entering confirmation / input target versions"},
               "zero_denominator": "not calculable", "missing": {category: "no eligible independent human annotation supplied" for category in CATEGORIES}}
    save(OUT / "blind-freeze.json", scoring)
    print(json.dumps({"originals": len(originals), "load_files": len(samples), "blind_samples": 0,
                      "manifest_sha256": hashlib.sha256((OUT / "load-manifest.json").read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
