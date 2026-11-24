import json
import os
import re
import sys
from pathlib import Path

import FreeCAD as App
import Part

sys.path.insert(0, "/job")
from common import read_json, sha256, write_json
from export import write_html


def build(folder, mirrored):
    basis = read_json(folder / "basis.json")
    checks = read_json(folder / "checklist.json")
    plate = Part.makeBox(40, 30, 3, App.Vector(-20, -15, 0))
    holes = next(item["expected"] for item in checks["checks"] if item["name"] == "孔位有符号坐标")
    for hole in holes:
        center = hole["center_xy_mm"]
        plate = plate.cut(Part.makeCylinder(hole["radius_mm"], 3,
            App.Vector(-center[0] if mirrored else center[0], center[1], 0)))
    plate = plate.removeSplitter()
    plate.exportBrep(str(folder / "plate.brep"))
    manifest = {"schema": "m3-plate-fixture", "geometry_revision": basis["geometry_revision"], "version": basis["version"],
                "part_number": basis["part_number"], "basis_sha256": sha256(folder / "basis.json"),
                "components": [{"id": "plate"}], "brep_sha256": {"plate.brep": sha256(folder / "plate.brep")},
                "datum": basis["datum"], "unit": "mm"}
    write_json(folder / "geometry.json", manifest)
    identity = {key: manifest[key] for key in ("geometry_revision", "version", "part_number", "basis_sha256", "unit")}
    identity.update(geometry_sha256=sha256(folder / "geometry.json"), nut_installed_pose=None)
    plate.exportStep(str(folder / "model.step"))
    step = folder / "model.step"
    step.write_bytes(step.read_bytes().replace(b"HEADER;\n", b"HEADER;\n/* M1 " + json.dumps(identity).encode() + b" */\n", 1))
    component = {"id": "plate", "name": "非对称孔位测试板", "color": "#7294b7"}
    write_html(folder / "model.html", identity, [(component, plate)], {})
    html = (folder / "model.html").read_text(encoding="utf-8")
    html = html.replace("ENDE05 A0", "M3 测试夹具").replace("螺母 D 分件展示，安装位置未定。触点按位置标识。", basis["datum"])
    (folder / "model.html").write_text(html, encoding="utf-8")


def verify(folder):
    checks = read_json(folder / "checklist.json")
    step = folder / "model.step"
    shape = Part.read(str(step))
    identity = json.loads(re.search(rb"/\* M1 ([^\n]+) \*/", step.read_bytes()[:2048])[1])
    cylinders = {}
    for face in shape.Faces:
        surface = face.Surface
        if isinstance(surface, Part.Cylinder) and abs(surface.Axis.z) > 0.99999:
            cylinders[round(surface.Radius, 6)] = [round(surface.Center.x, 6), round(surface.Center.y, 6)]
    measured = {"孔位有符号坐标": [{"radius_mm": radius, "center_xy_mm": center} for radius, center in sorted(cylinders.items())],
                "外形与实体": [round(shape.BoundBox.XLength, 6), round(shape.BoundBox.YLength, 6),
                              round(shape.BoundBox.ZLength, 6), len(shape.Solids), shape.isValid()],
                "身份与基准": {"part_number": identity["part_number"], "unit": identity["unit"], "datum": "plate-center +X right +Y up +Z front"}}
    results = [{**item, "measured": measured[item["name"]], "passed": measured[item["name"]] == item["expected"],
                "evidence_regions": ["fixture-datum"]} for item in checks["checks"]]
    report = {"passed": all(item["passed"] for item in results), "checks": results,
              "step_sha256": sha256(step), "geometry_sha256": sha256(folder / "geometry.json"),
              "checklist_sha256": sha256(folder / "checklist.json"), "freecad": ".".join(App.Version()[:3]), "occt": Part.OCC_VERSION,
              "environment": {"uid": os.getuid(), "interfaces": sorted(os.listdir("/sys/class/net")),
                              "root_read_only": bool(os.statvfs("/").f_flag & os.ST_RDONLY)}}
    write_json(folder / "step-check.json", report)
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    mode, directory = sys.argv[1:3]
    folder = Path(directory)
    if mode == "build":
        build(folder, sys.argv[3] == "mirror")
    else:
        sys.exit(verify(folder))
