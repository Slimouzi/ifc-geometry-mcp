"""Smoke test géométrique : construit une mini-maquette IFC et exécute les 5
analyseurs pour valider le pipeline (create_shape → footprint → STRtree)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ifcopenshell
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.spatial
import ifcopenshell.api.aggregate
import ifcopenshell.api.geometry
import ifcopenshell.api.group

f = ifcopenshell.api.project.create_file(version="IFC4")
proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="I3F Test")
_lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")  # mètre
ifcopenshell.api.unit.assign_unit(f, units=[_lu])
model_ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
body = ifcopenshell.api.context.add_context(
    f,
    context_type="Model",
    context_identifier="Body",
    target_view="MODEL_VIEW",
    parent=model_ctx,
)

site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="Site")
bld = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="Bat")
storey = ifcopenshell.api.root.create_entity(
    f, ifc_class="IfcBuildingStorey", name="R+1"
)
ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=proj)
ifcopenshell.api.aggregate.assign_object(f, products=[bld], relating_object=site)
ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=bld)


def _p3(x, y, z=0.0):
    return f.create_entity("IfcCartesianPoint", (float(x), float(y), float(z)))


def _identity_placement():
    a2p = f.create_entity("IfcAxis2Placement3D", Location=_p3(0, 0, 0))
    return f.create_entity("IfcLocalPlacement", RelativePlacement=a2p)


def box(name, long_name, x0, y0, w, d, h=2.6, cls="IfcSpace"):
    el = ifcopenshell.api.root.create_entity(f, ifc_class=cls, name=name)
    if long_name is not None and hasattr(el, "LongName"):
        el.LongName = long_name
    pts2d = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + d), (x0, y0 + d)]
    ipts = [
        f.create_entity("IfcCartesianPoint", (float(x), float(y))) for x, y in pts2d
    ]
    ipts.append(ipts[0])
    poly = f.create_entity("IfcPolyline", Points=ipts)
    profile = f.create_entity(
        "IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly
    )
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=f.create_entity("IfcAxis2Placement3D", Location=_p3(0, 0, 0)),
        ExtrudedDirection=f.create_entity("IfcDirection", (0.0, 0.0, 1.0)),
        Depth=float(h),
    )
    shape = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    el.Representation = f.create_entity(
        "IfcProductDefinitionShape", Representations=[shape]
    )
    el.ObjectPlacement = _identity_placement()
    if cls == "IfcSpace":
        ifcopenshell.api.aggregate.assign_object(
            f, products=[el], relating_object=storey
        )
    else:
        ifcopenshell.api.spatial.assign_container(
            f, products=[el], relating_structure=storey
        )
    return el


# 2 pièces qui se chevauchent (chevauchement_pieces attendu)
s1 = box("SEJOUR", "Séjour", 0, 0, 4, 3)
s2 = box("CH1", "Chambre 1", 3.5, 0, 3, 3)  # recouvre s1 sur 0.5m
# 1 placard inclus dans une chambre
s3 = box("PLA", "Placard", 0.2, 0.2, 1.0, 1.0)  # inclus dans s1
# 1 mur qui traverse le séjour (perte de surface attendue)
w1 = box("MUR1", None, 1.0, 0, 0.2, 3, h=2.6, cls="IfcWall")

# Zone regroupant s1,s2,s3
zone = ifcopenshell.api.root.create_entity(f, ifc_class="IfcZone", name="Logt A")
ifcopenshell.api.group.assign_group(f, products=[s1, s2, s3], group=zone)

path = "/tmp/mini.ifc"
f.write(path)
print(
    "IFC écrit:",
    path,
    "| spaces:",
    len(f.by_type("IfcSpace")),
    "| walls:",
    len(f.by_type("IfcWall")),
    "| zones:",
    len(f.by_type("IfcZone")),
)

# --- Exécution des analyseurs --------------------------------------------- #
os.environ["AUDIT_OUTPUT_DIR"] = "/tmp/out"
from ifc_openshell_mcp.analyzers import (
    inventory,
    space_clash,
    surface_loss,
    boundaries,
    openings,
)
from ifc_openshell_mcp import ifc_utils

model = ifc_utils.open_model(path)
inv = inventory.run(model, file_name="mini.ifc")
clash = space_clash.run(model)
loss = surface_loss.run(model)
bnd = boundaries.run(model)
opn = openings.run(model, file_name="mini.ifc")

print("\n-- inventory --")
print(
    "  spaces:", inv["counts"], "| exemple flags:", [s["flags"] for s in inv["spaces"]]
)
print(
    "  zones:", [(z["name"], z["flags"], z["typologie_calculee"]) for z in inv["zones"]]
)
print("-- space_clash --")
print("  ", clash["counts"])
for fd in clash["findings"]:
    print("   ->", fd["classification"], fd["a"], "/", fd["b"])
print("-- surface_loss --")
print("  ", loss["counts"])
for loss_item in loss["losses"]:
    print(
        "   ->",
        loss_item["name"],
        loss_item["perte_totale_m2"],
        "m2",
        loss_item["severity"],
    )
print("-- boundaries --", bnd["counts"])
print(
    "-- openings --",
    opn["counts"],
    "sans_reservations:",
    opn["structure_sans_reservations"],
)

# Validation : le pipeline géométrique a bien produit des footprints
assert inv["counts"]["n_spaces"] == 3
assert any(s["area_recalc_m2"] for s in inv["spaces"]), "footprints non calculés !"
assert clash["counts"]["n_spaces_geom"] == 3, "geometry manquante"
print("\nSMOKE TEST GÉOMÉTRIE OK ✔  (footprints, clash, STRtree fonctionnels)")
