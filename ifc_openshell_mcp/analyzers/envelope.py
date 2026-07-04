"""extract_envelope_surfaces — surfaces d'enveloppe depuis la géométrie IFC.

Calcule, pour alimenter l'annexe I3F « Extraction surface enveloppe » :

- **Superficie des façades** : aire verticale des murs extérieurs + murs-rideaux
  (parement extérieur, ouvertures déduites) + les menuiseries (qui occupent le
  plan de façade) → aire de façade *brute*.
- **Superficie des menuiseries** : aire des IfcWindow + IfcDoor extérieures
  (largeur × hauteur d'emprise).
- **SHAB** : surface habitable = somme des surfaces nettes des pièces, hors
  annexes (cave, cellier, parking, local technique, extérieur).
- **ratio FAC/SHAB** : compacité de l'enveloppe (façade brute / SHAB).

Produit un dict + un classeur .xlsx au format lu par
``audit_bim.reporting.avp_sources.read_enveloppe`` (libellé en colonne A,
valeur numérique en colonne B ; table détaillée ancrée sur « Composant »).
"""

from __future__ import annotations


from .. import ifc_utils
from .spaces import normalize_room_type

_WALL_TYPES = ("IfcWall", "IfcWallStandardCase")
_CURTAIN_TYPES = ("IfcCurtainWall",)
# Pièces exclues de la surface habitable (annexes non chauffées / extérieur).
_SHAB_EXCLUDE = {"cave", "cellier", "parking", "technique", "exterieur"}


_WALL_LIKE = {"IfcWall", "IfcWallStandardCase", "IfcCurtainWall"}


def _external_wall_guids(model) -> tuple[set[str], str]:
    """GlobalId des murs extérieurs.

    Méthode primaire : ``IfcRelSpaceBoundary`` marquées ``EXTERNAL`` (fiable même
    quand ``Pset_WallCommon.IsExternal`` est absent/faux — cas fréquent des
    exports ArchiCAD). Repli : flag ``IsExternal=True``.
    """
    guids: set[str] = set()
    for rel in model.by_type("IfcRelSpaceBoundary"):
        if getattr(rel, "InternalOrExternalBoundary", None) != "EXTERNAL":
            continue
        el = getattr(rel, "RelatedBuildingElement", None)
        if el is not None and el.is_a() in _WALL_LIKE:
            guids.add(el.GlobalId)
    method = "space_boundaries"
    if not guids:  # repli sur le flag IsExternal
        method = "is_external_flag"
        for t in (*_WALL_TYPES, *_CURTAIN_TYPES):
            for el in model.by_type(t):
                if ifc_utils.is_external(el) is True:
                    guids.add(el.GlobalId)
    return guids, method


def _facades(model) -> tuple[float, int, int, str]:
    """Aire de façade nette = somme des NetSideArea des murs extérieurs (m²)."""
    guids, method = _external_wall_guids(model)
    total = 0.0
    n_ext = n_geom_fallback = 0
    for guid in guids:
        el = model.by_guid(guid)
        a = ifc_utils.quantity(el, "NetSideArea", "GrossSideArea", "GrossArea")
        if a is None:
            a = ifc_utils.vertical_face_area(el)
            if a:
                n_geom_fallback += 1
        if a:
            total += a
            n_ext += 1
    return total, n_ext, n_geom_fallback, method


def _menuiserie_area(el) -> tuple[float, float, float] | None:
    """(largeur, hauteur, surface) d'une baie via attributs/Qto, sinon géométrie."""
    w = getattr(el, "OverallWidth", None)
    h = getattr(el, "OverallHeight", None)
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w and h:
        return float(w), float(h), float(w) * float(h)
    a = ifc_utils.quantity(el, "Area")
    if a is not None:
        return None, None, a
    wh = ifc_utils.bbox_width_height(el)  # fallback géométrique
    if wh:
        return wh[0], wh[1], wh[0] * wh[1]
    return None


def _menuiseries(model) -> tuple[float, int, list[dict]]:
    """Aire des baies : fenêtres + portes extérieures."""
    total = 0.0
    n = 0
    detail: list[dict] = []
    for el in model.by_type("IfcWindow"):
        res = _menuiserie_area(el)
        if res:
            w, h, area = res
            total += area
            n += 1
            detail.append(
                {
                    "type": "IfcWindow",
                    "name": el.Name,
                    "largeur_m": round(w, 2) if w else None,
                    "hauteur_m": round(h, 2) if h else None,
                    "surface_m2": round(area, 2),
                }
            )
    for el in model.by_type("IfcDoor"):
        if ifc_utils.is_external(el) is True:
            res = _menuiserie_area(el)
            if res:
                w, h, area = res
                total += area
                n += 1
                detail.append(
                    {
                        "type": "IfcDoor(ext)",
                        "name": el.Name,
                        "largeur_m": round(w, 2) if w else None,
                        "hauteur_m": round(h, 2) if h else None,
                        "surface_m2": round(area, 2),
                    }
                )
    return total, n, detail


def _shab(model) -> tuple[float, int]:
    """SHAB via NetFloorArea déclarée (Qto), hors annexes. Aucune géométrie."""
    total = 0.0
    n = 0
    for sp in model.by_type("IfcSpace"):
        label = getattr(sp, "LongName", None) or sp.Name or ""
        if normalize_room_type(label) in _SHAB_EXCLUDE:
            continue
        a = ifc_utils.quantity(sp, "NetFloorArea", "GrossFloorArea", "NetArea")
        if a:
            total += a
            n += 1
    return total, n


def run(model, file_name: str, *, seuil_3f: float | None = None) -> dict:
    facade_net, n_ext, n_geom, method = _facades(model)
    menuis, n_menuis, menuis_detail = _menuiseries(model)
    facade_gross = facade_net + menuis
    shab, n_shab = _shab(model)
    ratio = (facade_gross / shab) if shab else None

    return {
        "file": file_name,
        "superficie_facades_m2": round(facade_gross, 2),
        "superficie_facades_nette_m2": round(facade_net, 2),
        "superficie_menuiseries_m2": round(menuis, 2),
        "shab_m2": round(shab, 2),
        "ratio_fac_shab": round(ratio, 3) if ratio is not None else None,
        "seuil_3f": seuil_3f,
        "methode_facade": method,
        "counts": {
            "n_murs_exterieurs": n_ext,
            "n_facades_fallback_geom": n_geom,
            "n_menuiseries": n_menuis,
            "n_pieces_shab": n_shab,
        },
        "menuiseries_detail": menuis_detail,
    }


# --------------------------------------------------------------------------- #
#  Écriture du classeur .xlsx au format read_enveloppe (avp_sources)
# --------------------------------------------------------------------------- #
def write_xlsx(payload: dict, path: str) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Extraction surface enveloppe"

    ws["A1"] = (
        "BIMDATA — EXTRACTION SURFACE ENVELOPPE (calcul géométrique IfcOpenShell)"
    )
    ws["A2"] = f"Fichier : {payload.get('file')}"

    # Bloc synthèse : libellé en A, valeur numérique en B (lu par _scan_value).
    synth = [
        ("Superficie des façades", payload.get("superficie_facades_m2")),
        ("Superficie des menuiseries", payload.get("superficie_menuiseries_m2")),
        ("SHAB", payload.get("shab_m2")),
        ("ratio FAC/SHAB", payload.get("ratio_fac_shab")),
    ]
    if payload.get("seuil_3f") is not None:
        synth.append(("Seuil 3F 2026", payload.get("seuil_3f")))

    ws["A4"] = "Synthèse"
    r = 5
    for label, val in synth:
        ws.cell(r, 1, label)
        ws.cell(r, 2, val)
        r += 1

    # Table détaillée des menuiseries (ancrée sur « Composant »).
    r += 1
    headers = ["Composant", "Type", "Largeur (m)", "Hauteur (m)", "Surface (m²)"]
    for c, h in enumerate(headers, start=1):
        ws.cell(r, c, h)
    r += 1
    for d in payload.get("menuiseries_detail", []):
        ws.cell(r, 1, d.get("name"))
        ws.cell(r, 2, d.get("type"))
        ws.cell(r, 3, d.get("largeur_m"))
        ws.cell(r, 4, d.get("hauteur_m"))
        ws.cell(r, 5, d.get("surface_m2"))
        r += 1

    wb.save(path)
