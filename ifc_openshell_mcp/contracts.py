"""Provenance des documents JSON produits par ce serveur.

Les **schémas** et leur validation vivent dans ``bim_core.contracts`` : ce
module ne fait qu'apposer l'identité du producteur sur les payloads émis, et
offrir la validation de sortie utilisée par les outils MCP.
"""

from __future__ import annotations

from typing import Any

from bim_core.contracts import (
    parse_computed_base_quantities,
    parse_envelope_quantities,
    parse_spatial_evidence,
    utc_now_iso,
)

from . import __version__

#: Nom public du MCP émetteur (le module interne s'appelle ``ifc_openshell_mcp``).
PRODUCER = "ifc-geometry"


def contract_source(tool: str, ifc_file: str) -> dict[str, Any]:
    """Bloc ``source`` d'un payload versionné."""
    return {
        "producer": PRODUCER,
        "tool": tool,
        "version": __version__,
        "ifc_file": ifc_file,
    }


def validate_emitted_envelope(payload: dict[str, Any]) -> None:
    """Vérifie qu'un payload d'enveloppe émis respecte ``envelope_quantities/v1``.

    Le mode strict est forcé : un document produit ici doit porter son ``schema``,
    jamais dépendre de la tolérance migratoire réservée aux fichiers historiques.
    """
    parse_envelope_quantities(
        payload, strict=True, origin="sortie extract_envelope_surfaces"
    )


def validate_emitted_quantities(payload: dict[str, Any]) -> None:
    """Idem pour ``computed_base_quantities/v1``."""
    parse_computed_base_quantities(
        payload, strict=True, origin="sortie export_computed_base_quantities"
    )


def validate_emitted_spatial_evidence(payload: dict[str, Any]) -> None:
    """Idem pour ``spatial_evidence/v1``.

    Pas de paramètre ``strict`` : ce contrat n'a pas de forme legacy, un payload
    sans ``schema`` y est refusé quel que soit le mode.
    """
    parse_spatial_evidence(payload, origin="sortie extract_spatial_evidence")


__all__ = [
    "PRODUCER",
    "contract_source",
    "utc_now_iso",
    "validate_emitted_envelope",
    "validate_emitted_quantities",
    "validate_emitted_spatial_evidence",
]
