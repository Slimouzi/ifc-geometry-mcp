"""envelope.json enrichi : par_type (façade filtrée) / hors_filtre_type
(diagnostic, hors total) / superficie_calque_total_m2 / split fenêtres-portes."""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import pytest

from ifc_openshell_mcp.analyzers import envelope


def _model():
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
    lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
    ifcopenshell.api.unit.assign_unit(f, units=[lu])
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="S")
    bld = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="R+1"
    )
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(f, products=[bld], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=bld)

    def wall(name, wtype, netside, *, external):
        el = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name=name)
        el.ObjectType = wtype
        ifcopenshell.api.spatial.assign_container(
            f, products=[el], relating_structure=storey
        )
        qto = ifcopenshell.api.pset.add_qto(
            f, product=el, name="Qto_WallBaseQuantities"
        )
        ifcopenshell.api.pset.edit_qto(
            f, qto=qto, properties={"NetSideArea": float(netside)}
        )
        pset = ifcopenshell.api.pset.add_pset(f, product=el, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(
            f, pset=pset, properties={"IsExternal": bool(external)}
        )
        return el

    # 2 murs extérieurs même type (façade) + 1 mur intérieur (hors filtre).
    wall("MX1", "MUR EXT 25", 100.0, external=True)
    wall("MX2", "MUR EXT 25", 50.0, external=True)
    wall("MI1", "MUR INT 10", 30.0, external=False)

    win = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWindow", name="F1")
    win.OverallWidth, win.OverallHeight = 1.2, 1.0
    door = ifcopenshell.api.root.create_entity(f, ifc_class="IfcDoor", name="P1")
    door.OverallWidth, door.OverallHeight = 0.9, 2.1
    dp = ifcopenshell.api.pset.add_pset(f, product=door, name="Pset_DoorCommon")
    ifcopenshell.api.pset.edit_pset(f, pset=dp, properties={"IsExternal": True})
    return f


def test_par_type_is_filtered_facade_by_type():
    payload = envelope.run(_model(), file_name="mn.ifc", seuil_3f=0.9)
    par = payload["par_type"]
    assert len(par) == 1  # un seul type de façade
    e = par[0]
    assert e["type"] == "MUR EXT 25"
    assert e["netsidearea_m2"] == pytest.approx(150.0)
    assert e["nombre"] == 2


def test_hors_filtre_excluded_from_facade_total():
    payload = envelope.run(_model(), file_name="mn.ifc")
    # Le mur intérieur est en hors_filtre, PAS dans le total façade.
    assert payload["superficie_facades_nette_m2"] == pytest.approx(150.0)
    hors = {h["type"] for h in payload["hors_filtre_type"]}
    assert "MUR INT 10" in hors
    assert payload["superficie_calque_total_m2"] == pytest.approx(180.0)  # 150 + 30


def test_menuiseries_split_fenetres_portes():
    payload = envelope.run(_model(), file_name="mn.ifc")
    assert payload["superficie_menuiseries_fenetres_m2"] == pytest.approx(1.2)
    assert payload["superficie_menuiseries_portes_m2"] == pytest.approx(1.89)


def test_seuil_i3f_alias_present():
    payload = envelope.run(_model(), file_name="mn.ifc", seuil_3f=0.9)
    assert payload["seuil_i3f"] == 0.9 and payload["seuil_3f"] == 0.9
