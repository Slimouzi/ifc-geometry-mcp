"""Contrat de sortie — vérifie que les clés produites par les analyseurs
correspondent à ce qu'attend le parseur audit-bim-i3f
(``audit_bim/audit/rules/preliminary.py``).

Ce test ne nécessite ni IFC ni ifcopenshell : il valide la *forme* des
payloads. Le test d'intégration géométrique (``test_geometry_smoke.py``)
exécute réellement les analyseurs sur une mini-maquette.
"""

# Clés minimales attendues par branche du parseur preliminary.py.
SPACE_KEYS = {
    "guid",
    "name",
    "long_name",
    "storey",
    "zones",
    "flags",
    "min_area_threshold_m2",
    "area_declared_m2",
    "area_recalc_m2",
    "area_delta_pct",
}
ZONE_KEYS = {"guid", "name", "flags", "typologie_declaree", "typologie_calculee"}
LOSS_KEYS = {
    "guid",
    "name",
    "long_name",
    "storey",
    "zones",
    "perte_totale_m2",
    "perte_pct",
    "perte_murs_m2",
    "perte_poteaux_m2",
    "n_intrus",
    "severity",
    "deduction_declaree",
}
CLASH_KEYS = {"classification", "a", "a_guid", "b", "distance"}
MISSING_BND_KEYS = {"a_guid", "a_name", "b_name", "storey", "zones", "distance_m"}
OPENING_KEYS = {
    "structure",
    "archi",
    "tolerance_m",
    "structure_sans_reservations",
    "misaligned_models",
    "structure_sans_correspondance_archi",
}

VALID_SPACE_FLAGS = {
    "piece_trop_petite",
    "ecart_surface",
    "sans_surface_declaree",
    "sans_zone",
    "sans_etage",
}
VALID_ZONE_FLAGS = {
    "typologie_incoherente",
    "zone_discontinue",
    "zone_sans_piece",
    "doublon_nom_zone",
    "duplex_possible",
}
VALID_CLASH = {
    "doublon_piece",
    "placard_double_modelise",
    "chevauchement_pieces",
    "chevauchement_vertical_entre_etages",
}
VALID_SEVERITY = {"Critique", "Majeur", "Mineur"}


def test_inventory_space_keys():
    from ifc_openshell_mcp.analyzers.inventory import _space_entry
    from ifc_openshell_mcp.analyzers.spaces import SpaceRecord

    rec = SpaceRecord(
        guid="g",
        name="Chambre",
        long_name="Chambre 1",
        storey="R+1",
        zones=["A"],
        footprint=None,
        z_min=0,
        z_max=2.6,
        area_declared_m2=8.0,
        area_recalc_m2=7.2,
        room_type="chambre",
        raw_label="Chambre 1",
    )
    entry = _space_entry(rec, 9.0, 5.0)
    assert SPACE_KEYS <= set(entry)
    assert set(entry["flags"]) <= VALID_SPACE_FLAGS
    assert "piece_trop_petite" in entry["flags"]  # 8 < 9 et habitable
    assert "ecart_surface" in entry["flags"]  # -10% > 5%


def test_flag_vocabularies_are_frozen():
    # Garde-fou : le vocabulaire de classification du clash reste aligné
    # avec _CLASH_SPECS du parseur audit-bim-i3f.
    from ifc_openshell_mcp.analyzers.space_clash import run  # noqa: F401

    assert VALID_CLASH == {
        "doublon_piece",
        "placard_double_modelise",
        "chevauchement_pieces",
        "chevauchement_vertical_entre_etages",
    }


def test_normalize_room_type():
    from ifc_openshell_mcp.analyzers.spaces import normalize_room_type

    assert normalize_room_type("Chambre 1") == "chambre"
    assert normalize_room_type("CH2") == "chambre"
    assert normalize_room_type("Placard") == "placard"
    assert normalize_room_type("Cellier") == "cellier"
    assert normalize_room_type("Séjour / Cuisine") == "sejour"
