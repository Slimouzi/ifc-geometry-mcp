"""Tests de ``complete_ifc_base_quantities`` : complétion des BaseQuantities dans
une **copie** de l'IFC, jamais in-place.

Construit une mini-maquette (mètre / SI) avec :
- ``S1`` IfcSpace **sans** Qto → NetFloorArea doit être créée ;
- ``S2`` IfcSpace **avec** Qto_SpaceBaseQuantities.NetFloorArea=99.0 → conservée
  (défaut) ou mise à jour (overwrite) ;
- ``SL1`` IfcSlab, ``W1`` IfcWall, ``WIN1`` IfcWindow → NetArea / NetSideArea /
  Width+Height.
"""

from __future__ import annotations

import asyncio

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.util.element as ue
import pytest

from ifc_openshell_mcp import server


# --------------------------------------------------------------------------- #
#  Construction d'une mini-maquette IFC (mètre)
# --------------------------------------------------------------------------- #
def _build_source(path: str) -> None:
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcProject", name="I3F Test"
    )
    lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")  # mètre
    ifcopenshell.api.unit.assign_unit(f, units=[lu])
    ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=ctx,
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

    def box(name, x0, y0, w, d, h=2.6, cls="IfcSpace"):
        el = ifcopenshell.api.root.create_entity(f, ifc_class=cls, name=name)
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + d), (x0, y0 + d)]
        ipts = [
            f.create_entity("IfcCartesianPoint", (float(x), float(y))) for x, y in pts
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
        el.ObjectPlacement = f.create_entity(
            "IfcLocalPlacement",
            RelativePlacement=f.create_entity(
                "IfcAxis2Placement3D", Location=_p3(0, 0, 0)
            ),
        )
        if cls == "IfcSpace":
            ifcopenshell.api.aggregate.assign_object(
                f, products=[el], relating_object=storey
            )
        else:
            ifcopenshell.api.spatial.assign_container(
                f, products=[el], relating_structure=storey
            )
        return el

    box("S1", 0, 0, 4, 3)  # 12 m², sans Qto
    s2 = box("S2", 10, 0, 2, 2)  # 4 m² géom, mais Qto existante = 99.0
    box("SL1", 0, 10, 5, 4, h=0.2, cls="IfcSlab")
    box("W1", 1, 0, 0.2, 3, h=2.6, cls="IfcWall")
    box("WIN1", 20, 0, 1.2, 0.1, h=1.0, cls="IfcWindow")

    qto = ifcopenshell.api.pset.add_qto(f, product=s2, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.pset.edit_qto(f, qto=qto, properties={"NetFloorArea": 99.0})

    f.write(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(in_dir))
    monkeypatch.setenv("AUDIT_OUTPUT_DIR", str(out_dir))
    src = in_dir / "maquette.ifc"
    _build_source(str(src))
    return src, in_dir, out_dir


def _space_net_floor_area(model, name: str):
    for sp in model.by_type("IfcSpace"):
        if sp.Name == name:
            qtos = ue.get_psets(sp, qtos_only=True) or {}
            return (qtos.get("Qto_SpaceBaseQuantities") or {}).get("NetFloorArea")
    return None


# --------------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------------- #
def test_tool_listed_in_mcp():
    tool = asyncio.run(server.mcp.get_tool("complete_ifc_base_quantities"))
    assert tool is not None


def test_dry_run_writes_no_file(env):
    src, _in_dir, out_dir = env
    res = server.complete_ifc_base_quantities("maquette.ifc", dry_run=True)
    assert res["status"] == "dry_run"
    assert res["summary"]["quantities_created"] > 0
    # Aucun fichier IFC produit dans la sandbox de sortie.
    assert list(out_dir.glob("*.ifc")) == []


def test_confirm_false_blocks_write(env):
    src, _in_dir, out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", dry_run=False, confirm=False
    )
    assert res["status"] == "failed"
    assert "confirm" in res["error"].lower()
    assert list(out_dir.glob("*.ifc")) == []


def test_missing_quantity_added(env):
    src, _in_dir, out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", dry_run=False, confirm=True
    )
    assert res["status"] == "written"
    out = ifcopenshell.open(res["output_ifc"])
    # S1 n'avait pas de Qto → NetFloorArea créée (~12 m²).
    assert _space_net_floor_area(out, "S1") == pytest.approx(12.0, abs=0.2)
    created = [c for c in res["changes"] if c["action"] == "created"]
    assert any(
        c["ifc_class"] == "IfcSpace" and c["quantity"] == "NetFloorArea"
        for c in created
    )


def test_existing_not_overwritten_by_default(env):
    src, _in_dir, out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", dry_run=False, confirm=True, overwrite_existing=False
    )
    out = ifcopenshell.open(res["output_ifc"])
    # S2 gardait NetFloorArea=99.0 → non écrasée.
    assert _space_net_floor_area(out, "S2") == pytest.approx(99.0)
    s2_change = next(
        c
        for c in res["changes"]
        if c["name"] == "S2" and c["quantity"] == "NetFloorArea"
    )
    assert s2_change["action"] == "skipped"
    assert s2_change["reason"] == "exists_not_overwritten"
    assert s2_change["old_value"] == pytest.approx(99.0)


def test_existing_updated_with_overwrite(env):
    src, _in_dir, out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", dry_run=False, confirm=True, overwrite_existing=True
    )
    out = ifcopenshell.open(res["output_ifc"])
    # S2 recalculée (~4 m²), plus 99.0.
    val = _space_net_floor_area(out, "S2")
    assert val == pytest.approx(4.0, abs=0.2)
    assert val != pytest.approx(99.0)
    s2_change = next(
        c
        for c in res["changes"]
        if c["name"] == "S2" and c["quantity"] == "NetFloorArea"
    )
    assert s2_change["action"] == "updated"
    assert s2_change["old_value"] == pytest.approx(99.0)


def test_source_file_unchanged(env):
    src, _in_dir, _out_dir = env
    before = src.read_bytes()
    server.complete_ifc_base_quantities("maquette.ifc", dry_run=False, confirm=True)
    assert src.read_bytes() == before  # source jamais modifiée
    # Et la source ne porte toujours pas de Qto sur S1.
    reopened = ifcopenshell.open(str(src))
    assert _space_net_floor_area(reopened, "S1") is None


def test_output_reopenable_and_has_quantities(env):
    src, _in_dir, _out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", dry_run=False, confirm=True
    )
    out = ifcopenshell.open(res["output_ifc"])  # rouvrable sans erreur
    slab = out.by_type("IfcSlab")[0]
    wall = out.by_type("IfcWall")[0]
    win = out.by_type("IfcWindow")[0]
    assert (ue.get_psets(slab, qtos_only=True).get("Qto_SlabBaseQuantities") or {}).get(
        "NetArea"
    )
    assert (ue.get_psets(wall, qtos_only=True).get("Qto_WallBaseQuantities") or {}).get(
        "NetSideArea"
    )
    win_q = ue.get_psets(win, qtos_only=True).get("Qto_WindowBaseQuantities") or {}
    assert win_q.get("Width") and win_q.get("Height")


def test_classes_filter_scopes_work(env):
    src, _in_dir, _out_dir = env
    res = server.complete_ifc_base_quantities(
        "maquette.ifc", classes=["IfcSlab"], dry_run=True
    )
    touched = {c["ifc_class"] for c in res["changes"]}
    assert touched == {"IfcSlab"}
