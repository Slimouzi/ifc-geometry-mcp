"""Parité du shim safe_paths (profil ifc de bim-sandbox). Offline, fs tmp."""

from __future__ import annotations

import bim_sandbox
import pytest

from ifc_openshell_mcp import safe_paths as sp


def _mk(p, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_output_path_is_direct_reexport():
    assert sp.safe_output_path is bim_sandbox.safe_output_path


def test_input_path_ifc_profile_relative_under_root(tmp_path, monkeypatch):
    # Profil ifc : "x.ifc" résolu sous AUDIT_INPUT_DIR (pas cwd).
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(tmp_path))
    _mk(tmp_path / "model.ifc")
    got = sp.safe_input_path("model.ifc")
    assert got == (tmp_path / "model.ifc").resolve()
    # Parité stricte avec le profil ifc du package.
    assert got == bim_sandbox.safe_input_path("model.ifc", profile="ifc")


def test_input_path_no_default_extension_whitelist(tmp_path, monkeypatch):
    # Contrairement au profil audit, ifc n'impose pas d'extension par défaut.
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(tmp_path))
    _mk(tmp_path / "data.bin")
    assert sp.safe_input_path("data.bin") == (tmp_path / "data.bin").resolve()


def test_input_path_extension_checked_when_given(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(tmp_path))
    _mk(tmp_path / "model.txt")
    with pytest.raises(ValueError):  # UnsafePathError (sous-classe de ValueError)
        sp.safe_input_path("model.txt", allowed_extensions={".ifc"})


def test_input_path_outside_root_refused(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    monkeypatch.setenv("AUDIT_INPUT_DIR", str(in_dir))
    _mk(tmp_path / "outside.ifc")
    with pytest.raises(ValueError):
        sp.safe_input_path(str(tmp_path / "outside.ifc"))


def test_output_path_flattens_and_keeps_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_OUTPUT_DIR", str(tmp_path / "out"))
    target = sp.safe_output_path("sub/dir/../r.json")
    assert target.name == "r.json"  # aplati
    _mk(target)
    with pytest.raises(FileExistsError):  # conservé (pas UnsafePathError)
        sp.safe_output_path("r.json", overwrite=False)
