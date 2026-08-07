"""Serveur MCP ifc-geometry d'audit géométrique IFC (moteur IfcOpenShell).

Expose 5 outils qui analysent une maquette IFC et écrivent chacun un JSON
consommé sans transformation par ``audit-bim-i3f`` via
``import_preliminary_findings`` :

- ``run_space_clash_audit``        → ``*_space_clash_findings.json``
- ``extract_space_inventory``      → ``*_space_inventory.json``
- ``compute_surface_loss``         → ``*_surface_loss.json``
- ``check_space_boundaries``       → ``*_boundaries.json``
- ``check_opening_correspondence`` → ``*_openings_check.json``

Le contrat de sortie est défini par
``audit_bim/audit/rules/preliminary.py`` côté audit-bim-i3f.
"""

__version__ = "0.6.0"
