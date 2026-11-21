import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import FreeCAD as App
import Import
import Part

from common import read_json, sha256, write_json

Vector = App.Vector
TRIAL_TOLERANCE = 0.015


def within(actual, expected, tolerance):
    return actual is not None and abs(actual - float(expected)) <= tolerance + 1e-8


def bounds(shape):
    return shape.optimalBoundingBox(False)


def axial_cylinders(shape):
    return [(face.Surface, bounds(face)) for face in shape.Faces
            if isinstance(face.Surface, Part.Cylinder) and abs(face.Surface.Axis.z) > 0.9999]


def plane_offsets(shape, axis):
    return [face for face in shape.Faces if isinstance(face.Surface, Part.Plane)
            and abs(face.Surface.Axis.dot(axis)) > 0.9999 and bounds(face).ZLength > 0.2]


def hex_flats(shape, center):
    return [face for face in shape.Faces if isinstance(face.Surface, Part.Plane)
            and abs(face.Surface.Axis.z) < 1e-6
            and abs((face.CenterOfMass - center).dot(face.Surface.Axis)) > 8.5]


def width_af(faces, center):
    return 2 * min(abs((face.CenterOfMass - center).dot(face.Surface.Axis)) for face in faces) if len(faces) == 6 else None


def measure_thread(shape, center, low, high, nominal_radius, internal=False):
    probe_radius = nominal_radius - 0.25
    base = center + Vector(0, probe_radius, 0)
    axis = Part.makeLine(base + Vector(0, 0, low + 0.005), base + Vector(0, 0, high - 0.005))
    crossings = sorted({round(vertex.Point.z, 6) for vertex in shape.section(axis).Vertexes})
    pitches = [second - first for first, second in zip(crossings, crossings[2:])]
    diameters = []
    for first, second in zip(crossings, crossings[1:]):
        height = (first + second) / 2
        if shape.isInside(base + Vector(0, 0, height), 1e-6, False) == internal:
            continue
        ray = Part.makeLine(center + Vector(0, nominal_radius - 0.38, height),
                            center + Vector(0, nominal_radius + 0.30, height))
        radii = [vertex.Point.y - center.y for vertex in shape.section(ray).Vertexes]
        if radii:
            diameters.append(2 * max(radii))
    return {"major_diameter_mm": round(max(diameters), 6) if diameters else None,
            "pitches_mm": [round(pitch, 6) for pitch in pitches], "axial_intersections_mm": crossings}


def thread_pass(measured, thread):
    diameter, pitch = (float(item) for item in thread[1:].split("×"))
    return (within(measured["major_diameter_mm"], diameter, 0.05) and len(measured["pitches_mm"]) >= 2
            and all(within(value, pitch, 0.02) for value in measured["pitches_mm"]))


def verify(step, checklist_path, geometry_path):
    checklist = read_json(checklist_path)
    manifest = read_json(geometry_path)
    geometry_sha = sha256(geometry_path)
    step_bytes = step.read_bytes()
    identity = json.loads(re.search(rb"/\* M1 ([^\n]+) \*/", step_bytes[:2048])[1])
    shape = Part.read(str(step))
    doc = App.newDocument("IndependentM1Readback")
    Import.insert(str(step), doc.Name)
    doc.recompute()
    objects = {item.Label: item for item in doc.Objects if item.TypeId == "Part::Feature"}
    parts = {label: item.Shape for label, item in objects.items()}
    connector = next(item for item in doc.Objects if item.TypeId == "App::Part" and item.Label.endswith(" / Connector"))
    pins = [part for label, part in parts.items() if label.startswith("B-")]
    shell, housing, nut, ring = (parts[code] for code in ("C", "A", "D", "E"))
    body = bounds(connector.Shape)
    shell_box = bounds(shell)
    front = shell_box.ZMin
    shell_back = shell_box.ZMax
    pin_bounds = [bounds(pin) for pin in pins]
    key, key_box = next((surface, box) for surface, box in axial_cylinders(housing)
                        if abs(surface.Center.x) < 1e-6 and surface.Center.y > 2.5)
    flats = plane_offsets(shell, Vector(1, 0, 0))
    flat_x = sorted({round(face.CenterOfMass.x, 6) for face in flats})
    flat_first = min(bounds(face).ZMin for face in flats)
    flat_last = max(bounds(face).ZMax for face in flats)
    shoulder_faces = hex_flats(shell, Vector())
    hex_front = min(bounds(face).ZMin for face in shoulder_faces)
    groove, groove_box = next((surface, box) for surface, box in axial_cylinders(shell)
                              if 6.9 < surface.Radius < 7.15 and abs(surface.Center.x) < 1e-6
                              and within(box.XLength, 2 * surface.Radius, 1e-5))
    pin_cylinders = [min(axial_cylinders(pin), key=lambda item: (item[0].Radius, item[1].ZMin)) for pin in pins]
    pin_stems = [min(axial_cylinders(pin), key=lambda item: item[1].ZMin)[0] for pin in pins]
    centers = [[round(surface.Center.x, 6), round(surface.Center.y, 6)] for surface in pin_stems]
    terminal_count = sum(abs(box.ZMax - body.ZMax) < 0.03 for _, box in pin_cylinders)
    nut_box = bounds(nut)
    nut_center = Vector((nut_box.XMin + nut_box.XMax) / 2, (nut_box.YMin + nut_box.YMax) / 2, 0)
    matches_mating = within(key_box.ZMin, front, 0.03) and key.Center.y > 0
    matches_terminal = len(flat_x) == 2 and within(flat_last, shell_back, 0.03) and terminal_count == len(pins)
    scope = checklist["scope"]
    result = []

    def check(name, measure):
        spec = next(item for item in checklist["checks"] if item["name"] == name)
        measured, passed = measure(spec["expected"])
        result.append({"name": name, "kind": spec["kind"], "passed": bool(passed),
                       "expected": spec["expected"], "measured": measured,
                       "evidence_regions": [item["region"] for item in spec["evidence"]]})

    metadata = " / ".join(identity[field] for field in ("part_number", "drawing_no", "drawing_revision"))
    check("料号、图号、图版", lambda expected: ({"identifier": metadata, "unit": identity["unit"]},
          metadata == expected and identity["version"] == checklist["version"] and identity["unit"] == "mm"
          and b"SI_UNIT(.MILLI.,.METRE.)" in step_bytes and identity["source_sha256"] == checklist["source_sha256"]
          and identity["basis_sha256"] == checklist["basis_sha256"] and identity["geometry_sha256"] == geometry_sha
          and identity["geometry_revision"] == manifest["geometry_revision"] and identity["z_positive"] == "mating_to_terminal"
          and connector.Label == f"{identity['part_number']} / {identity['geometry_revision']} / Connector"))
    check("端面视向与触点数", lambda expected: (
        {"+Z_mating": matches_mating, "-Z_terminal": matches_terminal, "pin_count": len(pins),
         "terminal_count": terminal_count, "key_z_mm": [key_box.ZMin, key_box.ZMax],
         "mount_flats_z_mm": [flat_first, flat_last], "key_y_mm": key.Center.y},
        matches_mating and matches_terminal and len(pins) == expected["pin_count"]
        and expected["end_views"]["mating"]["look_along"] == "+Z"
        and expected["end_views"]["terminal"]["look_along"] == "-Z"))

    total = body.ZLength
    check("本体轴向总长", lambda expected: ({"length_mm": round(total, 6), "body_z_mm": [body.ZMin, body.ZMax],
          "shell_to_farthest_terminal_mm": round(max(box.ZMax for box in pin_bounds) - front, 6)},
          within(total, expected["nominal_mm"], float(expected["general_tolerance_mm"][1:]))))
    check("轴向外壳尺寸", lambda expected: (
        {"shell_length_mm": shell_box.ZLength, "front_to_hex_shoulder_mm": hex_front - front},
        within(shell_box.ZLength, expected["shell_length_mm"], float(expected["tolerance_mm"][1:]))
        and within(hex_front - front, expected["front_to_hex_shoulder_mm"], float(expected["tolerance_mm"][1:]))))
    setbacks = [box.ZMin - front for box in pin_bounds]
    recess = groove_box.ZMin - hex_front
    check("剖视局部轴向尺寸", lambda expected: ({"pin_setback_mm": setbacks, "recess_offset_mm": recess},
          all(within(value, expected["pin_setback_mm"], float(expected["tolerance_mm"][1:])) for value in setbacks)
          and within(recess, expected["recess_offset_mm"], float(expected["tolerance_mm"][1:]))))

    diameters = sorted((2 * surface.Radius for surface, box in axial_cylinders(housing)
                        if abs(surface.Center.x) + abs(surface.Center.y) < 1e-6 and box.ZLength > 8), reverse=True)
    mount_width = flat_x[-1] - flat_x[0]
    check("配合端圆径与安装端扁宽", lambda expected: ({"diameters_mm": diameters, "mount_width_mm": mount_width},
          len(diameters) == 2 and all(within(value, target, float(expected["tolerance_mm"][1:]))
              for value, target in zip(diameters, expected["diameters_mm"]))
          and within(mount_width, expected["mount_width_mm"], float(expected["tolerance_mm"][1:]))))
    pin_diameters = [2 * min(surface.Radius for surface, _ in axial_cylinders(pin) if surface.Radius > 0.45)
                     for pin in pins]
    check("配合端图示针段", lambda expected: (pin_diameters,
          all(within(value, expected["diameter_mm"], float(expected["tolerance_mm"][1:])) for value in pin_diameters)))

    mating_thread = measure_thread(shell, Vector(), front, hex_front, 6)
    mount_thread = measure_thread(shell, Vector(), flat_first, flat_last, 8)
    shell_af = width_af(shoulder_faces, Vector())
    check("本体螺纹与六角", lambda expected: ({"mating_thread": mating_thread, "mount_thread": mount_thread, "width_af_mm": shell_af},
          thread_pass(mating_thread, expected["mating_thread"]) and thread_pass(mount_thread, expected["mount_thread"])
          and within(shell_af, expected["width_af_mm"], 0.35)))
    nut_thread = measure_thread(nut, nut_center, nut_box.ZMin, nut_box.ZMax, 8, internal=True)
    nut_af = width_af(hex_flats(nut, nut_center), nut_center)
    group_labels = {item.Label for item in connector.Group if item.TypeId == "Part::Feature"}
    separate = "D" not in group_labels and nut_box.XMin > body.XMax + 1
    check("独立螺母 D", lambda expected: ({"count": len(nut.Solids), "axial_mm": nut_box.ZLength,
          "width_af_mm": nut_af, "thread": nut_thread, "installed_pose": identity["nut_installed_pose"],
          "separate": separate, "display_center_xy_mm": [nut_center.x, nut_center.y]},
          separate and len(nut.Solids) == expected["count"] and identity["nut_installed_pose"] is None
          and within(nut_box.ZLength, expected["axial_mm"], 0.25) and within(nut_af, expected["width_af_mm"], 0.35)
          and thread_pass(nut_thread, expected["thread"])))

    panel_name = manifest["panel_reference"]["brep"]
    panel_path = geometry_path.parent / panel_name
    panel = Part.read(str(panel_path))
    panel_width, panel_diameter = bounds(panel).XLength, bounds(panel).YLength
    panel_is_reference = (not panel.Solids and "panel" not in parts and
                          sha256(panel_path) == manifest["brep_sha256"][panel_name])
    check("M16 面板开孔参考", lambda expected: ({"diameter_mm": panel_diameter, "width_mm": panel_width, "reference_only": panel_is_reference},
          panel_is_reference and within(panel_diameter, expected["diameter_mm"], 0.25)
          and float(expected["width_mm"]) - 1e-6 <= panel_width <= float(expected["width_mm"]) + float(expected["width_upper_deviation_mm"]) + 1e-6))
    counts = dict(Counter(label.split("-")[0] for label in group_labels))
    validity = {label: part.isValid() and len(part.Solids) == 1 and part.Volume > 0 for label, part in parts.items()}
    check("部件与交付范围", lambda expected: ({"connector": counts, "connector_solids": len(connector.Shape.Solids),
          "nut_solids": len(nut.Solids), "all_solids": len(shape.Solids), "validity": validity},
          scope == expected and counts == expected["connector"] and len(connector.Shape.Solids) == sum(expected["connector"].values())
          and len(shape.Solids) == sum(expected["connector"].values()) + expected["nut"]["D"]
          and shape.isValid() and all(validity.values()) and separate and panel_is_reference))

    key_walls = [face.CenterOfMass.x for face in plane_offsets(housing, Vector(1, 0, 0))
                 if face.CenterOfMass.y > key.Center.y - 0.05 and bounds(face).ZLength < 2.5]
    key_values = {"width_mm": max(key_walls) - min(key_walls), "axial_length_mm": key_box.ZLength,
                  "end_radius_mm": key.Radius, "radial_reach_mm": min(diameters) / 2 - (key.Center.y - key.Radius)}
    check("A 键轮廓", lambda expected: (key_values,
          all(within(key_values[name], value, TRIAL_TOLERANCE) for name, value in expected.items())))
    pin_label = "五针中心" if scope["connector"]["B"] == 5 else "四针中心"
    check(pin_label, lambda expected: (centers, sorted(centers) == sorted(expected["centers_xy_mm"])))
    torus = next(face.Surface for face in ring.Faces if isinstance(face.Surface, Part.Toroid))
    ring_values = {"installed_id_mm": 2 * (torus.MajorRadius - torus.MinorRadius), "section_mm": 2 * torus.MinorRadius,
                   "center_depth_mm": torus.Center.z - front, "groove_root_d_mm": 2 * groove.Radius,
                   "groove_width_mm": groove_box.ZLength}
    check("O 形圈和密封台阶", lambda expected: (ring_values,
          all(within(ring_values[name], value, TRIAL_TOLERANCE) for name, value in expected.items())))
    expected_solids = sum(scope["connector"].values()) + scope["nut"]["D"]
    check("环氧灌封表示", lambda expected: ({"epoxy_separate_solid": len(shape.Solids) != expected_solids},
          len(shape.Solids) == expected_solids and not expected["epoxy_separate_solid"]))
    App.closeDocument(doc.Name)
    passed = all(item["passed"] for item in result)
    return {"passed": passed, "step_sha256": sha256(step), "geometry_sha256": geometry_sha,
            "checklist_sha256": sha256(checklist_path), "checks": result, "freecad": ".".join(App.Version()[:3]),
            "occt": Part.OCC_VERSION, "verifier_sha256": sha256(Path(__file__)), "environment": {
                "uid": os.getuid(), "interfaces": sorted(os.listdir("/sys/class/net")),
                "root_read_only": bool(os.statvfs("/").f_flag & os.ST_RDONLY)}}


if __name__ == "__main__":
    step, checklist, geometry, output = map(Path, sys.argv[1:])
    measurement = verify(step, checklist, geometry)
    write_json(output, measurement)
    print(step.name, "PASS" if measurement["passed"] else "FAIL",
          [item["name"] for item in measurement["checks"] if not item["passed"]], flush=True)
    sys.exit(0 if measurement["passed"] else 1)
