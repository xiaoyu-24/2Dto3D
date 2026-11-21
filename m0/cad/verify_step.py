import hashlib
import json
import math
import platform
import sys
from pathlib import Path

import FreeCAD
import Part

path = Path(sys.argv[1])
shape = Part.read(str(path))
if not shape.isValid() or len(shape.Solids) != 1:
    raise ValueError("Expected one valid solid")

bounds = shape.BoundBox
dimensions = (bounds.XLength, bounds.YLength, bounds.ZLength)
holes = [face for face in shape.Faces if isinstance(face.Surface, Part.Cylinder)]
if len(holes) != 1:
    raise ValueError("Expected one cylindrical hole")
wall = holes[0]
cylinder = wall.Surface
measurements = (*dimensions, cylinder.Center.x, cylinder.Center.y, cylinder.Radius,
                abs(cylinder.Axis.z), wall.BoundBox.ZMin, wall.BoundBox.ZMax, shape.Volume)
expected = (32, 20, 5, 7, 9, 3, 1, 0, 5, (32 * 20 - math.pi * 3**2) * 5)
if not all(math.isclose(value, target, rel_tol=0, abs_tol=1e-6)
           for value, target in zip(measurements, expected, strict=True)):
    raise ValueError(f"STEP measurement mismatch: {measurements}")

print(json.dumps({"freecad": ".".join(FreeCAD.Version()[:3]), "occt": Part.OCC_VERSION,
                  "python": platform.python_version(), "solids": len(shape.Solids),
                  "dimensions_mm": dimensions, "hole_center_mm": [cylinder.Center.x, cylinder.Center.y],
                  "hole_radius_mm": cylinder.Radius, "volume_mm3": shape.Volume,
                  "step_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
