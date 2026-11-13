"""Store provider API keys in the operating-system credential manager."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

PROVIDER_KEYRING_SERVICE = "BlackBelt AI Providers"
SUPPORTED_PROVIDERS = frozenset({"groq", "openrouter"})


class ProviderCredentialsError(RuntimeError):
    """Raised when provider credentials cannot be safely accessed."""


def store_api_key(provider: str, api_key: str) -> None:
    """Store a validated API key without exposing it in project files."""
    normalized_provider = _normalize_provider(provider)
    normalized_key = api_key.strip()
    if not normalized_key or len(normalized_key) > 512:
        raise ProviderCredentialsError("La clave API no es válida.")
    keyring = _load_keyring()
    try:
        keyring.set_password(
            PROVIDER_KEYRING_SERVICE,
            normalized_provider,
            normalized_key,
        )
    except _keyring_error_types(keyring) as exc:
        raise ProviderCredentialsError(
            "No se pudo guardar la clave en el almacén seguro del sistema."
        ) from exc


def load_api_key(provider: str) -> str:
    """Return a configured key or a safe, actionable error."""
    normalized_provider = _normalize_provider(provider)
    keyring = _load_keyring()
    try:
        api_key = keyring.get_password(
            PROVIDER_KEYRING_SERVICE,
            normalized_provider,
        )
    except _keyring_error_types(keyring) as exc:
        raise ProviderCredentialsError(
            "No se pudo leer el almacén seguro del sistema."
        ) from exc
    if not api_key:
        raise ProviderCredentialsError(
            f"Falta la clave de {normalized_provider}. Configúrala con "
            "`blackbelt run providers configure`."
        )
    return api_key


def has_api_key(provider: str) -> bool:
    """Report whether a key exists without returning the secret."""
    normalized_provider = _normalize_provider(provider)
    keyring = _load_keyring()
    try:
        return bool(
            keyring.get_password(
                PROVIDER_KEYRING_SERVICE,
                normalized_provider,
            )
        )
    except _keyring_error_types(keyring) as exc:
        raise ProviderCredentialsError(
            "No se pudo consultar el almacén seguro del sistema."
        ) from exc


def delete_api_key(provider: str) -> None:
    """Remove one provider key from the operating-system credential manager."""
    normalized_provider = _normalize_provider(provider)
    keyring = _load_keyring()
    try:
        keyring.delete_password(
            PROVIDER_KEYRING_SERVICE,
            normalized_provider,
        )
    except _keyring_error_types(keyring) as exc:
        raise ProviderCredentialsError(
            "No se pudo borrar la clave del almacén seguro del sistema."
        ) from exc


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().casefold()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProviderCredentialsError(
            "Las credenciales solo se gestionan para Groq y OpenRouter."
        )
    return normalized


def _load_keyring() -> ModuleType:
    try:
        return import_module("keyring")
    except ImportError as exc:
        raise ProviderCredentialsError(
            "Falta la dependencia keyring. Instala BlackBelt con "
            "`python -m pip install -e .`."
        ) from exc


def _keyring_error_types(module: ModuleType) -> tuple[type[Exception], ...]:
    errors = import_module("keyring.errors")
    keyring_error = getattr(errors, "KeyringError", None)
    if not isinstance(keyring_error, type) or not issubclass(
        keyring_error,
        Exception,
    ):
        raise ProviderCredentialsError(
            f"El módulo {module.__name__} no expone sus errores esperados."
        )
    return (keyring_error,)
