import sys

import FreeCAD
import MeshPart
import Part

plate = Part.makeBox(32, 20, 5)
hole = Part.makeCylinder(3, 5, FreeCAD.Vector(7, 9, 0))
solid = plate.cut(hole)
mesh = MeshPart.meshFromShape(Shape=solid, LinearDeflection=0.1, AngularDeflection=0.3)
if mesh.CountFacets == 0:
    raise ValueError("MeshPart produced an empty mesh")
solid.exportStep(sys.argv[1])
