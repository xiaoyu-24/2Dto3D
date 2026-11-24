import re
from copy import deepcopy

from app.m1_acceptance import independent_checklist
from app.m1_sample import PART, REGIONS, SOURCE, trial_parameters

FIVE_PIN = "M12A05-03-301-M16"
WLT = "15f5aef63a2e3f1b55ad6362e79bc57c73c3587d4be8b9e096f8853028bf4743"
B040 = "a99425986b8e6e483afb18b3a63f5dde00d78f9d0912f651333732a101050173"
TEMPLATES = {
    B040: ("B040108M0-2XX2XX01", re.compile(r"B040108M0-2(10|20|24|30|34|40|50|60)2[ACD5][AS]01"), "B040108M0-2102AA01"),
    WLT: ("WLT2F9-141XX0200", re.compile(r"WLT2F9-141[1256]A0200"), "WLT2F9-1415A0200"),
}


def eligible_products(source, products):
    template = TEMPLATES.get(source)
    if not template:
        return products
    return [product for product in products
            if product["part_number"] == template[0] or template[1].fullmatch(product["part_number"])]


def template_input(source, part_number):
    template = TEMPLATES.get(source)
    return template[2] if template and template[0] == part_number else None


def resolve_variant(source, part_number, products):
    template = TEMPLATES.get(source)
    if not template or template[0] not in products:
        return None
    match = template[1].fullmatch(part_number)
    if not match:
        return None
    specification = products[template[0]]
    return {**specification, "part_number": part_number, "pin_count": int(match[1]) if source == B040 else 14,
            "total_length_mm": None, "questions": [{"key": "configuration", "regions": specification["questions"][0]["regions"],
                "question": f"请对照原图确认 {part_number} 的针数和配置，并确认产品范围为第 1 页。第 2 页为包装。",
                "expected": f"确认 {part_number}"}]}


def region(page, label, box, width=1263, height=893):
    left, top, right, bottom = box
    return {"page": page, "label": label, "rect": [left / width, top / height, right / width, bottom / height]}


def sample_evidence(source, reading):
    products = {item["part_number"]: item for item in eligible_products(source, reading["products"])}
    regions = dict(reading["regions"])
    if source == SOURCE:
        for key, (label, box) in REGIONS.items():
            regions[f"verified-{key}"] = region(1, label, box, 1888, 1334)
        for part, pin_count, length, row, pins, dim in (
            (PART, 4, "24.70", (27, 509, 472, 530), (173, 59, 230, 150), (664, 116, 852, 143)),
            (FIVE_PIN, 5, "26.70", (27, 529, 472, 551), (226, 59, 283, 150), (665, 98, 860, 119)),
        ):
            variant, key, total = (f"verified-{name}-{pin_count}" for name in ("variant", "key", "length"))
            regions.update({variant: region(1, f"{pin_count} 针料号行", row), key: region(1, f"A-code {pin_count} 针针位与针径", pins),
                            total: region(1, f"{pin_count} 针总长 {length} mm", dim)})
            raw = products.get(part, {})
            questions = []
            if raw.get("questions") or raw.get("total_length_mm") not in (length, length.rstrip("0")) or raw.get("pin_count") != pin_count:
                questions.append({"key": "verified-length", "question": f"模型对 {part} 的型号依据有疑问。请核对料号行、针位图和总长引出线。",
                                  "regions": [variant, key, total], "expected": f"{pin_count} 针 / {length} mm"})
            if pin_count == 5:
                questions.append({"key": "five-pin-view", "question": "剖视两端示例画的是四针。请确认五针版以 A-code 5pin 栏作为配合端针位依据，剖视保留配合端至焊线端的轴向基准。",
                                  "regions": [key, "verified-mating", "verified-section"], "expected": "采用 A-code 5pin 栏"})
            products[part] = {**raw, "part_number": part, "drawing_no": "ENDE05", "revision": "A0", "family": "M12 A-code M16",
                              "pin_count": pin_count, "total_length_mm": length, "regions": [variant, key], "questions": questions,
                              "findings": [{"label": "本体轴向总长", "value": f"{length} mm ±0.25 mm", "kind": "drawing",
                                            "regions": [total, variant, "verified-title"]},
                                           {"label": "配合端针位", "value": f"A-code {pin_count} 针，图示针径 X=1.00 mm", "kind": "drawing", "regions": [key]},
                                           {"label": "轴向基准", "value": "沿带键配合端指向带安装扁面的焊线端为 +Z。",
                                            "kind": "derived", "regions": ["verified-mating", "verified-terminal", "verified-section"]}]}
    if source in (WLT, B040):
        if source == WLT:
            part = TEMPLATES[WLT][0]
            boxes = {"model": (1, "Type-C 料号和配置表", (891, 378, 1094, 478)),
                     "dimension": (1, "侧视 14.00 ±0.20 mm", (85, 136, 333, 398)),
                     "package": (2, "包装尺寸与数量", (435, 619, 640, 727))}
            pin_count, family = 14, "Type-C 14P/F DIP"
            question = "请对照料号表确认镀层码与颜色码。第二页为包装，建模范围仅包含第一页的产品。"
        else:
            part = TEMPLATES[B040][0]
            boxes = {"model": (1, "板对板料号配置", (858, 427, 1184, 674)),
                     "dimension": (1, "各针数的 A/B/C 尺寸表", (561, 536, 795, 735)),
                     "package": (2, "卷盘包装规格表", (41, 440, 585, 742))}
            pin_count, family = None, "板对板 0.4 mm"
            question = "请确认实际针数、镀层与颜色与所选料号一致，并明确建模范围为第一页产品。"
        regions.update({f"verified-{key}": region(*arguments) for key, arguments in boxes.items()})
        raw = products.get(part, {})
        products[part] = {**raw, "part_number": part, "drawing_no": "ENDE05", "revision": "A0", "family": family,
                          "pin_count": pin_count, "total_length_mm": None, "regions": ["verified-model", "verified-package"],
                          "findings": [{"label": boxes["dimension"][1], "value": "按原页引出线及表格核对", "kind": "drawing",
                                        "regions": ["verified-dimension"]}, *raw.get("findings", [])],
                          "questions": [{"key": "configuration", "question": question,
                                         "regions": ["verified-model", "verified-dimension", "verified-package"]},
                                        *raw.get("questions", [])]}
    return list(products.values()), regions


def frozen_inputs(evidence, specification, version, answers):
    part = specification["part_number"]
    equivalent = evidence.get("equivalent_read", {})
    reviewed = evidence["source_sha256"] == SOURCE or (equivalent.get("peer_source_sha256") == SOURCE
              and len(equivalent.get("page_identity", [])) == 1 and equivalent.get("peer_stage")
              and evidence["model_run"].get("source_stage_id") == equivalent["peer_stage"])
    if not reviewed or part not in (PART, FIVE_PIN):
        return None
    pins = 4 if part == PART else 5
    length = "24.70" if pins == 4 else "26.70"
    trials = deepcopy(trial_parameters())
    if pins == 5:
        trial = trials["pins"]
        trial["label"] = "五针中心"
        trial["values"]["centers_xy_mm"] = [*trial["values"]["centers_xy_mm"], [0, 0]]
        trial["summary"] = "五针按 A-code 图，以轴心为原点，外围四针取 X、Y 各 ±1.75 mm，另一个针心位于轴心。"
        trial["basis"] = "角位间距从端面按 SW18 估读。五针中心有独立针位表证据。"
    regions = {key: evidence["regions"][f"verified-{key}"] for key in REGIONS}
    images = {key: evidence["images"][f"verified-{key}"] for key in REGIONS}
    for alias, source in (("variant", f"verified-variant-{pins}"), ("key", f"verified-key-{pins}"),
                          ("variant-length", f"verified-length-{pins}")):
        regions[alias], images[alias] = evidence["regions"][source], evidence["images"][source]
    if pins == 5:
        trials["pins"]["regions"] = ["key"]
    spec = {"source_sha256": evidence["source_sha256"], "regions": regions, "images": images, "trials": trials}
    checklist = deepcopy(independent_checklist(spec, None))
    checklist.update(schema="m2-acceptance-v1", version=version)
    checklist["scope"]["connector"]["B"] = pins
    for check in checklist["checks"]:
        if check["name"] == "料号、图号、图版":
            check["expected"] = f"{part} / ENDE05 / A0"
            check["datum"] = f"料号表 {pins} 针行及标题栏"
        elif check["name"] == "端面视向与触点数":
            check["expected"]["pin_count"] = pins
            check["method"] = f"独立回读 STEP，从两端分别对照原图与 A-code 针位表，检数 {pins} 条触点。"
        elif check["name"] == "本体轴向总长":
            check["expected"].update(nominal_mm=length, reject_mm=[value for value in ("24.70", "26.70", "26.90") if value != length])
            check["datum"] = f"{length} 引出线所指的配合端外壳前端面与焊线端最远端子面"
            check["evidence"].insert(0, {"region": "variant-length", **regions["variant-length"], "image_sha256": images["variant-length"]})
        elif check["name"] == "部件与交付范围":
            check["expected"] = checklist["scope"]
            check["method"] = f"核对本体 {pins + 3} 个实体、独立螺母 D 一件及面板参考。"
    basis = {**spec, "schema": "m2-modeling-basis-v1", "version": version, "part_number": part,
             "drawing_no": "ENDE05", "revision": "A0", "pin_count": pins, "total_length_mm": length,
             "z_positive": "mating_to_terminal", "scope": checklist["scope"], "model_run": evidence["model_run"],
             "confirmations": answers, "checkpoint_sha256": evidence["checkpoint_sha256"]}
    if equivalent:
        basis["equivalent_read"] = equivalent
    return basis, checklist
