"""Shared fixtures of the validation tests."""

import dataclasses

import pytest

from anonymization_pipeline.pipeline_config.categories import Registry, registry


@pytest.fixture
def families(monkeypatch: pytest.MonkeyPatch) -> Registry:
    """Registry with known generic families: tests do not depend on ``categories.yaml``."""
    reg = dataclasses.replace(
        registry(),
        generic={
            "NOME": "PERSONA",
            "COGNOME": "PERSONA",
            "MEDICO": "PERSONA",
            "FAMILIARE": "PERSONA",
        },
    )
    monkeypatch.setattr("anonval.build.registry", lambda: reg)
    return reg
