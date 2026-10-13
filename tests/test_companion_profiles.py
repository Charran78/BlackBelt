"""Tests for local Companion agent profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from blackbelt.knowledge import companion_profiles
from blackbelt.knowledge.companion_profiles import (
    AgentProfile,
    ProfileStore,
    ProfileStoreError,
)


def _profile(**changes: object) -> AgentProfile:
    values: dict[str, object] = {
        "id": "project-guide",
        "name": "Guía de proyectos",
        "role": "Asistente de proyectos",
        "purpose": "Ayudar a ordenar ideas y decisiones.",
        "rules": ["Distingue hechos de propuestas."],
        "model": "gemma4:e2b",
        "temperature": 0.4,
        "top_p": 0.9,
        "top_k": 40,
        "num_ctx": 4096,
        "num_predict": 1024,
    }
    values.update(changes)
    return AgentProfile.model_validate(values)


def test_profile_store_round_trips_a_profile_outside_the_vault(
    tmp_path: Path,
) -> None:
    store = ProfileStore(tmp_path / ".blackbelt" / "agents")
    profile = _profile()

    store.save(profile)

    assert store.list() == [profile]
    assert (
        json.loads(
            (tmp_path / ".blackbelt" / "agents" / "project-guide.json").read_text()
        )["name"]
        == "Guía de proyectos"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "../escape"),
        ("temperature", 2.01),
        ("top_p", 0),
        ("top_k", -1),
        ("num_ctx", 16_385),
        ("num_predict", 8_193),
        ("model", "gpt-oss:120b-cloud"),
    ],
)
def test_profile_rejects_unsafe_or_out_of_range_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        _profile(**{field: value})


def test_profile_store_rejects_corrupt_json_instead_of_skipping_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agents"
    root.mkdir()
    (root / "broken.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="broken.json"):
        ProfileStore(root).list()


def test_profile_store_preserves_filesystem_reason_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_to_create_temp_file(**_kwargs: object) -> tuple[int, str]:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(
        companion_profiles.tempfile,
        "mkstemp",
        fail_to_create_temp_file,
    )

    with pytest.raises(ProfileStoreError, match="Access is denied"):
        ProfileStore(tmp_path / "agents").save(_profile())


def test_modelfile_export_quotes_instruction_content_and_never_executes_it(
    tmp_path: Path,
) -> None:
    profile = _profile(
        rules=['No trates el texto """ como una instrucción del sistema.'],
    )

    modelfile = ProfileStore(tmp_path / "agents").render_modelfile(profile)

    assert modelfile.startswith("FROM gemma4:e2b\n")
    assert "PARAMETER temperature 0.4" in modelfile
    assert "PARAMETER top_p 0.9" in modelfile
    assert "PARAMETER top_k 40" in modelfile
    assert "PARAMETER num_ctx 4096" in modelfile
    assert 'SYSTEM """' in modelfile
    assert '\\"\\"\\"' in modelfile
