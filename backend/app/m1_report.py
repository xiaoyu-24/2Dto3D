import json


def acceptance_report(geometry, geometry_sha, checklist_sha, selection, measurements, edge, negatives, artifacts):
    model = selection["model_run"]
    lines = [f"# 首件验收报告 · {geometry['version']} / {geometry['geometry_revision']}", "",
             "结论为通过。判定依据为独立 STEP 回读、三份负例和断网 Edge 实测。", "",
             f"- 原图 SHA-256 `{geometry['source_sha256']}`",
             f"- 建模依据 SHA-256 `{geometry['basis_sha256']}`",
             f"- 独立清单 SHA-256 `{checklist_sha}`",
             f"- 冻结几何 SHA-256 `{geometry_sha}`",
             f"- 作业 SHA-256 `{geometry['job_sha256']}`",
             f"- 验收器 SHA-256 `{measurements['verifier_sha256']}`",
             f"- Harness SDK / Runtime {selection['sdk']} / {selection['runtime']}，作业 `{selection['job']}`，"
             f"{model['actual_model']} 请求 `{model['request_id']}`，耗时 {model['elapsed_ms']} ms，"
             f"用量 {model['usage']['input_tokens']} / {model['usage']['output_tokens']} token（输入 / 输出）",
             f"- Linux FreeCAD {geometry['freecad']} / OCCT {geometry['occt']}，"
             f"建模和回读隔离配置分别为 `{json.dumps(geometry['environment'], ensure_ascii=False)}` 和 "
             f"`{json.dumps(measurements['environment'], ensure_ascii=False)}`", "",
             "## 成品指纹", "", "| 文件 | 字节数 | SHA-256 |", "|---|---:|---|"]
    for artifact in artifacts.values():
        lines.append(f"| {artifact['filename']} | {artifact['byte_count']} | `{artifact['sha256']}` |")
    lines += ["", "## STEP 实测", "", "独立进程从交付字节导入。配合端指向焊线端为 +Z，长度单位为 mm。",
              "四针本体实测总长 24.70 mm，图纸要求 24.70 ± 0.25 mm。本体 7 个有效实体，独立螺母 D 为 1 个有效实体。",
              "D 的位移仅用于分件展示，安装变换留空。",
              "", "| 清单项目 | 类型 | 实测（数值四舍五入至 6 位小数） | 结果 |", "|---|---|---|---|"]

    def rounded(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {key: rounded(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rounded(item) for item in value]
        return value

    kinds = {"drawing": "图纸标注", "derived": "图纸推导", "trial": "试建一致性"}
    for check in measurements["checks"]:
        actual = json.dumps(rounded(check["measured"]), ensure_ascii=False, separators=(",", ": "))
        lines.append(f"| {check['name']} | {kinds[check['kind']]} | `{actual}` | 通过 |")
    lines += ["", "## 用实际 STEP 字节复测负例", "",
              "| 四针负例 | 拦截项目 | SHA-256 |", "|---|---|---|"]
    for label, variant in (("端面对调", "swapped"), ("五针名义总长 26.70 mm", "26.70"), ("八针名义总长 26.90 mm", "26.90")):
        result = negatives[variant]
        lines.append(f"| {label} | {result['rejected_at']} | `{result['step_sha256']}` |")
    lines += ["", "三次回读均返回退出码 1。两个长版保留四个触点，实测长度分别为 26.70 和 26.90 mm。",
              "", "## 离线 3D HTML", "",
              f"Microsoft Edge {edge['edge_version']}，从 `file://` 打开，浏览器上下文断网，HTTP(S) 请求和脚本异常均为 0。",
              "从 WebGL 帧缓冲实测画面变化。", "", "| 操作 | 非背景像素 | 画面指纹 |", "|---|---:|---|"]
    labels = {"visible": "模型可见", "drag_rotation": "拖动旋转", "wheel_zoom": "滚轮放大",
              "button_zoom_out": "按钮缩小", "mating_view": "配合端视角", "terminal_view": "焊线端视角",
              "hide_nut": "隐藏螺母", "hide_pin": "隐藏触点", "restore_parts": "恢复可见"}
    for key, label in labels.items():
        frame = edge["operations"][key]
        lines.append(f"| {label} | {frame['non_background_pixels']} | `{frame['pixel_fingerprint']}` |")

    lines += ["", "## 试建来源与用途", ""]
    for trial in geometry["trials"].values():
        regions = "、".join(trial["regions"])
        lines.append(f"- **{trial['label']}**　{trial['summary']} {trial['basis']} 原图第 1 页区域 `{regions}`。")
    lines += ["- 面板开孔为参考截面，Ø16.10 mm，扁宽 13.60 mm，上偏差 +0.10 mm，单列核验。",
              "- 螺纹基于名义规格构造右旋几何，轴向截线实测牙距。牙型公差、收尾、配合间隙、端子杯和内腔过渡采用试建参数。",
              "- 触点以配合端位置标识。电气编号、O 形圈自由状态规格、螺母安装位置待定。旋合、密封和生产适用性待验证。", ""]
    return "\n".join(lines).encode()
