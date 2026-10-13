"""Validated local agent profiles for the Companion."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_CLOUD_SUFFIXES = (":cloud", "-cloud")
_MAX_PROFILES = 50


class ProfileStoreError(ValueError):
    """Raised when a local agent profile cannot be read or written safely."""


class AgentProfile(BaseModel):
    """A bounded, local persona and Ollama generation configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=1_000)
    rules: list[str] = Field(min_length=1, max_length=20)
    model: str = Field(min_length=1, max_length=128)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=1_000)
    repeat_penalty: float = Field(default=1.1, ge=0.5, le=2.0)
    num_ctx: int = Field(default=4_096, ge=512, le=16_384)
    num_predict: int = Field(default=1_024, ge=64, le=8_192)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _PROFILE_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "El identificador solo admite minúsculas, números y guiones."
            )
        return value

    @field_validator("model")
    @classmethod
    def validate_local_model(cls, value: str) -> str:
        if value.casefold().endswith(_CLOUD_SUFFIXES):
            raise ValueError("Los perfiles locales no pueden fijar un modelo Cloud.")
        return value

    @field_validator("rules")
    @classmethod
    def validate_rules(cls, values: list[str]) -> list[str]:
        normalized = [rule.strip() for rule in values]
        if any(not rule or len(rule) > 500 for rule in normalized):
            raise ValueError("Cada regla debe contener entre 1 y 500 caracteres.")
        return normalized

    def system_instructions(self) -> str:
        """Render user-authored profile fields as bounded system instructions."""
        rules = "\n".join(f"- {rule}" for rule in self.rules)
        return (
            "Identidad del agente:\n"
            f"Te llamas {json.dumps(self.name, ensure_ascii=False)}. "
            f"Si te preguntan tu nombre, responde {json.dumps(self.name, ensure_ascii=False)}. "
            "No adoptes nombres mencionados en las fuentes recuperadas.\n\n"
            f"Rol:\n{self.role}\n\n"
            f"Propósito:\n{self.purpose}\n\n"
            f"Reglas del perfil:\n{rules}"
        )


class ProfileStore:
    """Persist validated profile JSON outside the Obsidian vault."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()

    def list(self) -> list[AgentProfile]:
        if not self.root.exists():
            return []
        try:
            paths = sorted(self.root.glob("*.json"), key=lambda path: path.name)
        except OSError as exc:
            raise ProfileStoreError(
                "No se pudieron listar los perfiles locales."
            ) from exc
        if len(paths) > _MAX_PROFILES:
            raise ProfileStoreError(
                f"Hay más de {_MAX_PROFILES} perfiles; revisa la carpeta local."
            )
        profiles = [self._read(path) for path in paths]
        return sorted(profiles, key=lambda profile: profile.name.casefold())

    def get(self, profile_id: str) -> AgentProfile:
        path = self._path_for(profile_id)
        if not path.is_file():
            raise ProfileStoreError(f"No existe el perfil local '{profile_id}'.")
        return self._read(path)

    def save(self, profile: AgentProfile) -> AgentProfile:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if (
                not self._path_for(profile.id).exists()
                and len(self.list()) >= _MAX_PROFILES
            ):
                raise ProfileStoreError(
                    f"No se pueden guardar más de {_MAX_PROFILES} perfiles."
                )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{profile.id}-",
                suffix=".tmp",
                dir=self.root,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="\n"
                ) as stream:
                    stream.write(
                        json.dumps(
                            profile.model_dump(mode="json"),
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary_path.replace(self._path_for(profile.id))
            except OSError:
                temporary_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise ProfileStoreError(
                f"No se pudo guardar el perfil local: {reason}"
            ) from exc
        return profile

    def delete(self, profile_id: str) -> None:
        path = self._path_for(profile_id)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise ProfileStoreError(
                f"No existe el perfil local '{profile_id}'."
            ) from exc
        except OSError as exc:
            raise ProfileStoreError("No se pudo borrar el perfil local.") from exc

    def render_modelfile(self, profile: AgentProfile) -> str:
        """Return a reviewable Modelfile without invoking `ollama create`."""
        system = profile.system_instructions().replace('"""', '\\"\\"\\"')
        return (
            f"FROM {profile.model}\n"
            f"PARAMETER temperature {profile.temperature:g}\n"
            f"PARAMETER top_p {profile.top_p:g}\n"
            f"PARAMETER top_k {profile.top_k}\n"
            f"PARAMETER repeat_penalty {profile.repeat_penalty:g}\n"
            f"PARAMETER num_ctx {profile.num_ctx}\n"
            f"PARAMETER num_predict {profile.num_predict}\n"
            f'SYSTEM """\n{system}\n"""\n'
        )

    def _path_for(self, profile_id: str) -> Path:
        if not _PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise ProfileStoreError("El identificador del perfil no es válido.")
        return self.root / f"{profile_id}.json"

    def _read(self, path: Path) -> AgentProfile:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = AgentProfile.model_validate(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ProfileStoreError(
                f"No se pudo validar el perfil local {path.name}."
            ) from exc
        if path.name != f"{profile.id}.json":
            raise ProfileStoreError(
                f"El identificador no coincide con el archivo {path.name}."
            )
        return profile
