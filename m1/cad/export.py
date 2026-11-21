import base64
import json
import sys
from array import array
from pathlib import Path

import FreeCAD as App
import Import
import MeshPart
import Part

from common import read_json, sha256


def frozen_shapes(folder, manifest):
    shapes = {}
    for name, expected in manifest["brep_sha256"].items():
        path = folder / name
        if sha256(path) != expected:
            raise ValueError(f"Frozen BREP mismatch: {name}")
        shapes[name] = Part.read(str(path))
    for component in manifest["components"]:
        solid = shapes[component["brep"]].copy()
        if not solid.isValid() or len(solid.Solids) != 1:
            raise ValueError(f"Invalid frozen component: {component['id']}")
        pose = component["display_pose"]
        angles = pose["rotation_xyz_deg"]
        solid.Placement = App.Placement(App.Vector(*pose["translation_mm"]), App.Rotation(*reversed(angles)))
        yield component, solid


def write_step(step, identity, components):
    doc = App.newDocument("M1FirstDelivery")
    connector = doc.addObject("App::Part", "Connector")
    connector.Label = f"{identity['part_number']} / {identity['geometry_revision']} / Connector"
    for item, solid in components:
        feature = doc.addObject("Part::Feature", "Component_" + item["id"].replace("-", "_"))
        feature.Label = item["id"]
        feature.Shape = solid
        if item["code"] != "D":
            connector.addObject(feature)
        else:
            nut = feature
    doc.recompute()
    Import.export([connector, nut], str(step))
    header = b"/* M1 " + json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() + b" */\n"
    step.write_bytes(step.read_bytes().replace(b"HEADER;\n", b"HEADER;\n" + header, 1))
    if header not in step.read_bytes()[:2048]:
        raise ValueError("STEP identity header is missing")
    App.closeDocument(doc.Name)

    print(f"STEP {sha256(step)}, geometry {identity['geometry_sha256']}", flush=True)


def write_html(html, identity, components, trials):
    parts = []
    for item, solid in components:
        mesh = MeshPart.meshFromShape(Shape=solid, LinearDeflection=0.035, AngularDeflection=0.22, Relative=False)
        vertices, facets = mesh.Topology
        if not facets:
            raise ValueError(f"Empty viewer mesh: {item['id']}")
        packed = array("f", (axis for facet in facets for index in facet for axis in vertices[index]))
        if sys.byteorder != "little":
            packed.byteswap()
        parts.append({"id": item["id"], "name": item["name"], "color": item["color"],
                      "triangles": len(facets), "xyz": base64.b64encode(packed).decode()})
    scene = {"identity": identity, "parts": parts, "trials": trials}
    payload = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = Path(__file__).with_name("viewer.html").read_text(encoding="utf-8")
    html.write_text(template.replace("/*__MODEL__*/", payload), encoding="utf-8")
    print(f"HTML: {len(parts)} components, {sum(part['triangles'] for part in parts)} triangles", flush=True)


def export(folder, step, html=None):
    path = folder / "geometry.json"
    manifest = read_json(path)
    components = list(frozen_shapes(folder, manifest))
    identity = {field: manifest[field] for field in ("version", "geometry_revision", "part_number", "drawing_no",
                "drawing_revision", "source_sha256", "basis_sha256", "z_positive", "unit")}
    identity["geometry_sha256"] = sha256(path)
    identity["nut_installed_pose"] = next(item["installed_pose"] for item, _ in components if item["code"] == "D")
    if step != Path("-"):
        write_step(step, identity, components)
    if html:
        write_html(html, identity, components, manifest["trials"])


if __name__ == "__main__":
    export(*(Path(argument) for argument in sys.argv[1:]))
