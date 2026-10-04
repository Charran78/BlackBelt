"""Resolve and store Gmail credentials without writing secrets to project files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType


KEYRING_SERVICE = "BlackBelt Gmail IMAP"
KEYRING_ACCOUNT_KEY = "configured-account"


class EmailCredentialsError(RuntimeError):
    """Raised when Gmail credentials cannot be safely resolved or stored."""


@dataclass(frozen=True, slots=True)
class EmailCredentials:
    username: str
    app_password: str = field(repr=False)


def load_credentials() -> EmailCredentials:
    username = (
        os.getenv("EMAIL_USER", "").strip()
        or os.getenv("GMAIL_USER", "").strip()
    )
    app_password = (
        os.getenv("EMAIL_APP_PASSWORD", "").strip()
        or os.getenv("GMAIL_APP_PASSWORD", "").strip()
    )
    app_password = app_password.replace(" ", "")

    try:
        if not username:
            username = (_keyring_get(KEYRING_ACCOUNT_KEY) or "").strip()
        if username and not app_password:
            app_password = (_keyring_get(username) or "").replace(" ", "").strip()
    except EmailCredentialsError:
        raise

    if not _looks_like_email(username):
        raise EmailCredentialsError(
            "Falta una dirección Gmail válida. Ejecuta "
            "'blackbelt run email configure'."
        )
    if not app_password:
        raise EmailCredentialsError(
            "Falta la contraseña de aplicación de Gmail. Ejecuta "
            "'blackbelt run email configure'."
        )
    return EmailCredentials(username=username, app_password=app_password)


def store_credentials(username: str, app_password: str) -> None:
    normalized_username = username.strip()
    normalized_password = app_password.replace(" ", "").strip()
    if not _looks_like_email(normalized_username):
        raise EmailCredentialsError("Introduce una dirección de correo válida.")
    if not normalized_password:
        raise EmailCredentialsError(
            "La contraseña de aplicación no puede estar vacía."
        )
    try:
        _keyring_set(normalized_username, normalized_password)
        _keyring_set(KEYRING_ACCOUNT_KEY, normalized_username)
    except EmailCredentialsError:
        raise


def _looks_like_email(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    local, separator, domain = value.partition("@")
    return bool(separator and local and "." in domain and "@" not in domain)


def _keyring_get(username: str) -> str | None:
    module = _load_keyring()
    try:
        return module.get_password(KEYRING_SERVICE, username)
    except _keyring_error_types(module) as exc:
        raise EmailCredentialsError(
            "No se pudo leer el almacén seguro del sistema. Comprueba que "
            "el administrador de credenciales esté disponible."
        ) from exc


def _keyring_set(username: str, secret: str) -> None:
    module = _load_keyring()
    try:
        module.set_password(KEYRING_SERVICE, username, secret)
    except _keyring_error_types(module) as exc:
        raise EmailCredentialsError(
            "No se pudo escribir en el almacén seguro del sistema. Comprueba "
            "que el administrador de credenciales esté disponible."
        ) from exc


def _load_keyring() -> ModuleType:
    try:
        return import_module("keyring")
    except ImportError as exc:
        raise EmailCredentialsError(
            "Falta la dependencia keyring. Instala BlackBelt con "
            "'python -m pip install -e .'."
        ) from exc


def _keyring_error_types(module: ModuleType) -> tuple[type[Exception], ...]:
    errors = import_module("keyring.errors")
    keyring_error = getattr(errors, "KeyringError", None)
    if not isinstance(keyring_error, type) or not issubclass(
        keyring_error,
        Exception,
    ):
        raise EmailCredentialsError(
            f"El módulo {module.__name__} no expone sus errores esperados."
        )
    return (keyring_error,)
