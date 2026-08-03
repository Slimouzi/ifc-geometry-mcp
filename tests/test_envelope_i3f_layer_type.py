"""Sélection I3F de l'enveloppe : calque ArchiCAD + filtre de nom de type.

Ce que ces tests verrouillent, et pourquoi :

- le nom de type métier vit dans le **type IFC** (``IfcWallType.Name``), pas
  dans ``ObjectType`` ni ``PredefinedType``. Sur les maquettes ArchiCAD,
  ``PredefinedType`` vaut ``ELEMENTEDWALL`` pour **tous** les murs : le prendre
  comme clé de regroupement écrase toute la décomposition métier en un seul
  type. C'est le défaut que ces tests interdisent de revenir ;
- le **calque** délimite l'enveloppe ; un filtre géométrique « extérieur » ne le
  remplace pas — il retient aussi habillages et ouvrages annexes ;
- au sein du calque, ``type_pattern`` sépare murs extérieurs et habillages : ces
  derniers restent listés en ``hors_filtre_type``, **hors du total métier**.

Les valeurs de l'extraction de référence (8 types, 2071,18 m², SHAB 2164,68,
ratio 0,9568) sont reproduites ici sur une maquette synthétique de même forme.
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

LAYER_ENVELOPPE = "221 - MURS - Extérieurs périphériques.Exndo"
LAYER_AUTRE = "232 - MURS - Intérieurs non porteurs - Cloisons.Exndo"
LAYER_PATTERN = r"221|ext[ée]rieurs?\s+p[ée]riph[ée]riques"
TYPE_PATTERN = r"^ME[ _]"

# Les 8 types métier de l'extraction de référence (Σ = 2071,18 m²).
TYPES_METIER = [
    ("ME 8+36+6 : Fin ext + Bois Paille + Fin int 500", 24, 313.14),
    ("ME_R+1_Enduit_recoupement 530 x 2850", 24, 872.01),
    ("ME_RDC_36ep 509 x 2850", 1, 51.16),
    ("ME_RDC_36ep 509 x 2980", 3, 168.24),
    ("ME_RDC_36ep_Enduit Chaux_sans bavette 509 x 2850", 10, 379.72),
    ("ME_RDC_36ep_Enduit Chaux_sans bavette 509 x 3020", 2, 53.19),
    ("ME_RDC_36ep_soubassement 509 x 2850", 2, 52.29),
    ("ME_RDC_36ep_soubassement 509 x 2980", 6, 181.44),
]
TOTAL_FACADE = 2071.18
# Habillages présents sur le MÊME calque, exclus du total métier par le filtre.
TYPES_HABILLAGE = [("Métal - Zinc 50", 3, 120.5), ("Bois - Bardage 22", 2, 80.25)]
SHAB_ATTENDUE = 2164.68
RATIO_ATTENDU = 0.9568

# Annexes non habitables, RATTACHÉES À UNE ZONE : elles seraient donc comptées
# dans la SHAB si leur exclusion échouait. Une par chemin d'exclusion.
ANNEXES_ZONEES = [
    ("CAVE", 300.0),  # normalisé -> cave
    ("BALCON", 12.0),  # normalisé -> exterieur
    ("LOCAL TECHNIQUE", 18.0),  # normalisé -> technique
    ("GARAGE", 25.0),  # normalisé -> autre : exclu sur le libellé brut
    ("CAGE D'ESCALIER", 30.0),  # normalisé -> autre : idem
]
SHAB_EXCLUSIONS_ATTENDUES = sum(a for _, a in ANNEXES_ZONEES)


def _model():
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

    def add_walls(type_name, count, total_area, layer):
        """`count` murs d'un même type IFC, se partageant `total_area`."""
        wtype = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcWallType", name=type_name
        )
        each = total_area / count
        for _ in range(count):
            w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Mur")
            # ArchiCAD renseigne PredefinedType pour TOUS les murs : si le
            # regroupement le lisait, les 8 types n'en feraient plus qu'un.
            w.ObjectType = "ELEMENTEDWALL"
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
                properties={"Calque": layer},
            )
            ifcopenshell.api.pset.edit_qto(
                f,
                qto=ifcopenshell.api.pset.add_qto(
                    f, product=w, name="Qto_WallBaseQuantities"
                ),
                properties={"NetSideArea": float(each)},
            )

    for name, n, area in TYPES_METIER:
        add_walls(name, n, area, LAYER_ENVELOPPE)
    for name, n, area in TYPES_HABILLAGE:
        add_walls(name, n, area, LAYER_ENVELOPPE)
    # Mur d'un AUTRE calque : hors périmètre, il ne doit apparaître nulle part.
    add_walls("CL 7 : sur ossature 70", 2, 500.0, LAYER_AUTRE)

    # Pièces : seules celles rattachées à une zone comptent dans la SHAB.
    zone = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcZone", name="Logement T3"
    )
    zoned = []
    for i, area in enumerate((1200.0, 964.68)):
        sp = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcSpace", name=f"SEJOUR {i}"
        )
        ifcopenshell.api.aggregate.assign_object(
            f, products=[sp], relating_object=storey
        )
        ifcopenshell.api.pset.edit_qto(
            f,
            qto=ifcopenshell.api.pset.add_qto(
                f, product=sp, name="Qto_SpaceBaseQuantities"
            ),
            properties={"NetFloorArea": float(area)},
        )
        zoned.append(sp)
    # Annexes ZONÉES : présentes dans un logement mais non habitables. Chacune
    # emprunte un chemin d'exclusion différent — type normalisé pour CAVE
    # (`cave`), BALCON (`exterieur`) et LOCAL TECHNIQUE (`technique`), libellé
    # brut pour GARAGE et ESCALIER que la normalisation classe en `autre`.
    for nom, area in ANNEXES_ZONEES:
        annexe = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSpace", name=nom)
        ifcopenshell.api.aggregate.assign_object(
            f, products=[annexe], relating_object=storey
        )
        ifcopenshell.api.pset.edit_qto(
            f,
            qto=ifcopenshell.api.pset.add_qto(
                f, product=annexe, name="Qto_SpaceBaseQuantities"
            ),
            properties={"NetFloorArea": float(area)},
        )
        zoned.append(annexe)
    ifcopenshell.api.group.assign_group(f, products=zoned, group=zone)

    # Pièce HORS zone : n'appartient à aucun logement → hors SHAB.
    hors = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcSpace", name="CIRCULATION"
    )
    ifcopenshell.api.aggregate.assign_object(f, products=[hors], relating_object=storey)
    ifcopenshell.api.pset.edit_qto(
        f,
        qto=ifcopenshell.api.pset.add_qto(
            f, product=hors, name="Qto_SpaceBaseQuantities"
        ),
        properties={"NetFloorArea": 999.0},
    )
    return f


@pytest.fixture(scope="module")
def payload():
    return envelope.run(
        _model(),
        file_name="synthetique.ifc",
        seuil_3f=0.9,
        layer_pattern=LAYER_PATTERN,
        type_pattern=TYPE_PATTERN,
    )


# ── nom de type métier : IfcWallType.Name, pas PredefinedType ──────────


def test_business_type_names_come_from_the_ifc_type(payload):
    noms = {r["type"] for r in payload["par_type"]}
    assert noms == {name for name, _, _ in TYPES_METIER}


def test_no_collapse_into_a_single_predefined_type(payload):
    """Garde-fou : `ELEMENTEDWALL` ne doit jamais servir de clé de regroupement."""
    tous = [r["type"] for r in payload["par_type"] + payload["hors_filtre_type"]]
    assert "ELEMENTEDWALL" not in tous
    assert len(payload["par_type"]) == 8, "les 8 types métier doivent rester distincts"


def test_wall_type_resolution_order():
    """Le type IFC prime ; `PredefinedType` n'est qu'un dernier recours."""
    f = ifcopenshell.api.project.create_file(version="IFC4")
    wtype = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcWallType", name="ME_36"
    )
    w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Mur")
    w.ObjectType = "ELEMENTEDWALL"
    ifcopenshell.api.type.assign_type(f, related_objects=[w], relating_type=wtype)
    assert envelope._wall_type(w) == "ME_36"

    sans_type = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcWall", name="Mur nu"
    )
    sans_type.ObjectType = "ELEMENTEDWALL"
    assert envelope._wall_type(sans_type) == "ELEMENTEDWALL"  # dernier recours assumé


# ── totaux métier reproduisant l'extraction de référence ───────────────


def test_facade_total_matches_reference(payload):
    assert payload["summary"]["superficie_facades_m2"] == pytest.approx(
        TOTAL_FACADE, abs=0.05
    )


def test_shab_counts_only_zoned_non_annex_spaces(payload):
    # 1200 + 964,68 retenues ; les 5 annexes zonées et la circulation hors zone
    # sont exclues. Si UNE seule exclusion échouait, la SHAB augmenterait.
    assert payload["summary"]["shab_m2"] == pytest.approx(SHAB_ATTENDUE, abs=0.05)
    assert payload["diagnostics"]["counts"]["n_pieces_shab"] == 2
    assert payload["diagnostics"]["shab_exclusions_m2"] == pytest.approx(
        SHAB_EXCLUSIONS_ATTENDUES
    )


@pytest.mark.parametrize(
    "label",
    [
        "CAVE",
        "CELLIER",
        "BALCON",
        "TERRASSE",
        "LOGGIA",
        "LOCAL TECHNIQUE",
        "GARAGE",
        "CAGE D'ESCALIER",
    ],
)
def test_annexes_are_excluded_from_i3f_shab(label):
    """Chaque annexe est exclue, quel que soit son chemin d'exclusion.

    ``normalize_room_type`` ne rend PAS les libellés métier : « balcon » devient
    ``exterieur``, « local technique » ``technique``, et « garage » / « escalier »
    retombent sur ``autre``. Comparer les libellés métier au type normalisé
    n'excluait donc que ``cave`` et ``cellier``.
    """
    assert envelope._is_i3f_shab_excluded(label) is True


@pytest.mark.parametrize("label", ["SEJOUR", "CHAMBRE 01", "CUISINE", "SALLE DE BAIN"])
def test_habitable_rooms_are_kept_in_i3f_shab(label):
    assert envelope._is_i3f_shab_excluded(label) is False


def test_ratio_matches_reference(payload):
    assert payload["summary"]["ratio_fac_shab"] == pytest.approx(
        RATIO_ATTENDU, abs=0.0005
    )


def test_seuil_conformity_is_evaluated(payload):
    assert payload["summary"]["seuil_i3f"] == 0.9
    assert payload["summary"]["conforme_seuil"] is False  # 0,9568 > 0,9


# ── hors filtre : distinct, et jamais dans le total métier ─────────────


def test_hors_filtre_is_distinct_and_excluded_from_business_total(payload):
    hors = {r["type"] for r in payload["hors_filtre_type"]}
    assert hors == {name for name, _, _ in TYPES_HABILLAGE}
    assert hors.isdisjoint({r["type"] for r in payload["par_type"]})

    retenu = sum(r["net_side_area_m2"] for r in payload["par_type"])
    exclu = sum(r["net_side_area_m2"] for r in payload["hors_filtre_type"])
    assert payload["summary"]["superficie_facades_m2"] == pytest.approx(
        retenu, abs=0.05
    )
    assert payload["summary"]["superficie_calque_total_m2"] == pytest.approx(
        retenu + exclu, abs=0.05
    )
    assert exclu > 0, "le cas testé doit réellement comporter des habillages"


def test_walls_from_other_layers_are_out_of_scope(payload):
    tous = {r["type"] for r in payload["par_type"] + payload["hors_filtre_type"]}
    assert "CL 7 : sur ossature 70" not in tous
    total_calque = payload["summary"]["superficie_calque_total_m2"]
    assert (
        total_calque < TOTAL_FACADE + 500.0
    )  # les 500 m² de l'autre calque sont dehors


def test_wall_counts_are_reported(payload):
    counts = payload["diagnostics"]["counts"]
    attendu = sum(n for _, n, _ in TYPES_METIER) + sum(n for _, n, _ in TYPES_HABILLAGE)
    assert counts["n_murs_calque"] == attendu
    assert counts["n_murs_retenus"] == sum(n for _, n, _ in TYPES_METIER)
    assert counts["n_types_facade"] == 8
    assert counts["n_types_hors_filtre"] == 2


# ── filtres explicites, tracés dans le document ────────────────────────


def test_filters_are_recorded_in_diagnostics(payload):
    """Les motifs ET leur effet sont dans le JSON : la sélection est auditable.

    Les motifs seuls ne permettent pas de relire un résultat — il faut voir ce
    qu'ils ont retenu et ce qu'ils ont écarté pour constater, sans rouvrir l'IFC,
    qu'une couche de façade manque ou qu'un habillage a été compté.
    """
    filtres = payload["diagnostics"]["filters"]
    assert filtres["mode"] == "layer_type_filter"
    assert filtres["layer_pattern"] == LAYER_PATTERN
    assert filtres["type_pattern"] == TYPE_PATTERN
    assert filtres["types_retenus"] == sorted(n for n, _, _ in TYPES_METIER)
    assert filtres["types_rejetes"] == sorted(n for n, _, _ in TYPES_HABILLAGE)
    assert payload["summary"]["methode_facade"] == "layer_type_filter"


def test_without_type_pattern_all_layer_walls_are_retained():
    doc = envelope.run(
        _model(), file_name="s.ifc", layer_pattern=LAYER_PATTERN, type_pattern=None
    )
    assert doc["hors_filtre_type"] == []
    assert len(doc["par_type"]) == len(TYPES_METIER) + len(TYPES_HABILLAGE)


def test_geometric_mode_remains_the_default():
    """Sans aucun motif, la sélection géométrique reste le comportement par défaut."""
    doc = envelope.run(_model(), file_name="s.ifc")
    assert doc["summary"]["methode_facade"] in ("space_boundaries", "is_external_flag")
    # La trace de filtre existe désormais dans les TROIS modes : un contrat sans
    # bloc ``filters`` laissait le lecteur deviner par quel chemin le total avait
    # été obtenu. Ici elle dit « aucun motif appliqué ».
    filtres = doc["diagnostics"]["filters"]
    assert filtres["mode"] == "geometric"
    assert filtres["layer_pattern"] is None
    assert filtres["type_pattern"] is None
