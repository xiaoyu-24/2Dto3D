from decimal import Decimal, InvalidOperation

from app.m1_sample import PART, SCOPE

DRAWING = "ENDE05"
REVISION = "A0"
TOTAL = "24.70"
VERSION = f"{PART}/{REVISION}/M1.3"
END_VIEWS = {
    "mating": {"region": "mating", "look_along": "+Z"},
    "terminal": {"region": "terminal", "look_along": "-Z"},
}
END_LABELS = {"mating": ("配合端", "带键端"), "terminal": ("焊线端",)}


def check_variant(part_number, pins, total_length):
    try:
        length = Decimal(str(total_length))
    except InvalidOperation:
        length = None
    if part_number != PART or pins != 4 or length != Decimal(TOTAL):
        raise ValueError("型号错配：四针 M16 的料号、针数或轴向总长与原图不符")


def check_end_views(findings):
    end_regions = {view["region"] for view in END_VIEWS.values()}
    claims = {}
    for finding in findings:
        label = finding["label"]
        if "视向" not in label:
            continue
        roles = [role for role, names in END_LABELS.items() if any(name in label for name in names)]
        regions = end_regions.intersection(finding["regions"])
        if not roles and not regions:
            continue
        if len(roles) != 1:
            raise ValueError("端面视向不明确，须分别识别配合端和焊线端")
        role = roles[0]
        expected = END_VIEWS[role]
        contradicts_label = any(name in finding["value"] for other, names in END_LABELS.items()
                                if other != role for name in names)
        if regions != {expected["region"]} or role in claims or contradicts_label:
            raise ValueError(f"端面视向错配：{label} 与原图端面页区或读图描述不一致")
        claims[role] = {"region": regions.pop(), "look_along": expected["look_along"], "reported_label": label}
    if claims.keys() != END_VIEWS.keys():
        raise ValueError("端面视向缺失，须分别提供配合端和焊线端的页区证据")
    return claims


def independent_checklist(evidence, basis_sha):
    checks = [
        ("料号、图号、图版", f"{PART} / {DRAWING} / {REVISION}", "drawing", ("variant", "title"),
         "料号表四针行及标题栏", "比对交付元数据和原图字段。"),
        ("端面视向与触点数", {"pin_count": 4, "end_views": END_VIEWS}, "derived", ("key", "mating", "terminal", "section"),
         "以剖视中从带键配合端指向焊线端为 +Z",
         "独立回读 STEP，视线沿 +Z 从带键配合端向内，沿 -Z 从带安装扁面的焊线端向内，分别对照原图并检数四条触点。"),
        ("本体轴向总长", {"nominal_mm": TOTAL, "general_tolerance_mm": "±0.25", "reject_mm": ["26.70", "26.90"]},
         "drawing", ("section", "title"),
         "24.70 引出线所指的配合端外壳前端面与焊线端最远端子面",
         "独立导入 STEP，在本体范围沿主轴投影取两端距离。另以两种错误名义总长分别验证型号门禁。"),
        ("轴向外壳尺寸", {"shell_length_mm": "21.00", "front_to_hex_shoulder_mm": "10.00", "tolerance_mm": "±0.25"},
         "derived", ("section", "title"), "配合端外壳前端面、安装端外壳后端面、六角前肩",
         "剖切到主轴并沿轴分别量前端到外壳后端、前端到六角前肩的距离。"),
        ("剖视局部轴向尺寸", {"pin_setback_mm": "2.20", "recess_offset_mm": "3.00", "tolerance_mm": "±0.25"},
         "derived", ("section", "title"), "配合端前端面和针尖、六角前肩和 O 形圈前方台阶",
         "沿主轴测两组局部面间距并对照各自尺寸界线。"),
        ("配合端圆径与安装端扁宽", {"diameters_mm": ["10.60", "8.35"], "mount_width_mm": "13.40",
                                     "tolerance_mm": "±0.25"},
         "drawing", ("mating", "section", "terminal", "title"),
         "配合端同轴圆轮廓及焊线端相对平行面", "在垂直主轴的截面量两段圆径和两面法向间距。"),
        ("配合端图示针段", {"diameter_mm": "1.00", "tolerance_mm": "±0.25"}, "derived",
         ("key", "section", "title"), "4pin 栏 X 值和剖视 Ø:X 标记",
         "在被标注的直圆针段截面测量直径，比较四针的同类段。"),
        ("本体螺纹与六角", {"mating_thread": "M12×1", "mount_thread": "M16×1.5", "width_af_mm": "18"},
         "derived", ("variant", "mating", "section", "terminal"), "同一主轴和六角的相对外平面",
         "分别检配合端与安装端的公称规格及螺距，量六角对边。"),
        ("独立螺母 D", {"count": 1, "width_af_mm": "18", "axial_mm": "2.80", "thread": "M16×1.5"},
         "derived", ("nut", "parts", "variant"), "螺母自身轴线、两端平面和六角相对平面",
         "在独立部件逐件核数，量轴向厚度、对边，检内螺纹规格。装配位置留空。"),
        ("M16 面板开孔参考", {"diameter_mm": "16.10", "width_mm": "13.60", "width_upper_deviation_mm": "+0.10"},
         "drawing", ("panel",), "开孔中心、外圆与两平行边",
         "从参考截面量外圆直径与平行边距，检查交付实体清单是否排除面板。"),
    ]
    checks.append(("部件与交付范围", SCOPE, "derived", ("parts", "section", "variant", "panel"),
                   "部件代号 A 至 E 和 M16 开孔参考",
                   "分别核对本体 7 个实体、螺母 D 一件及面板参考。独立螺母没有安装变换。"))
    trial_methods = {
        "key": ("配合端轴心及正上方", "量键的宽、径向伸入、圆端和轴向长度。"),
        "pins": ("配合端轴心、图中向右与朝键向上", "在垂直主轴截面量四针中心有符号坐标。"),
        "oring": ("配合端壳体前端面与主轴", "用轴向剖切检查装配状态的内径、线径、台阶和位置。"),
        "assembly": ("本体和独立件的实体清单", "检查灌封仅记录在说明中。"),
    }
    for key, trial in evidence["trials"].items():
        datum, method = trial_methods[key]
        checks.append((trial["label"], trial["values"], "trial", trial["regions"], datum, method))
    return {"schema": "m1-acceptance-v1", "version": VERSION, "basis_sha256": basis_sha,
            "source_sha256": evidence["source_sha256"], "scope": SCOPE,
            "trial_usage": "试建项按所选参数检查几何一致性，原图符合性核验采用标注与有证据的推导。",
            "checks": [{"name": name, "expected": expected, "kind": kind, "datum": datum, "method": method,
                        "evidence": [{"region": key, **evidence["regions"][key],
                                      "image_sha256": evidence["images"][key]} for key in regions]}
                       for name, expected, kind, regions, datum, method in checks]}
