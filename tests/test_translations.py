"""Cohérence des fichiers de métadonnées et de traduction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.stiebel_isg_modbus.const import DOMAIN
from custom_components.stiebel_isg_modbus.registers import CLIMATES, SENSORS

COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN
LANGUAGES = ("en", "fr")


def _load(language: str) -> dict:
    return json.loads((COMPONENT / "translations" / f"{language}.json").read_text("utf-8"))


def _paths(node: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _paths(value, path)
        else:
            paths.add(path)
    return paths


def test_manifest() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text("utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["name"] == "WattKeeper - Stiebel Eltron ISG Modbus"
    assert manifest["documentation"] == "https://github.com/crapitouille/ha-stiebeleltron-isg"
    assert manifest["issue_tracker"] == "https://github.com/crapitouille/ha-stiebeleltron-isg/issues"
    assert manifest["codeowners"] == ["@crapitouille"]
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert any(req.startswith("pymodbus") for req in manifest["requirements"])
    assert manifest["version"]


def test_hacs_json() -> None:
    hacs = json.loads((COMPONENT.parent.parent / "hacs.json").read_text("utf-8"))
    assert hacs["name"] == "WattKeeper - Stiebel Eltron ISG Modbus"


def test_readme_boutons_home_assistant() -> None:
    """Le README contient les boutons HACS et « Ajouter l'intégration »."""
    readme = (COMPONENT.parent.parent / "README.md").read_text("utf-8")
    assert (
        "my.home-assistant.io/redirect/hacs_repository/"
        "?owner=crapitouille&repository=ha-stiebeleltron-isg&category=integration"
    ) in readme
    assert f"my.home-assistant.io/redirect/config_flow_start/?domain={DOMAIN}" in readme
    assert "github.com/crapitouille/ha-stiebeleltron-isg" in readme


def test_traductions_alignees() -> None:
    en, fr = (_paths(_load(lang)) for lang in LANGUAGES)
    assert en == fr


@pytest.mark.parametrize("language", LANGUAGES)
def test_toutes_les_entites_sont_traduites(language: str) -> None:
    entity = _load(language)["entity"]
    for sensor in SENSORS:
        assert entity["sensor"][sensor.key]["name"]
    for climate in CLIMATES:
        assert entity["climate"][climate.key]["name"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_erreurs_du_config_flow_traduites(language: str) -> None:
    translations = _load(language)
    for error in ("cannot_connect", "invalid_response", "unknown"):
        assert translations["config"]["error"][error]
    assert translations["config"]["abort"]["already_configured"]
    assert translations["exceptions"]["write_failed"]["message"]
    assert translations["exceptions"]["no_previous_setpoint"]["message"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_les_erreurs_de_connexion_affichent_la_cause(language: str) -> None:
    errors = _load(language)["config"]["error"]
    assert "{error}" in errors["cannot_connect"]
    assert "{error}" in errors["invalid_response"]
