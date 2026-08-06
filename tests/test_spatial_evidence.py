"""``spatial_evidence`` — mesures géométriques, sans maquette.

Les deux approximations de largeur et le rattachement objet → espace sont les
seuls endroits où ce module *invente* une valeur qui n'est pas lue telle quelle
dans le fichier. Ils sont donc testés sur des formes dont on connaît la réponse
à la main, et non sur une maquette réelle : un test dont le corpus vient de la
même source que le code ne prouve rien.
"""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from ifc_openshell_mcp.analyzers.spatial_evidence import (
    MIN_Z_OVERLAP_M,
    _inscribed_diameter,
    _min_rect_width,
    _resolve_container,
)
from ifc_openshell_mcp.analyzers.spaces import SpaceRecord


# --------------------------------------------------------------------------- #
#  Largeurs : deux mesures, deux sens
# --------------------------------------------------------------------------- #
def test_couloir_les_deux_mesures_donnent_la_largeur():
    """Sur un rectangle, les deux approximations coïncident — c'est le seul cas."""
    couloir = Polygon([(0, 0), (10, 0), (10, 1.2), (0, 1.2)])
    assert _min_rect_width(couloir) == pytest.approx(1.2, abs=0.01)
    assert _inscribed_diameter(couloir) == pytest.approx(1.2, abs=0.02)


def test_couloir_oriente_a_45_degres_reste_a_sa_largeur():
    """Le rectangle englobant doit être ORIENTÉ. Aligné sur les axes, il rendrait
    la diagonale et un couloir de 1,20 m passerait pour large de 8 m."""
    droit = Polygon([(0, 0), (10, 0), (10, 1.2), (0, 1.2)])
    incline = Polygon(
        [
            (x * 0.7071 - y * 0.7071, x * 0.7071 + y * 0.7071)
            for x, y in droit.exterior.coords
        ]
    )
    assert _min_rect_width(incline) == pytest.approx(1.2, abs=0.01)


def test_piece_en_L_aucune_des_deux_mesures_n_est_la_largeur_de_passage():
    """Le garde-fou principal du contrat, et il vaut contre les DEUX mesures.

    L de 6×6 dont on retire un carré 4×4 : les branches font 2 m de large.

    - Le rectangle englobant orienté rend 6,00 — il enveloppe le L entier.
    - Le cercle inscrit rend 2,34, et non 2,00 : le plus grand cercle ne se loge
      pas dans une branche mais dans l'angle, où il déborde en diagonale dans
      les deux.

    Aucune des deux ne vaut donc 2,00. Le cercle inscrit dit « la pièce fait au
    moins ce diamètre QUELQUE PART », jamais « elle fait au moins ça PARTOUT ».
    Un contrôle « largeur de circulation ≥ 2,50 m » ne peut être tranché par
    aucune des deux sur une pièce non convexe : il faudrait un axe médian, qui
    n'est pas dans ce lot.
    """
    forme_en_L = Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)])
    assert _min_rect_width(forme_en_L) == pytest.approx(6.0, abs=0.01)
    assert _inscribed_diameter(forme_en_L) == pytest.approx(2.338, abs=0.02)
    assert _inscribed_diameter(forme_en_L) > 2.0  # PAS la largeur des branches


def test_sur_une_piece_convexe_le_cercle_inscrit_est_la_largeur():
    """Le cas où la mesure est exacte, et le seul : une pièce convexe.

    Carré de 4 m → 4,00, et non la diagonale de 5,66. C'est ce qui rend la
    mesure utilisable sur les pièces rectangulaires des tables de surfaces
    minimales, et inutilisable telle quelle ailleurs.
    """
    carre = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    assert _inscribed_diameter(carre) == pytest.approx(4.0, abs=0.03)


@pytest.mark.parametrize("vide", [None, Polygon()])
def test_geometrie_absente_ne_rend_pas_zero(vide):
    """``None`` (pas mesuré) et ``0.0`` (mesuré nul) ne veulent pas dire la même
    chose : un consommateur qui les confond déclare une pièce de largeur nulle."""
    assert _min_rect_width(vide) is None
    assert _inscribed_diameter(vide) is None


# --------------------------------------------------------------------------- #
#  Rattachement objet → espace
# --------------------------------------------------------------------------- #
class _FakeMeasures:
    def __init__(self, footprint=None, bbox=None, centroid=None):
        self.footprint = footprint
        self.bbox = bbox
        self.centroid = centroid


class _FakeTree:
    """STRtree factice : rend tous les index, la sélection fine est le sujet."""

    def __init__(self, n):
        self._n = n

    def query(self, _geometry):
        return range(self._n)


def _space(guid, polygon, z_min=0.0, z_max=2.5):
    return SpaceRecord(
        guid=guid,
        name=guid,
        long_name=guid,
        storey="RDC",
        zones=[],
        footprint=polygon,
        z_min=z_min,
        z_max=z_max,
        area_declared_m2=polygon.area,
        area_recalc_m2=polygon.area,
        room_type="sejour",
        raw_label=guid,
    )


_SEJOUR = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])


def test_declaration_ifc_prime_sur_toute_deduction():
    spaces = [_space("RDC-SEJOUR", _SEJOUR)]
    measures = _FakeMeasures(
        footprint=Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        bbox=(1, 2, 1, 2, 0, 1),
        centroid=(1.5, 1.5, 0.5),
    )
    result = _resolve_container(measures, "AUTRE-PIECE", spaces, _FakeTree(1))
    assert result == {"space_global_id": "AUTRE-PIECE", "method": "ifc_declared"}


def test_objet_sans_empreinte_est_rattache_par_son_centroide():
    """Le cas des menuiseries : plaque verticale, donc aucune empreinte, mais un
    centroïde. Exiger l'empreinte priverait toutes les portes de rattachement —
    c'est-à-dire précisément la classe qui porte les largeurs de passage."""
    spaces = [_space("RDC-SEJOUR", _SEJOUR)]
    porte = _FakeMeasures(
        footprint=None, bbox=(2, 3, 2, 2.1, 0, 2.1), centroid=(2.5, 2.05, 1.0)
    )
    result = _resolve_container(porte, None, spaces, _FakeTree(1))
    assert result["space_global_id"] == "RDC-SEJOUR"
    assert result["method"] == "centroid_in_footprint"
    assert result["overlap_ratio"] is None


def test_superposition_verticale_seule_ne_rattache_pas():
    """Sans contrôle d'altitude, un luminaire du 3e tomberait dans le séjour du
    rez-de-chaussée : en projection XY les deux se superposent parfaitement."""
    spaces = [_space("RDC-SEJOUR", _SEJOUR, z_min=0.0, z_max=2.5)]
    au_dessus = _FakeMeasures(
        footprint=Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        bbox=(1, 2, 1, 2, 9.0, 9.5),
        centroid=(1.5, 1.5, 9.2),
    )
    assert _resolve_container(au_dessus, None, spaces, _FakeTree(1)) is None


def test_recouvrement_vertical_juste_suffisant_rattache():
    spaces = [_space("RDC-SEJOUR", _SEJOUR, z_min=0.0, z_max=2.5)]
    limite = _FakeMeasures(
        footprint=Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        bbox=(1, 2, 1, 2, 2.5 - MIN_Z_OVERLAP_M * 2, 4.0),
        centroid=(1.5, 1.5, 3.0),
    )
    assert _resolve_container(limite, None, spaces, _FakeTree(1)) is not None


def test_recouvrement_partiel_insuffisant_ne_rattache_pas():
    """Un mur à cheval sur deux pièces n'appartient à aucune. Le rattacher à la
    première rencontrée ferait entrer un objet mitoyen dans un décompte de
    contenu de pièce."""
    spaces = [_space("RDC-SEJOUR", _SEJOUR)]
    a_cheval = _FakeMeasures(
        footprint=Polygon([(4.5, 1), (10, 1), (10, 2), (4.5, 2)]),
        bbox=(4.5, 10, 1, 2, 0, 1),
        centroid=(7.25, 1.5, 0.5),  # hors du séjour
    )
    assert _resolve_container(a_cheval, None, spaces, _FakeTree(1)) is None


def test_recouvrement_majoritaire_rattache_et_porte_son_ratio():
    spaces = [_space("RDC-SEJOUR", _SEJOUR)]
    surtout_dedans = _FakeMeasures(
        footprint=Polygon([(4, 1), (6, 1), (6, 2), (4, 2)]),  # 50 % dans le séjour
        bbox=(4, 6, 1, 2, 0, 1),
        centroid=(5.5, 1.5, 0.5),  # hors empreinte : force la voie recouvrement
    )
    result = _resolve_container(surtout_dedans, None, spaces, _FakeTree(1))
    assert result["method"] == "footprint_overlap"
    assert result["overlap_ratio"] == pytest.approx(0.5, abs=0.01)


def test_objet_sans_geometrie_du_tout_n_est_rattache_a_rien():
    spaces = [_space("RDC-SEJOUR", _SEJOUR)]
    assert _resolve_container(_FakeMeasures(), None, spaces, _FakeTree(1)) is None
