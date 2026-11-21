import hashlib
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

PART = "M12A04-03-301-M16"
SOURCE = "be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c"
GENERATOR = "m1-vision-v1"

REGIONS = {
    "variant": ("料号表", (42, 657, 708, 908)),
    "key": ("A-code 针位表", (36, 85, 677, 224)),
    "mating": ("带键槽端面", (615, 249, 875, 509)),
    "section": ("轴向剖视", (915, 150, 1327, 525)),
    "terminal": ("焊线端面", (1422, 249, 1664, 508)),
    "nut": ("螺母 D 详图", (929, 614, 1290, 841)),
    "panel": ("M16 面板开孔参考", (1481, 600, 1644, 841)),
    "parts": ("部件表", (1370, 930, 1852, 1121)),
    "title": ("图号、图版、单位和公差", (527, 1116, 1852, 1298)),
}

TRIALS = {
    "key": {
        "label": "A 键轮廓",
        "values": {"width_mm": 1.20, "radial_reach_mm": 1.20, "end_radius_mm": 0.60, "axial_length_mm": 2.20},
        "summary": "键在配合端正上方，试建向轴心伸入的 U 形轮廓，宽 1.20、径向伸入 1.20、圆端 R0.60、轴向长 2.20 mm。",
        "basis": "用配合端 SW18 标注校准轮廓约 19 px，轴向长度采用试建设定。",
        "regions": ["mating", "section"],
    },
    "pins": {
        "label": "四针中心",
        "values": {"centers_xy_mm": [[-1.75, 1.75], [1.75, 1.75], [-1.75, -1.75], [1.75, -1.75]]},
        "summary": "从配合端看，轴心为原点，向右为 +X、向上朝键为 +Y。针心为 X、Y 各 ±1.75 mm，按位置标识。",
        "basis": "用 SW18 校准端面图，四针相邻针心约差 55 px；图纸未标注电气针号。",
        "regions": ["mating", "key"],
    },
    "oring": {
        "label": "O 形圈和密封台阶",
        "values": {"installed_id_mm": 14.10, "section_mm": 2.00, "center_depth_mm": 14.00,
                   "groove_root_d_mm": 14.10, "groove_width_mm": 2.00},
        "summary": "装配示意用 Ø14.10 内径、Ø2.00 截面，圆心距配合端前端面 14.00 mm；台阶根径 Ø14.10、轴向宽 2.00 mm。",
        "basis": "按剖视 24.70 标注对圆形截面和位置估读，台阶尺寸按试建环包络设定。自由状态规格未标注。",
        "regions": ["section", "parts"],
    },
    "assembly": {
        "label": "环氧灌封表示",
        "values": {"epoxy_separate_solid": False},
        "summary": "在说明中保留环氧灌封标注，试建组件中省略独立灌封实体。",
        "basis": "剖视标示灌封位置，部件表未列独立零件，图纸未给出灌封体的完整尺寸。",
        "regions": ["parts", "section"],
    },
}
SCOPE = {"connector": {"A": 1, "B": 4, "C": 1, "E": 1}, "nut": {"D": 1, "installed_pose": None},
         "panel": "M16 安装开孔参考轮廓"}

# These observations were checked against the page and its crops for SOURCE.
REVIEWED = (
    ("料号与图版", "M12A04-03-301-M16，A-CODE 四针线端公头，图号 ENDE05，图版 A0，单位 mm。", "drawing", ("variant", "title")),
    ("带键端与焊线端", "配合端沿主轴向内看，键在图中上方，四个针端在轴心周围。焊线端从相反方向向内看，标有安装螺纹及两平行边。", "derived", ("key", "mating", "terminal", "section")),
    ("四针总长", "24.70 mm。通用两位小数线性公差 ±0.25 mm。剖视上的 26.70 与 26.90 分别标给五针与八针。", "drawing", ("variant", "section", "title")),
    ("总长量测基准", "沿主轴，从配合端外壳前端面量至焊线端端子最远端；取 24.70 的两条引出线。", "derived", ("section",)),
    ("剖视主要轴向尺寸", "21.00 为前端面到安装螺纹一侧外壳后端；10.00 为前端面到六角前肩；2.20 为配合端针尖相对前端面退距；3.00 从六角前肩量至图示 O 形圈前缘所在台阶。单位 mm，公差按标题栏。", "derived", ("section", "title")),
    ("配合端关键尺寸", "SW18，配合螺纹 M12×1，剖视 Ø10.60，带键端面内轮廓 Ø8.35。", "drawing", ("mating", "section")),
    ("安装端关键尺寸", "13.40 mm 是后端两平行面的间距。剖视和后视共用标注 PG9/M16×1.5；料号后缀 M16 选 M16×1.5。", "derived", ("variant", "terminal", "section")),
    ("针径参数", "四针 X=1.00。剖视 Ø:X 给出图示配合端针段直径 Ø1.00 mm；图纸未标针心尺寸与电气编号。", "derived", ("key", "section", "mating")),
    ("通用公差", "线性一、二、三位小数分别为 ±0.35、±0.25、±0.10 mm。角度参考、整度和度分分别为 ±6°、±3°、±2°。", "drawing", ("title",)),
    ("部件范围", "A 外壳 PA66+GF 一件，B 黄铜镀金触点在本版本为四件，C 黄铜镀镍外导体一件，E 黑色 EPDM O 形圈一件。剖视另标环氧灌封。", "derived", ("parts", "variant", "key", "section")),
    ("独立螺母 D", "黄铜镀镍一件，六角对边 18 mm，轴向厚度 2.80 mm。本料号选内螺纹 M16×1.5。", "derived", ("nut", "parts", "variant")),
    ("面板开孔参考", "M16×1.5 的安装参考轮廓，外圆 Ø16.10 mm，两平行边宽 13.60 mm，图注上偏差 +0.10 mm。", "drawing", ("panel",)),
)


def trial_parameters():
    return {key: {**item, "kind": "trial"} for key, item in TRIALS.items()}


def annotate_reading(evidence):
    evidence["reviewed"] = [{"label": label, "value": value, "kind": kind, "regions": list(regions)}
                            for label, value, kind, regions in REVIEWED]
    evidence["trials"] = trial_parameters()
    return evidence


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checked_blob(body, expected_sha):
    if hashlib.sha256(body).digest() != expected_sha:
        raise ValueError("原图或检查点的字节指纹不匹配")
    return body


def render_evidence(source, checkpoint):
    page = checkpoint["pages"][0]
    if (checkpoint["source_sha256"] != SOURCE or checkpoint["page_count"] != 1
            or page["rotation"] != 0 or page["user_unit"] != 1
            or page["crop_box"] != [0, 0, 841.92, 595.32]):
        raise ValueError("原页与本次证据范围不一致")

    with TemporaryDirectory() as directory:
        pdf = Path(directory, "source.pdf")
        pdf.write_bytes(source)

        def render(region=None):
            args = ["pdftoppm", "-f", "1", "-l", "1", "-cropbox", "-singlefile", "-r", "240", "-png"]
            if region:
                args += [str(item) for pair in zip(("-x", "-y", "-W", "-H"), region) for item in pair]
            return subprocess.run([*args, str(pdf)], check=True, capture_output=True, timeout=20).stdout

        full = render()
        width, height = struct.unpack(">II", full[16:24])
        scale = 240 / 72
        frame = {**{key: page[key] for key in ("number", "rotation", "user_unit", "media_box", "crop_box")},
                 "width": width, "height": height, "dpi": 240,
                 "pdf_to_pixels": [scale, 0, 0, -scale, 0, page["crop_box"][3] * scale]}
        images = {"page": full}
        regions = {}
        for key, (view, (left, top, right, bottom)) in REGIONS.items():
            x0, x1 = (round(value * width / 1888) for value in (left, right))
            y0, y1 = (round(value * height / 1334) for value in (top, bottom))
            rect = [x0, y0, x1 - x0, y1 - y0]
            images[key] = render(rect)
            regions[key] = {"source_sha256": SOURCE, "page": 1, "view": view, "pixel_rect": rect,
                            "pdf_rect": [x0 / scale, page["crop_box"][3] - y1 / scale,
                                         x1 / scale, page["crop_box"][3] - y0 / scale]}
    return {"source_sha256": SOURCE, "part_number": PART, "page": frame, "regions": regions}, images
