"""check_opening_correspondence — correspondance ouvertures Archi ↔ Structure.

Produit ``*_openings_check.json`` consommé par
``preliminary._openings_findings``.

Deux modes :

- **bi-maquette** (recommandé) : ``structure_model`` fourni. On apparie chaque
  réservation structure (IfcOpeningElement) à une ouverture archi
  (IfcOpeningElement / IfcDoor / IfcWindow) par proximité de centroïde
  (tolérance ``tolerance_m``). Les réservations non appariées sont signalées.
- **mono-maquette** : une seule maquette. On signale les IfcOpeningElement sans
  remplissage (IfcRelFillsElement) ni ouverture archi proche — réservations
  potentiellement non coordonnées.

Cas particuliers gérés (court-circuit) :
- ``structure_sans_reservations`` : la maquette structure ne contient aucun
  IfcOpeningElement (contrôle impossible).
- ``misaligned_models`` : emprises des deux maquettes disjointes (bases de
  coordonnées différentes).
"""

from __future__ import annotations

import numpy as np

from .. import ifc_utils


def _centroids(model, ifc_types) -> list[tuple[str, str, str, str, tuple]]:
    """Retourne [(guid, name, host_name, host_class, centroid)] pour les types."""
    out = []
    for t in ifc_types:
        for el in model.by_type(t):
            c = ifc_utils.element_centroid(el)
            if c is None:
                continue
            host_name = host_class = None
            for rel in getattr(el, "VoidsElements", []) or []:
                host = getattr(rel, "RelatingBuildingElement", None)
                if host is not None:
                    host_name = host.Name
                    host_class = host.is_a()
                    break
            out.append((el.GlobalId, el.Name, host_name, host_class, c))
    return out


def _has_filling(opening) -> bool:
    return bool(getattr(opening, "HasFillings", None))


def _bbox(points: list[tuple]) -> tuple | None:
    if not points:
        return None
    arr = np.array(points, dtype=float)
    return (arr.min(axis=0), arr.max(axis=0))


def _bbox_disjoint(a, b, margin: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    (amin, amax), (bmin, bmax) = a, b
    for k in range(3):
        if amax[k] + margin < bmin[k] or bmax[k] + margin < amin[k]:
            return True
    return False


def run(
    model,
    file_name: str,
    *,
    structure_model=None,
    structure_file_name: str | None = None,
    tolerance_m: float = 0.10,
) -> dict:
    archi_name = ifc_utils.model_name(model, file_name)

    if structure_model is not None:
        struct = structure_model
        struct_name = ifc_utils.model_name(
            struct, structure_file_name or "structure.ifc"
        )
        struct_openings = _centroids(struct, ("IfcOpeningElement",))
        archi_targets = _centroids(model, ("IfcOpeningElement", "IfcDoor", "IfcWindow"))
    else:
        struct = model
        struct_name = archi_name
        struct_openings = _centroids(model, ("IfcOpeningElement",))
        # En mono-maquette, la "cible archi" = portes/fenêtres.
        archi_targets = _centroids(model, ("IfcDoor", "IfcWindow"))

    result: dict = {
        "structure": struct_name,
        "archi": archi_name,
        "tolerance_m": tolerance_m,
        "structure_sans_reservations": False,
        "misaligned_models": False,
        "counts": {
            "n_reservations_structure": len(struct_openings),
            "n_cibles_archi": len(archi_targets),
        },
        "structure_sans_correspondance_archi": [],
    }

    if not struct_openings:
        result["structure_sans_reservations"] = True
        return result

    # Détection d'un décalage global des bases de coordonnées.
    if structure_model is not None:
        bb_struct = _bbox([o[4] for o in struct_openings])
        bb_archi = _bbox([a[4] for a in archi_targets]) if archi_targets else None
        if _bbox_disjoint(bb_struct, bb_archi):
            result["misaligned_models"] = True
            return result

    target_pts = (
        np.array([a[4] for a in archi_targets], dtype=float)
        if archi_targets
        else np.empty((0, 3))
    )

    unmatched: list[dict] = []
    for guid, name, host_name, host_class, c in struct_openings:
        # Mono-maquette : une réservation remplie (porte/fenêtre) est coordonnée.
        opening_ifc = struct.by_guid(guid)
        if structure_model is None and _has_filling(opening_ifc):
            continue
        matched = False
        if target_pts.size:
            d = np.linalg.norm(target_pts - np.array(c), axis=1)
            matched = bool((d <= tolerance_m).any())
        if not matched:
            unmatched.append(
                {
                    "guid": guid,
                    "name": name,
                    "host_name": host_name,
                    "host_class": host_class,
                }
            )

    result["structure_sans_correspondance_archi"] = unmatched
    result["counts"]["n_sans_correspondance"] = len(unmatched)
    return result
