"""Index des pièces (IfcSpace) enrichi — base commune aux analyseurs.

Calcule une seule fois, par pièce : identité, étage, zones, empreinte 2D,
plage d'altitude, surface déclarée (Qto) et surface recalculée (géométrie).
Fournit aussi la normalisation du *type de pièce* (chambre, séjour, placard…)
utilisée pour les seuils réglementaires et la cohérence de nommage.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from shapely.geometry import Polygon

from .. import ifc_utils


@dataclass
class SpaceRecord:
    guid: str
    name: str | None
    long_name: str | None
    storey: str | None
    zones: list[str]
    footprint: Polygon | None
    z_min: float | None
    z_max: float | None
    area_declared_m2: float | None
    area_recalc_m2: float | None
    room_type: str  # type normalisé (ex. "chambre")
    raw_label: str  # libellé source (name ou long_name)
    ifc: object = field(repr=False, default=None)

    @property
    def area_delta_pct(self) -> float | None:
        if not self.area_declared_m2 or self.area_recalc_m2 is None:
            return None
        if self.area_declared_m2 == 0:
            return None
        return (
            (self.area_recalc_m2 - self.area_declared_m2)
            / self.area_declared_m2
            * 100.0
        )

    @property
    def best_area(self) -> float | None:
        return self.area_declared_m2 if self.area_declared_m2 else self.area_recalc_m2


# Types habitables soumis à un seuil de surface minimale (décence / RE).
HABITABLE_TYPES = {"chambre", "sejour", "salon", "studio", "piece_de_vie"}
# Types "placard" susceptibles d'être double-modélisés (volume + pièce dédiée).
CLOSET_TYPES = {"placard", "rangement", "dressing", "penderie"}

# Normalisation libellé → type de pièce.
_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"chambre|chbre|\bch\d*\b", "chambre"),
    (r"sejour|séjour|living", "sejour"),
    (r"salon", "salon"),
    (r"cuisine|\bcuis\b", "cuisine"),
    (r"salle de bain|\bsdb\b|bain", "salle_de_bain"),
    (r"salle d.?eau|\bsde\b", "salle_d_eau"),
    (r"\bwc\b|toilette", "wc"),
    (r"placard|\bpla?c\b", "placard"),
    (r"rangement|\brgt\b", "rangement"),
    (r"dressing|penderie", "dressing"),
    (r"degagement|dégagement|couloir|circulation", "degagement"),
    (r"entree|entrée|\bhall\b", "entree"),
    (r"cellier|\bcell\b", "cellier"),
    (r"\bcave\b", "cave"),
    (r"balcon|terrasse|loggia", "exterieur"),
    (r"local|\blt\b|gaine|technique", "technique"),
]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def normalize_room_type(label: str | None) -> str:
    """Déduit un type de pièce normalisé à partir d'un libellé libre."""
    if not label:
        return "inconnu"
    txt = _strip_accents(label).lower().strip()
    for pattern, rtype in _TYPE_PATTERNS:
        if re.search(pattern, txt):
            return rtype
    return "autre"


def build_spaces(model) -> list[SpaceRecord]:
    """Construit l'index enrichi de toutes les pièces de la maquette."""
    space_to_zones, _ = ifc_utils.zone_map(model)
    records: list[SpaceRecord] = []
    for sp in model.by_type("IfcSpace"):
        geom = ifc_utils.element_geometry(sp)
        long_name = ifc_utils.space_long_name(sp)
        raw_label = long_name or sp.Name or ""
        records.append(
            SpaceRecord(
                guid=sp.GlobalId,
                name=sp.Name,
                long_name=long_name,
                storey=ifc_utils.storey_name(sp),
                zones=space_to_zones.get(sp.GlobalId, []),
                footprint=geom.footprint,
                z_min=geom.z_min,
                z_max=geom.z_max,
                area_declared_m2=ifc_utils.declared_area(sp),
                area_recalc_m2=geom.recalc_area_m2,
                room_type=normalize_room_type(raw_label),
                raw_label=raw_label,
                ifc=sp,
            )
        )
    return records
