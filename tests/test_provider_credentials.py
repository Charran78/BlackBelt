from __future__ import annotations

from types import ModuleType
from unittest.mock import Mock

import pytest

from blackbelt.providers import credentials


def _fake_keyring() -> ModuleType:
    module = ModuleType("keyring")
    module.get_password = Mock(return_value=None)  # type: ignore[attr-defined]
    module.set_password = Mock()  # type: ignore[attr-defined]
    module.delete_password = Mock()  # type: ignore[attr-defined]
    return module


def _patch_keyring(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> None:
    monkeypatch.setattr(credentials, "_load_keyring", lambda: module)
    monkeypatch.setattr(
        credentials,
        "_keyring_error_types",
        lambda _module: (RuntimeError,),
    )


def test_api_key_is_stored_by_provider_and_never_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_keyring = _fake_keyring()
    _patch_keyring(monkeypatch, fake_keyring)

    credentials.store_api_key("groq", " gsk-test ")

    fake_keyring.set_password.assert_called_once_with(
        credentials.PROVIDER_KEYRING_SERVICE,
        "groq",
        "gsk-test",
    )


def test_missing_api_key_has_configuration_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_keyring(monkeypatch, _fake_keyring())

    with pytest.raises(
        credentials.ProviderCredentialsError,
        match="blackbelt run providers configure",
    ):
        credentials.load_api_key("openrouter")


def test_unknown_provider_cannot_write_credentials() -> None:
    with pytest.raises(credentials.ProviderCredentialsError):
        credentials.store_api_key("custom", "secret")
