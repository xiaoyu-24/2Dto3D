import os
import sys
from importlib.metadata import version
from pathlib import Path

from common import read_json, sha256, write_json
from m1_service import TOKEN_LIMITS, request_harness, settings

JOB = "m12-a04-m16-a0"


def select(basis_path, selection_path):
    if not settings():
        raise RuntimeError("M0 DeepSeek route is unavailable")
    basis = read_json(basis_path)
    prompt = f"""选择适合这个已冻结的 CAD 作业。图号 {basis['drawing_no']} / {basis['revision']}，
料号 {basis['part_number']}，针数 {basis['pin_count']}，轴向总长 {basis['total_length_mm']} mm，
配合端指向焊线端为 +Z。部件范围 {basis['scope']}。
候选 {JOB}：ENDE05/A0，M12A04-03-301-M16，四针，24.70 mm，
本体 A×1 B×4 C×1 E×1，D×1 独立且安装位姿未知，面板开孔参考。
完全匹配则仅返回 {JOB}，否则仅返回 no-match。不要生成或执行代码。"""
    TOKEN_LIMITS["cad_select"] = 128
    audit = request_harness(prompt, [], "cad_select")
    if not (audit.get("finish_reason") == "completed" and audit["http_status"] == 200
            and audit["request_id"] and (audit.get("response") or "").strip() == JOB):
        raise RuntimeError(f"CAD selection failed: HTTP {audit['http_status']}, "
                           f"{audit['finish_reason']}, {audit['error_type']}, request {audit['request_id']}")
    write_json(selection_path, {"job": JOB, "basis_sha256": sha256(basis_path),
               "job_sha256": sha256(Path(__file__).with_name("job.py")),
               "sdk": version("deepseek-harness-sdk"), "runtime": version("deepseek-harness-runtime-bin"),
               "selector_uid": os.getuid(), "model_run": audit})
    print(f"Selected {JOB}, request {audit['request_id']}")


if __name__ == "__main__":
    select(*(Path(argument) for argument in sys.argv[1:]))
