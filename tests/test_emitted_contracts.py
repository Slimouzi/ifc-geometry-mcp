"""Les JSON émis par ce serveur sont des documents **V1**, pas des legacy migrés.

Garde-fou central du volet producteur : chaque fichier écrit est relu par
``bim_core.contracts`` et doit être accepté

- **sans avertissement** (aucun ``legacy_schema_missing``) : le payload porte
  son ``schema``, il ne passe pas par la tolérance migratoire ;
- **en mode strict** (``BIM_CORE_JSON_STRICT_SCHEMA``) : ce qui est produit
  aujourd'hui survivra au jour où la compat legacy sera retirée.
"""

from __future__ import annotations

import json
import warnings

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import pytest
from bim_core.contracts import (
    SCHEMA_COMPUTED_BASE_QUANTITIES_V1,
    SCHEMA_ENVELOPE_QUANTITIES_V1,
    LegacySchemaWarning,
    load_computed_base_quantities,
    load_envelope_quantities,
)

from ifc_openshell_mcp import server
from ifc_openshell_mcp.analyzers import envelope


def _build_ifc(path: str) -> None:
    """Maquette minimale : une pièce, une dalle, une fenêtre, un mur extérieur."""
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
    lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
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

    box("SEJOUR", 0, 0, 4, 3)
    box("SL1", 0, 10, 5, 4, h=0.2, cls="IfcSlab")
    win = box("WIN1", 20, 0, 1.2, 0.1, h=1.0, cls="IfcWindow")
    wall = box("MUR EXT 25", 1, 0, 0.25, 6, cls="IfcWall")
    ifcopenshell.api.pset.edit_pset(
        f,
        pset=ifcopenshell.api.pset.add_pset(f, product=wall, name="Pset_WallCommon"),
        properties={"IsExternal": True},
    )
    ifcopenshell.api.pset.edit_pset(
        f,
        pset=ifcopenshell.api.pset.add_pset(f, product=win, name="Pset_WindowCommon"),
        properties={"IsExternal": True},
    )
    f.write(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(in_dir))
    monkeypatch.setenv("AUDIT_OUTPUT_DIR", str(out_dir))
    _build_ifc(str(in_dir / "maquette.ifc"))
    return in_dir, out_dir


# ── enveloppe ──────────────────────────────────────────────────────────


def test_emitted_envelope_is_accepted_without_warning(env):
    res = server.extract_envelope_surfaces("maquette.ifc", seuil_3f=0.9)
    with warnings.catch_warnings():
        warnings.simplefilter("error", LegacySchemaWarning)  # toute compat = échec
        payload = load_envelope_quantities(res["json_path"])
    assert payload.schema_ == SCHEMA_ENVELOPE_QUANTITIES_V1
    assert payload.source.producer == "ifc-geometry"
    assert payload.source.tool == "extract_envelope_surfaces"
    assert payload.created_at


def test_emitted_envelope_survives_strict_mode(env, monkeypatch):
    res = server.extract_envelope_surfaces("maquette.ifc")
    monkeypatch.setenv("BIM_CORE_JSON_STRICT_SCHEMA", "true")
    assert load_envelope_quantities(res["json_path"]).summary.shab_m2 is not None


def test_emitted_envelope_carries_business_decomposition(env):
    res = server.extract_envelope_surfaces("maquette.ifc", seuil_3f=0.9)
    payload = load_envelope_quantities(res["json_path"])
    assert payload.summary.seuil_i3f == 0.9
    assert payload.par_type, "au moins un type de façade"
    assert all(r.type for r in payload.par_type)


def test_diagnostics_are_kept_out_of_the_business_total(env):
    """Les compteurs et détails restent en diagnostics — jamais dans un total."""
    res = server.extract_envelope_surfaces("maquette.ifc")
    doc = json.loads(open(res["json_path"], encoding="utf-8").read())
    assert "counts" in doc["diagnostics"]
    assert "menuiseries_detail" in doc["diagnostics"]
    assert "counts" not in doc["summary"]
    # Le total métier ne compte que les types retenus.
    retained = sum(r.get("net_side_area_m2") or 0 for r in doc["par_type"])
    excluded = sum(r.get("net_side_area_m2") or 0 for r in doc["hors_filtre_type"])
    assert doc["summary"]["superficie_facades_nette_m2"] == pytest.approx(retained)
    if excluded:
        assert doc["summary"]["superficie_calque_total_m2"] == pytest.approx(
            retained + excluded
        )


def test_envelope_run_output_is_v1_not_legacy():
    """Le document est construit EN V1, pas assemblé à plat puis migré."""
    doc = envelope.run(_in_memory_model(), file_name="mn.ifc")
    assert doc["schema"] == SCHEMA_ENVELOPE_QUANTITIES_V1
    # Les clés du format historique n'existent plus à la racine.
    for legacy_key in ("superficie_facades_m2", "shab_m2", "file", "counts"):
        assert legacy_key not in doc


def _in_memory_model():
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
        _build_ifc(tmp.name)
        return ifcopenshell.open(tmp.name)


# ── quantités calculées ────────────────────────────────────────────────


def test_emitted_quantities_are_accepted_without_warning(env):
    res = server.export_computed_base_quantities("maquette.ifc")
    with warnings.catch_warnings():
        warnings.simplefilter("error", LegacySchemaWarning)
        payload = load_computed_base_quantities(res["json_path"])
    assert payload.schema_ == SCHEMA_COMPUTED_BASE_QUANTITIES_V1
    assert payload.source.producer == "ifc-geometry"
    assert payload.created_at


def test_emitted_quantities_survive_strict_mode(env, monkeypatch):
    res = server.export_computed_base_quantities("maquette.ifc")
    monkeypatch.setenv("BIM_CORE_JSON_STRICT_SCHEMA", "true")
    assert load_computed_base_quantities(res["json_path"]).quantities


def test_emitted_quantities_are_indexable_by_global_id(env):
    """Le besoin métier — accès keyé — est servi par l'API, pas par le format."""
    res = server.export_computed_base_quantities("maquette.ifc")
    payload = load_computed_base_quantities(res["json_path"])
    index = payload.by_global_id()
    assert index, "au moins un élément avec quantité calculée"
    for gid, quantities in index.items():
        assert isinstance(gid, str) and gid
        assert all(q.value is not None for q in quantities)
    # Le tableau reste PLAT dans le fichier (format déjà émis en production).
    doc = json.loads(open(res["json_path"], encoding="utf-8").read())
    assert isinstance(doc["quantities"], list)
