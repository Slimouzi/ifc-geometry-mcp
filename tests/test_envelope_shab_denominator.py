"""Le dénominateur du ratio FAC/SHAB a une seule nature, et il se déclare.

La v0.4.0 a unifié la **formule** du ratio (`nette / shab`) dans les trois modes
de sélection d'enveloppe. Mais le dénominateur, lui, ne l'était pas : les modes
filtrés employaient ``_shab_zoned`` (pièces rattachées à une zone, annexes non
habitables exclues) et le mode géométrique ``_shab`` (toutes les pièces).

Deux ratios pouvaient donc différer sur la même maquette sans que rien ne le
signale. Un écart de formule se repère à la relecture ; un écart de **contenu**
du dénominateur, non — c'est ce qui le rend plus sournois.

Une seule définition demeure, et ``summary.methode_shab`` la déclare, au même
titre que ``methode_facade``.
"""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.group
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.type
import ifcopenshell.api.unit
import pytest

from ifc_openshell_mcp.analyzers import envelope

TYPE_FACADE = "ME_Enduit"
TYPE_HABILLAGE = "Zinc_couvertine"
LAYER = "221 - MURS - Extérieurs périphériques"
LAYER_PATTERN = r"221"
TYPE_PATTERN = r"^ME[ _]"

SEJOUR = 120.0
CAVE = 40.0  # annexe : exclue de la SHAB I3F
HORS_ZONE = 500.0  # pièce sans logement : hors SHAB I3F


def _model(*, avec_zone=True, pieces_zonees=True):
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
    lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
    ifcopenshell.api.unit.assign_unit(f, units=[lu])
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="S")
    bld = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="RDC"
    )
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(f, products=[bld], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=bld)

    def add_walls(type_name, count, total_area):
        wtype = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcWallType", name=type_name
        )
        each = total_area / count
        for _ in range(count):
            w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Mur")
            ifcopenshell.api.type.assign_type(
                f, related_objects=[w], relating_type=wtype
            )
            ifcopenshell.api.spatial.assign_container(
                f, products=[w], relating_structure=storey
            )
            ifcopenshell.api.pset.edit_pset(
                f,
                pset=ifcopenshell.api.pset.add_pset(
                    f, product=w, name="ArchiCADProperties"
                ),
                properties={"Calque": LAYER},
            )
            ifcopenshell.api.pset.edit_pset(
                f,
                pset=ifcopenshell.api.pset.add_pset(
                    f, product=w, name="Pset_WallCommon"
                ),
                properties={"IsExternal": True},
            )
            ifcopenshell.api.pset.edit_qto(
                f,
                qto=ifcopenshell.api.pset.add_qto(
                    f, product=w, name="Qto_WallBaseQuantities"
                ),
                properties={"NetSideArea": float(each)},
            )

    add_walls(TYPE_FACADE, 4, 200.0)
    add_walls(TYPE_HABILLAGE, 2, 60.0)

    def add_space(nom, aire):
        sp = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=nom)
        ifcopenshell.api.aggregate.assign_object(
            f, products=[sp], relating_object=storey
        )
        ifcopenshell.api.pset.edit_qto(
            f,
            qto=ifcopenshell.api.pset.add_qto(
                f, product=sp, name="Qto_SpaceBaseQuantities"
            ),
            properties={"NetFloorArea": float(aire)},
        )
        return sp

    sejour = add_space("SEJOUR", SEJOUR)
    cave = add_space("CAVE", CAVE)
    add_space("CIRCULATION", HORS_ZONE)  # jamais zonée

    if avec_zone:
        zone = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcZone", name="Logement T3"
        )
        produits = [sejour, cave] if pieces_zonees else []
        if produits:
            ifcopenshell.api.group.assign_group(f, products=produits, group=zone)
    return f


def _tous_les_modes(f):
    return {
        "layer_type_filter": envelope.run(
            f, file_name="s.ifc", layer_pattern=LAYER_PATTERN, type_pattern=TYPE_PATTERN
        ),
        "geometric_type_filter": envelope.run(
            f, file_name="s.ifc", type_pattern=TYPE_PATTERN
        ),
        "geometric": envelope.run(f, file_name="s.ifc"),
    }


def test_the_three_modes_share_one_shab():
    """Même maquette, trois sélections d'enveloppe, un seul dénominateur.

    Avant, le mode géométrique comptait aussi la pièce hors zone (500 m²) et
    n'excluait pas la cave par libellé brut : son ratio n'était pas comparable
    à celui des modes filtrés.
    """
    docs = _tous_les_modes(_model())

    shabs = {mode: d["summary"]["shab_m2"] for mode, d in docs.items()}
    assert len(set(shabs.values())) == 1, shabs
    # Seul le SEJOUR zoné compte : ni la cave (annexe), ni la circulation
    # (hors logement).
    for mode, valeur in shabs.items():
        assert valeur == pytest.approx(SEJOUR, abs=0.01), mode

    methodes = {d["summary"]["methode_shab"] for d in docs.values()}
    assert methodes == {envelope.METHODE_SHAB_ZONES}


def test_each_mode_declares_the_shab_method():
    """La déclarer est ce qui rend deux ratios comparables en connaissance."""
    for mode, doc in _tous_les_modes(_model()).items():
        assert doc["summary"]["methode_shab"], mode
        assert doc["summary"]["methode_facade"], mode


def test_a_model_without_any_zone_falls_back_but_says_so():
    """Sans convention de zonage, exiger le zonage priverait de tout ratio.

    Le repli est donc autorisé — mais **déclaré**, jamais silencieux.
    """
    doc = envelope.run(_model(avec_zone=False), file_name="s.ifc")

    assert doc["summary"]["methode_shab"] == envelope.METHODE_SHAB_SANS_ZONAGE
    # Toutes les pièces hors annexes : séjour + circulation, cave exclue.
    assert doc["summary"]["shab_m2"] == pytest.approx(SEJOUR + HORS_ZONE, abs=0.01)


def test_zones_present_but_empty_is_a_defect_not_a_fallback():
    """Des zones existent mais aucune pièce n'y est rattachée : SHAB nulle.

    Se replier ici masquerait un vrai défaut de modélisation — les pièces
    n'appartiennent à aucun logement — sous un chiffre d'apparence normale.
    """
    doc = envelope.run(_model(pieces_zonees=False), file_name="s.ifc")

    assert doc["summary"]["methode_shab"] == envelope.METHODE_SHAB_ZONES
    assert doc["summary"]["shab_m2"] == 0.0
    assert doc["summary"]["ratio_fac_shab"] is None
    assert doc["diagnostics"]["counts"]["n_pieces_shab"] == 0
