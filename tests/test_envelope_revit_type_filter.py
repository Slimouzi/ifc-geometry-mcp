"""Sélection de l'enveloppe sur une maquette **Revit multicouche**, sans calque.

Le cas réel : sur la maquette Dieppe (export Revit, IFC2X3), la sélection I3F
« calque + type » ne retient **rien** — un export Revit n'expose aucun calque
ArchiCAD — et la sélection purement géométrique retient **tout**, soit environ
9 030 m² de façade pour 2 392 m² de SHAB, un ratio de 3,77 physiquement absurde.

La cause n'est pas le calcul mais la modélisation : chaque façade est faite de
murs **superposés** — structure porteuse, doublage isolant, peau extérieure.
Les additionner compte la même façade trois ou quatre fois.

``geometric_type_filter`` tranche cette superposition : les murs extérieurs sont
trouvés géométriquement, puis ``type_pattern`` désigne la couche qui représente
la façade. Le résultat doit être atteignable **par les seuls paramètres** —
aucun filtrage du contrat après génération, sans quoi la recette n'est pas
rejouable et le chiffre livré au client n'est pas reproductible.

Les valeurs reproduisent la maquette réelle : 11 types bruts (9 030,81 m²),
5 types retenus (2 206,19 m²), SHAB 2 392,64 m², ratio 0,9221.
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

# Motif du chantier réel. Il vit ICI, dans le test — **jamais** dans le produit :
# une liste de types propre à un projet codée dans le moteur reproduirait le
# défaut que le verrou d'identité vient de fermer côté audit-bim.
TYPE_PATTERN_DIEPPE = r"MUR ENDUIT|BARDAGE BOIS|ZINC|VERRE REGLIT"

# La peau extérieure : une seule couche par façade.
PEAUX = [
    ("Mur de base:MUR ENDUIT 20 mm", 54, 900.13),
    ("Mur de base:MUR ENDUIT 20 mm COULEUR", 38, 483.07),
    ("Mur de base:BARDAGE BOIS 20mm + VENTIL 50mm", 86, 722.60),
    ("Mur de base:VERRE REGLIT", 64, 59.54),
    ("Mur de base:ZINC", 6, 40.85),
]
# Structure et doublages : la MÊME façade, vue par ses autres couches. Marqués
# extérieurs eux aussi — c'est bien pour cela qu'un filtre géométrique seul ne
# suffit pas.
COUCHES_INTERNES = [
    ("Mur de base:BETON 200mm", 279, 4001.30),
    ("Mur de base:MOB 150mm + OSB", 119, 1347.45),
    ("Mur de base:ISOLANT 140 + BA13", 88, 847.73),
    ("Mur de base:ISOLANT laine de bois 60mm + BA13", 44, 412.55),
    ("Mur de base:ISOLANT 120", 6, 189.38),
    ("Mur de base:ISOLANT 140", 6, 26.21),
]
TOTAL_PEAUX = round(sum(a for _, _, a in PEAUX), 2)  # 2206.19
TOTAL_BRUT = round(TOTAL_PEAUX + sum(a for _, _, a in COUCHES_INTERNES), 2)  # 9030.81
SHAB = 2392.64
RATIO_ATTENDU = round(TOTAL_PEAUX / SHAB, 4)  # 0.9221


def _model():
    """Maquette Revit synthétique : pas de calque, type porté par IfcWallType."""
    f = ifcopenshell.api.project.create_file(version="IFC4")
    proj = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="P")
    lu = ifcopenshell.api.unit.add_si_unit(f, unit_type="LENGTHUNIT")
    ifcopenshell.api.unit.assign_unit(f, units=[lu])
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="S")
    bld = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="REZ-DE-CHAUSSEE"
    )
    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=proj)
    ifcopenshell.api.aggregate.assign_object(f, products=[bld], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=bld)

    def add_walls(type_name, count, total_area):
        wtype = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcWallType", name=type_name
        )
        each = total_area / count
        for i in range(count):
            w = ifcopenshell.api.root.create_entity(
                f,
                ifc_class="IfcWall",
                # Le Name d'instance Revit porte l'identifiant de l'élément :
                # s'il servait de clé de regroupement, chaque mur ferait son
                # propre « type » et la décomposition métier n'existerait plus.
                name=f"{type_name}:{3400000 + i}",
            )
            ifcopenshell.api.type.assign_type(
                f, related_objects=[w], relating_type=wtype
            )
            ifcopenshell.api.spatial.assign_container(
                f, products=[w], relating_structure=storey
            )
            # Aucun pset ArchiCADProperties : c'est tout l'enjeu du mode.
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

    for name, n, area in PEAUX + COUCHES_INTERNES:
        add_walls(name, n, area)
    # Cloison intérieure : jamais retenue, par aucun mode.
    cloison = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcWallType", name="Mur de base:Intérieur - Cloison 50 mm"
    )
    for _ in range(390):
        w = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Cloison")
        ifcopenshell.api.type.assign_type(f, related_objects=[w], relating_type=cloison)
        ifcopenshell.api.spatial.assign_container(
            f, products=[w], relating_structure=storey
        )
        ifcopenshell.api.pset.edit_pset(
            f,
            pset=ifcopenshell.api.pset.add_pset(f, product=w, name="Pset_WallCommon"),
            properties={"IsExternal": False},
        )
        ifcopenshell.api.pset.edit_qto(
            f,
            qto=ifcopenshell.api.pset.add_qto(
                f, product=w, name="Qto_WallBaseQuantities"
            ),
            properties={"NetSideArea": 3.3},
        )

    zone = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcZone", name="LOGEMENT 01"
    )
    zoned = []
    for i, area in enumerate((1200.0, 1192.64)):
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
    ifcopenshell.api.group.assign_group(f, products=zoned, group=zone)
    return f


@pytest.fixture(scope="module")
def modele():
    return _model()


@pytest.fixture(scope="module")
def payload(modele):
    return envelope.run(
        modele, file_name="DIEPPE-7427L.ifc", type_pattern=TYPE_PATTERN_DIEPPE
    )


# ── le type métier vient du type IFC, pas du Name d'instance ───────────


def test_business_type_comes_from_the_ifc_type_not_the_instance_name(payload):
    """Sinon chaque mur ferait son propre type : 390 « types » au lieu de 5."""
    types = {r["type"] for r in payload["par_type"]}
    assert types == {n for n, _, _ in PEAUX}
    assert not any(":34" in t for t in types), types


# ── le mode : type_pattern SANS layer_pattern ──────────────────────────


def test_type_pattern_alone_selects_the_geometric_type_filter_mode(payload):
    assert payload["summary"]["methode_facade"] == "geometric_type_filter"
    assert payload["diagnostics"]["filters"]["mode"] == "geometric_type_filter"


def test_raw_geometric_total_is_never_served_as_a_client_result(modele):
    """Sans filtre, le total compte la même façade plusieurs fois.

    ~9 030 m² n'est pas un résultat livrable : c'est la somme des couches. Il
    reste accessible en diagnostic, jamais comme total métier.
    """
    brut = envelope.run(modele, file_name="s.ifc")

    assert brut["summary"]["superficie_facades_nette_m2"] == pytest.approx(
        TOTAL_BRUT, abs=0.05
    )
    # Le mode filtré, lui, ne retient que la peau.
    filtre = envelope.run(modele, file_name="s.ifc", type_pattern=TYPE_PATTERN_DIEPPE)
    assert filtre["summary"]["superficie_facades_nette_m2"] == pytest.approx(
        TOTAL_PEAUX, abs=0.05
    )
    # Le total brut reste lisible comme diagnostic, hors total métier.
    assert filtre["summary"]["superficie_calque_total_m2"] == pytest.approx(
        TOTAL_BRUT, abs=0.05
    )


def test_filtered_result_matches_the_real_model(payload):
    """5 lignes métier, 2 206,19 m², ratio 0,9221."""
    assert len(payload["par_type"]) == 5
    assert payload["summary"]["superficie_facades_nette_m2"] == pytest.approx(
        TOTAL_PEAUX, abs=0.05
    )
    assert payload["summary"]["shab_m2"] == pytest.approx(SHAB, abs=0.05)
    assert payload["summary"]["ratio_fac_shab"] == pytest.approx(
        RATIO_ATTENDU, abs=1e-3
    )


def test_inner_layers_are_listed_as_rejected_not_summed(payload):
    """Les couches écartées restent visibles — mais hors du total métier."""
    rejetes = {r["type"] for r in payload["hors_filtre_type"]}
    assert {n for n, _, _ in COUCHES_INTERNES} <= rejetes
    total_retenu = sum(r["net_side_area_m2"] for r in payload["par_type"])
    assert total_retenu == pytest.approx(TOTAL_PEAUX, abs=0.05)


def test_interior_partitions_are_out_of_scope_entirely(payload):
    """Une cloison non extérieure n'apparaît ni en retenu, ni en rejeté."""
    tous = {r["type"] for r in payload["par_type"]} | {
        r["type"] for r in payload["hors_filtre_type"]
    }
    assert not any("Cloison" in t for t in tous), tous


# ── traçabilité : le contrat suffit à relire la sélection ──────────────


def test_contract_records_the_applied_filter(payload):
    filtres = payload["diagnostics"]["filters"]
    assert filtres["mode"] == "geometric_type_filter"
    assert filtres["layer_pattern"] is None
    assert filtres["type_pattern"] == TYPE_PATTERN_DIEPPE
    assert filtres["types_retenus"] == sorted(n for n, _, _ in PEAUX)
    assert set(filtres["types_rejetes"]) >= {n for n, _, _ in COUCHES_INTERNES}


def test_result_is_reproducible_from_parameters_alone(modele):
    """Critère de merge : aucun filtrage manuel après génération.

    Le contrat porte le motif ; rejouer ce seul motif redonne le même document.
    Si un post-traitement avait été nécessaire, les deux exécutions
    divergeraient — et le chiffre livré au client ne serait pas reproductible.
    """
    premier = envelope.run(modele, file_name="s.ifc", type_pattern=TYPE_PATTERN_DIEPPE)
    motif_relu = premier["diagnostics"]["filters"]["type_pattern"]
    mode_relu = premier["diagnostics"]["filters"]["mode"]

    second = envelope.run(
        modele, file_name="s.ifc", type_pattern=motif_relu, filter_mode=mode_relu
    )

    assert second["par_type"] == premier["par_type"]
    assert second["hors_filtre_type"] == premier["hors_filtre_type"]
    assert second["summary"] == premier["summary"]


# ── le mode explicite ne se dégrade jamais en silence ──────────────────


def test_explicit_mode_is_honoured(modele):
    doc = envelope.run(
        modele,
        file_name="s.ifc",
        type_pattern=TYPE_PATTERN_DIEPPE,
        filter_mode="geometric_type_filter",
    )
    assert doc["summary"]["methode_facade"] == "geometric_type_filter"


def test_explicit_mode_without_its_pattern_is_an_error(modele):
    """Se rabattre en silence changerait la nature du total sans le dire."""
    with pytest.raises(envelope.EnvelopeFilterModeError, match="type_pattern"):
        envelope.run(modele, file_name="s.ifc", filter_mode="geometric_type_filter")


def test_layer_mode_without_layer_pattern_is_an_error(modele):
    with pytest.raises(envelope.EnvelopeFilterModeError, match="layer_pattern"):
        envelope.run(
            modele,
            file_name="s.ifc",
            type_pattern=TYPE_PATTERN_DIEPPE,
            filter_mode="layer_type_filter",
        )


def test_geometric_mode_refuses_to_silently_ignore_patterns(modele):
    """Accepter un motif puis ne pas l'appliquer serait le pire des cas."""
    with pytest.raises(envelope.EnvelopeFilterModeError, match="ignor"):
        envelope.run(
            modele,
            file_name="s.ifc",
            type_pattern=TYPE_PATTERN_DIEPPE,
            filter_mode="geometric",
        )


def test_unknown_mode_is_rejected(modele):
    with pytest.raises(envelope.EnvelopeFilterModeError, match="inconnu"):
        envelope.run(modele, file_name="s.ifc", filter_mode="magique")


# ── le ratio a une définition unique ───────────────────────────────────


def test_ratio_is_net_over_shab_in_every_mode(modele):
    """Deux définitions concurrentes ont circulé : 0,92 dans le classeur Excel,
    1,05 dans le contrat (menuiseries incluses). Une seule subsiste."""
    for kwargs in (
        {"type_pattern": TYPE_PATTERN_DIEPPE},
        {},
    ):
        doc = envelope.run(modele, file_name="s.ifc", **kwargs)
        s = doc["summary"]
        attendu = s["superficie_facades_nette_m2"] / s["shab_m2"]
        assert s["ratio_fac_shab"] == pytest.approx(attendu, abs=1e-3), kwargs
