"""Tests de ``export_computed_base_quantities`` : contrat JSON des quantités
calculées (keyé GlobalId), sans écriture IFC, compatible fusion gap-only."""

from __future__ import annotations

import asyncio
import json

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import pytest

from ifc_openshell_mcp import server

_REQUIRED_FIELDS = {
    "global_id",
    "ifc_class",
    "qto",
    "quantity",
    "value",
    "unit",
    "method",
    "status",
    "source",
}


def _build_ifc(path: str) -> None:
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
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
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="S")
    bld = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="B")
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

    box("S1", 0, 0, 4, 3)  # IfcSpace → NetFloorArea ~12
    box("SL1", 0, 10, 5, 4, h=0.2, cls="IfcSlab")  # IfcSlab → NetArea ~20
    box("WIN1", 20, 0, 1.2, 0.1, h=1.0, cls="IfcWindow")  # Width/Height
    box("W1", 1, 0, 0.2, 3, cls="IfcWall")  # enveloppe → hors scope par défaut
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
    _build_ifc(str(src))
    return src, in_dir, out_dir


def _load_json(path):
    return json.loads(open(path, encoding="utf-8").read())


def test_export_writes_json_with_schema_and_fields(env):
    _src, _in, out_dir = env
    res = server.export_computed_base_quantities("maquette.ifc")
    doc = _load_json(res["json_path"])
    assert doc["schema"] == "computed_base_quantities/v1"
    assert doc["source"]["ifc_file"].endswith("maquette.ifc")
    assert doc["source"]["producer"] == "ifc-geometry"
    assert doc["source"]["tool"] == "export_computed_base_quantities"
    assert doc["created_at"]
    assert doc["quantities"], "au moins une quantité calculée"
    for q in doc["quantities"]:
        assert _REQUIRED_FIELDS <= set(q), (
            f"champs manquants : {_REQUIRED_FIELDS - set(q)}"
        )
        assert q["source"] == "computed_ifcopenshell"


def test_export_covers_dieppe_scope_quantities(env):
    _src, _in, _out = env
    res = server.export_computed_base_quantities("maquette.ifc")
    doc = _load_json(res["json_path"])
    computed = {
        (q["ifc_class"], q["quantity"])
        for q in doc["quantities"]
        if q["status"] == "computed"
    }
    assert ("IfcSpace", "NetFloorArea") in computed
    assert ("IfcSlab", "NetArea") in computed
    assert ("IfcWindow", "Width") in computed
    assert ("IfcWindow", "Height") in computed
    # Valeurs plausibles (gap-only en aval → value doit être présent).
    space_area = next(
        q["value"]
        for q in doc["quantities"]
        if q["ifc_class"] == "IfcSpace" and q["quantity"] == "NetFloorArea"
    )
    assert space_area == pytest.approx(12.0, abs=0.3)


def test_envelope_excluded_by_default_scope(env):
    _src, _in, _out = env
    res = server.export_computed_base_quantities("maquette.ifc")
    doc = _load_json(res["json_path"])
    classes = {q["ifc_class"] for q in doc["quantities"]}
    assert "IfcWall" not in classes  # NetSideArea = phase 2
    assert "NetSideArea" not in {q["quantity"] for q in doc["quantities"]}


def test_explicit_classes_can_include_wall(env):
    _src, _in, _out = env
    res = server.export_computed_base_quantities("maquette.ifc", classes=["IfcWall"])
    doc = _load_json(res["json_path"])
    assert {q["ifc_class"] for q in doc["quantities"]} == {"IfcWall"}
    assert any(q["quantity"] == "NetSideArea" for q in doc["quantities"])


def test_never_writes_ifc_and_source_unchanged(env):
    src, _in, out_dir = env
    before = src.read_bytes()
    server.export_computed_base_quantities("maquette.ifc")
    assert src.read_bytes() == before
    assert list(out_dir.glob("*.ifc")) == []  # aucun IFC produit
    assert list(out_dir.glob("*_computed_quantities.json"))  # seulement le JSON


def test_coverage_counts(env):
    _src, _in, _out = env
    res = server.export_computed_base_quantities("maquette.ifc")
    cov = res["coverage"]
    assert cov["n_elements"] == 3  # space + slab + window (scope défaut)
    assert cov["n_computed"] >= 4  # NetFloorArea + NetArea + Width + Height


def test_tool_listed_in_mcp():
    tool = asyncio.run(server.mcp.get_tool("export_computed_base_quantities"))
    assert tool is not None
