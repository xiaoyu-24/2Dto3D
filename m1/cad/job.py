import math
import os
import sys
from pathlib import Path

import FreeCAD as App
import Part

from common import read_json, sha256, write_json

Vector = App.Vector
JOB = "m12-a04-m16-a0"


def cylinder(radius, start, end):
    return Part.makeCylinder(radius, end - start, Vector(0, 0, start))


def hexagon(start, end):
    radius = 18 / math.sqrt(3)
    points = [Vector(radius * math.cos(index * math.pi / 3), radius * math.sin(index * math.pi / 3), start)
              for index in range(6)]
    return Part.Face(Part.makePolygon([*points, points[0]])).extrude(Vector(0, 0, end - start))


def threaded_form(pitch, radius, start, end, internal=False, flats=False):
    depth = pitch * (0.54127 if internal else 0.61343)
    core = radius - depth
    inner = core - 0.10 * pitch
    narrow = 0.09 * pitch
    wide = narrow + (radius - inner) / math.sqrt(3)
    def ridge(phase, first, last, count):
        profiles = []
        for index in range(count + 1):
            angle = first + (last - first) * index / count
            axial = phase + pitch * angle / math.tau
            points = [Vector(radial * math.cos(angle), radial * math.sin(angle), axial + rise)
                      for radial, rise in [(inner, -wide), (radius, -narrow), (radius, narrow), (inner, wide)]]
            profiles.append(Part.makePolygon([*points, points[0]]))
        return Part.makeLoft(profiles, True, False, False, 5)

    limit_width = 13.4 if flats else 20
    limit = Part.makeBox(limit_width, 20, end - start, Vector(-limit_width / 2, -10, start))
    base = cylinder(core, start, end)
    if not flats:
        turns = (end - start) / pitch + 1.68
        return base.fuse(ridge(start - 0.85 * pitch, 0, math.tau * turns,
                               math.ceil(24 * turns)).common(limit)).removeSplitter()

    phase = start - 0.37
    first_half = math.floor(2 * (start - phase - wide) / pitch)
    last_half = math.ceil(2 * (end - phase + wide) / pitch)
    crests = [ridge(phase, half * math.pi, (half + 1) * math.pi, 18).common(limit)
              for half in range(first_half, last_half)]
    return base.common(limit).fuse([solid for crest in crests for solid in crest.Solids]).removeSplitter()


def shapes(trials, length):
    key = trials["key"]["values"]
    ring = trials["oring"]["values"]
    centers = trials["pins"]["values"]["centers_xy_mm"]
    root = ring["groove_root_d_mm"] / 2
    groove_front = ring["center_depth_mm"] - ring["groove_width_mm"] / 2
    groove_back = ring["center_depth_mm"] + ring["groove_width_mm"] / 2

    front = threaded_form(1, 6, 0, 10)
    rear = threaded_form(1.5, 8, groove_back, 21, flats=True)
    shield = front.fuse([hexagon(10, groove_front), cylinder(root, groove_front, groove_back), rear])
    shield = shield.cut(cylinder(5.3, 0, 17.8).fuse(cylinder(6.05, 17.8, 21))).removeSplitter()

    inner = 8.35 / 2
    tip = inner - key["radial_reach_mm"] + key["end_radius_mm"]
    span = key["width_mm"]
    depth = key["axial_length_mm"]
    tab = Part.makeBox(span, inner + 0.1 - tip, depth, Vector(-span / 2, tip, 0)).fuse(
        Part.makeCylinder(key["end_radius_mm"], depth, Vector(0, tip, 0)))
    housing = cylinder(5.3, 0, 17.8).cut(cylinder(inner, 0, 10)).fuse(tab)
    holes = cylinder(0.5, 10, 12).fuse([cylinder(0.725, 12, 15.4), cylinder(0.68, 15.4, 17.8)])
    housing = housing.cut([holes.translated(Vector(*center, 0)) for center in centers]).removeSplitter()
    pin = Part.makeSphere(0.5, Vector(0, 0, 2.7), Vector(0, 0, 1), -90, 0, 360).fuse([
        cylinder(0.5, 2.7, 12), cylinder(0.725, 12, 15.4), cylinder(0.68, 15.4, length)])
    pin = pin.cut(cylinder(0.42, length - 2.25, length).fuse(
        Part.makeBox(0.6, 0.8, 0.95, Vector(-0.3, 0, length - 0.95)))).removeSplitter()

    section = ring["section_mm"] / 2
    seal = Part.makeTorus(ring["installed_id_mm"] / 2 + section, section,
                         Vector(0, 0, ring["center_depth_mm"]))
    nut = hexagon(0, 2.8).cut(threaded_form(1.5, 8, -0.1, 2.9, internal=True)).removeSplitter()
    if not nut.isValid():
        nut.fix(1e-5, 1e-6, 0.005)
    panel = cylinder(8.05, 0, 1).common(Part.makeBox(13.6, 20, 1, Vector(-6.8, -10, 0)))
    panel_face = next(face for face in panel.Faces if abs(face.BoundBox.ZMax) < 1e-7)
    return {"A": housing, "B": pin, "C": shield, "D": nut, "E": seal}, panel_face


def freeze(basis_path, selection_path, output, variant):
    basis = read_json(basis_path)
    selection = read_json(selection_path)
    job_sha = sha256(Path(__file__))
    basis_sha = sha256(basis_path)
    if (selection["job"] != JOB or selection["job_sha256"] != job_sha
            or selection["basis_sha256"] != basis_sha or basis["z_positive"] != "mating_to_terminal"):
        raise ValueError("CAD selection does not match the frozen input or job")
    length = float(variant) if variant in ("26.70", "26.90") else float(basis["total_length_mm"])
    solids, panel = shapes(basis["trials"], length)
    output.mkdir(parents=True, exist_ok=False)
    for code, solid in solids.items():
        if len(solid.Solids) != 1 or not solid.isValid():
            raise ValueError(f"Invalid CAD solid {code}")
        solid.exportBrep(str(output / f"{code}.brep"))
    panel.exportBrep(str(output / "panel.brep"))

    def component(part_id, code, name, material, color, translation):
        installed = None if code == "D" else {"translation_mm": translation, "rotation_xyz_deg": [0, 0, 0]}
        if variant == "swapped" and installed:
            installed = {"translation_mm": [-translation[0], translation[1], length],
                         "rotation_xyz_deg": [0, 180, 0]}
        return {"id": part_id, "code": code, "name": name, "material": material, "color": color,
                "brep": f"{code}.brep", "installed_pose": installed, "display_pose": installed or
                {"translation_mm": [26, 0, 0], "rotation_xyz_deg": [0, 0, 0]}}

    components = [component("A", "A", "外壳", "PA66+GF", "#273548", [0, 0, 0]),
                  component("C", "C", "外导体", "黄铜镀镍", "#9ba9b4", [0, 0, 0]),
                  component("E", "E", "O 形圈", "EPDM", "#30343a", [0, 0, 0]),
                  component("D", "D", "独立螺母", "黄铜镀镍", "#b6bcc2", [0, 0, 0])]
    positions = ("UL", "UR", "LL", "LR", "CENTER") if basis["pin_count"] == 5 else ("UL", "UR", "LL", "LR")
    for position, center in zip(positions, basis["trials"]["pins"]["values"]["centers_xy_mm"], strict=True):
        components.append(component(f"B-{position}", "B", f"触点 {position}", "黄铜镀金", "#d8a84a", [*center, 0]))

    manifest = {"schema": "m1-geometry-v1", "geometry_revision": "G1", "version": basis["version"],
                "part_number": basis["part_number"], "drawing_no": basis["drawing_no"],
                "drawing_revision": basis["revision"], "source_sha256": basis["source_sha256"],
                "basis_sha256": basis_sha, "job_sha256": job_sha, "selection_sha256": sha256(selection_path),
                "freecad": ".".join(App.Version()[:3]), "occt": Part.OCC_VERSION,
                "unit": "mm", "z_positive": basis["z_positive"], "components": components,
                "brep_sha256": {path.name: sha256(path) for path in output.glob("*.brep")},
                "panel_reference": {"brep": "panel.brep", "upper_deviation_mm": 0.1, "region": "panel"},
                "trials": basis["trials"], "variant": variant,
                "environment": {"uid": os.getuid(), "interfaces": sorted(os.listdir("/sys/class/net")),
                                "root_read_only": bool(os.statvfs("/").f_flag & os.ST_RDONLY)}}
    write_json(output / "geometry.json", manifest)
    print(f"Frozen {variant}: {sha256(output / 'geometry.json')}", flush=True)


if __name__ == "__main__":
    freeze(*(Path(argument) for argument in sys.argv[1:4]), sys.argv[4])
