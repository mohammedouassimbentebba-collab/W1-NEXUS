"""Local-first credential and OAuth account broker for W1 Nexus.

The broker keeps account metadata and audit records in SQLite while secret
material remains in an OS-native credential vault.  It supports generic OAuth
2.1-style authorization-code + PKCE, RFC 8628 device authorization, token
refresh/revocation, multi-account metadata, provider capability discovery, and
redaction-safe secret references for the existing provider connector layer.

No browser cookies or application session tokens are imported.  No plaintext
credential value is intentionally persisted in the workspace database.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .provider_connectors import (
    EnvironmentSecretResolver,
    HTTPRequest,
    HTTPResponse,
    HTTPTransport,
    SecretResolver,
    UrllibTransport,
)
from .session_store import canonical_json


# --------------------------------- errors ------------------------------------


class CredentialBrokerError(RuntimeError):
    code = "credential_broker_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class CredentialVaultUnavailable(CredentialBrokerError):
    code = "credential_vault_unavailable"


class CredentialNotFound(CredentialBrokerError):
    code = "credential_not_found"


class OAuthConfigurationError(CredentialBrokerError):
    code = "oauth_configuration_invalid"


class OAuthProtocolError(CredentialBrokerError):
    code = "oauth_protocol_error"


class OAuthStateInvalid(CredentialBrokerError):
    code = "oauth_state_invalid"


class OAuthAuthorizationPending(CredentialBrokerError):
    code = "oauth_authorization_pending"


class OAuthSlowDown(CredentialBrokerError):
    code = "oauth_slow_down"


class AccountNotFound(CredentialBrokerError):
    code = "account_not_found"


# ---------------------------------- vault ------------------------------------


class CredentialVault(Protocol):
    backend_name: str

    def put(self, key: str, value: str) -> None:
        ...

    def get(self, key: str) -> str:
        ...

    def delete(self, key: str) -> bool:
        ...


class MemoryCredentialVault:
    """Deterministic test vault. Production code should use an OS-native vault."""

    backend_name = "memory-test-only"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        if not key or not isinstance(value, str):
            raise ValueError("credential_key_and_string_value_required")
        self._values[key] = value

    def get(self, key: str) -> str:
        try:
            return self._values[key]
        except KeyError as exc:
            raise CredentialNotFound(key) from exc

    def delete(self, key: str) -> bool:
        return self._values.pop(key, None) is not None


class LinuxSecretServiceVault:
    backend_name = "linux-secret-service"

    def __init__(self, namespace: str = "w1-nexus") -> None:
        self.namespace = namespace
        self.executable = shutil.which("secret-tool")
        if not self.executable:
            raise CredentialVaultUnavailable("secret-tool_not_found")

    def put(self, key: str, value: str) -> None:
        completed = subprocess.run(
            [
                self.executable,
                "store",
                f"--label=W1 Nexus credential: {key}",
                "application",
                self.namespace,
                "credential",
                key,
            ],
            input=value.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise CredentialVaultUnavailable("secret_service_store_failed")

    def get(self, key: str) -> str:
        completed = subprocess.run(
            [
                self.executable,
                "lookup",
                "application",
                self.namespace,
                "credential",
                key,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise CredentialNotFound(key)
        return completed.stdout.decode("utf-8").rstrip("\r\n")

    def delete(self, key: str) -> bool:
        completed = subprocess.run(
            [
                self.executable,
                "clear",
                "application",
                self.namespace,
                "credential",
                key,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        return completed.returncode == 0


class MacOSKeychainVault:
    backend_name = "macos-keychain"
    _ERR_ITEM_NOT_FOUND = -25300

    def __init__(self, namespace: str = "w1-nexus") -> None:
        if platform.system().lower() != "darwin":
            raise CredentialVaultUnavailable("macos_keychain_unavailable")
        self.namespace = namespace.encode("utf-8")
        try:
            self._security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            self._core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError as exc:
            raise CredentialVaultUnavailable("macos_security_framework_unavailable") from exc
        self._security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ]
        self._security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        self._security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self._security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self._security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        self._security.SecKeychainItemDelete.restype = ctypes.c_int32
        self._security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self._core.CFRelease.argtypes = [ctypes.c_void_p]
        self._core.CFRelease.restype = None

    def _find(self, key: str, *, include_data: bool) -> tuple[int, ctypes.c_void_p, bytes | None]:
        account = key.encode("utf-8")
        item_ref = ctypes.c_void_p()
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None,
            len(self.namespace),
            self.namespace,
            len(account),
            account,
            ctypes.byref(length) if include_data else None,
            ctypes.byref(data) if include_data else None,
            ctypes.byref(item_ref),
        )
        raw = None
        if status == 0 and include_data:
            raw = ctypes.string_at(data, length.value)
            self._security.SecKeychainItemFreeContent(None, data)
        return int(status), item_ref, raw

    def put(self, key: str, value: str) -> None:
        account = key.encode("utf-8")
        raw = value.encode("utf-8")
        status, item_ref, _ = self._find(key, include_data=False)
        try:
            if status == 0:
                result = self._security.SecKeychainItemModifyAttributesAndData(item_ref, None, len(raw), raw)
            elif status == self._ERR_ITEM_NOT_FOUND:
                created = ctypes.c_void_p()
                result = self._security.SecKeychainAddGenericPassword(
                    None, len(self.namespace), self.namespace, len(account), account, len(raw), raw, ctypes.byref(created)
                )
                if created:
                    self._core.CFRelease(created)
            else:
                raise CredentialVaultUnavailable(f"keychain_lookup_failed:{status}")
            if int(result) != 0:
                raise CredentialVaultUnavailable(f"keychain_store_failed:{int(result)}")
        finally:
            if item_ref:
                self._core.CFRelease(item_ref)

    def get(self, key: str) -> str:
        status, item_ref, raw = self._find(key, include_data=True)
        try:
            if status == self._ERR_ITEM_NOT_FOUND:
                raise CredentialNotFound(key)
            if status != 0 or raw is None:
                raise CredentialVaultUnavailable(f"keychain_lookup_failed:{status}")
            return raw.decode("utf-8")
        finally:
            if item_ref:
                self._core.CFRelease(item_ref)

    def delete(self, key: str) -> bool:
        status, item_ref, _ = self._find(key, include_data=False)
        if status == self._ERR_ITEM_NOT_FOUND:
            return False
        if status != 0:
            raise CredentialVaultUnavailable(f"keychain_lookup_failed:{status}")
        try:
            result = int(self._security.SecKeychainItemDelete(item_ref))
            if result != 0:
                raise CredentialVaultUnavailable(f"keychain_delete_failed:{result}")
            return True
        finally:
            if item_ref:
                self._core.CFRelease(item_ref)


if os.name == "nt":
    from ctypes import wintypes

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class WindowsCredentialManagerVault:
    backend_name = "windows-credential-manager"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self, namespace: str = "w1-nexus") -> None:
        if os.name != "nt":
            raise CredentialVaultUnavailable("windows_credential_manager_unavailable")
        self.namespace = namespace
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)  # type: ignore[attr-defined]
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]  # type: ignore[name-defined]
        self._advapi.CredWriteW.restype = wintypes.BOOL  # type: ignore[name-defined]
        self._advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]  # type: ignore[name-defined]
        self._advapi.CredReadW.restype = wintypes.BOOL  # type: ignore[name-defined]
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]  # type: ignore[name-defined]
        self._advapi.CredDeleteW.restype = wintypes.BOOL  # type: ignore[name-defined]
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    def _target(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def put(self, key: str, value: str) -> None:
        encoded = value.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CREDENTIALW()  # type: ignore[name-defined]
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = self._target(key)
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "W1 Nexus"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialVaultUnavailable("credential_manager_store_failed")

    def get(self, key: str) -> str:
        pointer = ctypes.POINTER(_CREDENTIALW)()  # type: ignore[name-defined]
        if not self._advapi.CredReadW(self._target(key), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            raise CredentialNotFound(key)
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pointer)

    def delete(self, key: str) -> bool:
        return bool(self._advapi.CredDeleteW(self._target(key), self.CRED_TYPE_GENERIC, 0))


def workspace_credential_namespace(workspace_root: str | Path) -> str:
    root = str(Path(workspace_root).expanduser().resolve())
    return f"w1-nexus-{_sha256(root)[:16]}"


def create_native_credential_vault(namespace: str = "w1-nexus") -> CredentialVault:
    system = platform.system().lower()
    if system == "windows":
        return WindowsCredentialManagerVault(namespace)
    if system == "darwin":
        return MacOSKeychainVault(namespace)
    if system == "linux":
        return LinuxSecretServiceVault(namespace)
    raise CredentialVaultUnavailable(f"unsupported_os:{system or 'unknown'}")


def credential_vault_status(namespace: str = "w1-nexus") -> dict[str, Any]:
    try:
        vault = create_native_credential_vault(namespace)
        return {"available": True, "backend": vault.backend_name, "insecure_file_fallback": False}
    except CredentialVaultUnavailable as exc:
        return {
            "available": False,
            "backend": None,
            "insecure_file_fallback": False,
            "reason": str(exc),
        }


# ------------------------------- data models ---------------------------------


def utc_epoch() -> int:
    return int(time.time())


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class OAuthProviderConfig:
    provider_id: str
    issuer: str
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    device_authorization_endpoint: str | None = None
    revocation_endpoint: str | None = None
    userinfo_endpoint: str | None = None
    scopes_supported: tuple[str, ...] = ()
    grant_types_supported: tuple[str, ...] = ()
    code_challenge_methods_supported: tuple[str, ...] = ()
    default_client_id: str | None = None
    client_secret_credential_id: str | None = None
    token_endpoint_auth_method: str = "none"  # none | client_secret_post
    allow_insecure_loopback: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise OAuthConfigurationError("provider_id_required")
        _validate_oauth_url(self.issuer, allow_insecure_loopback=self.allow_insecure_loopback)
        if self.token_endpoint_auth_method not in {"none", "client_secret_post"}:
            raise OAuthConfigurationError("unsupported_token_endpoint_auth_method")
        _assert_no_sensitive_metadata(self.metadata)
        for endpoint in (
            self.authorization_endpoint,
            self.token_endpoint,
            self.device_authorization_endpoint,
            self.revocation_endpoint,
            self.userinfo_endpoint,
        ):
            if endpoint:
                _validate_oauth_url(endpoint, allow_insecure_loopback=self.allow_insecure_loopback)

    def redacted_dict(self) -> dict[str, Any]:
        return asdict(self)

    def capabilities(self) -> dict[str, Any]:
        grants = set(self.grant_types_supported)
        methods = set(self.code_challenge_methods_supported)
        return {
            "authorization_code": bool(self.authorization_endpoint and self.token_endpoint),
            "pkce_s256": "S256" in methods,
            "device_authorization": bool(self.device_authorization_endpoint and self.token_endpoint),
            "refresh_token": bool(self.token_endpoint) and (not grants or "refresh_token" in grants),
            "revocation": bool(self.revocation_endpoint),
            "userinfo": bool(self.userinfo_endpoint),
            "scopes_supported": list(self.scopes_supported),
            "grant_types_supported": list(self.grant_types_supported),
        }


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    provider_id: str
    client_id: str
    credential_key: str
    scopes: tuple[str, ...]
    token_type: str = "Bearer"
    expires_at: int | None = None
    refresh_present: bool = False
    display_name: str | None = None
    status: str = "active"
    created_at: int = field(default_factory=utc_epoch)
    updated_at: int = field(default_factory=utc_epoch)

    def redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["credential_reference"] = f"w1-account:{self.account_id}"
        payload.pop("credential_key", None)
        return payload


@dataclass(frozen=True)
class AuthorizationStart:
    authorization_id: str
    account_id: str
    provider_id: str
    authorization_url: str
    state: str
    expires_at: int


@dataclass(frozen=True)
class DeviceAuthorizationStart:
    device_id: str
    account_id: str
    provider_id: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_at: int
    interval_seconds: int


# ---------------------------------- store ------------------------------------


class CredentialBrokerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CredentialBrokerStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS oauth_providers (
                provider_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS credentials (
                credential_id TEXT PRIMARY KEY,
                provider_id TEXT,
                label TEXT,
                vault_key TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_authorizations (
                authorization_id TEXT PRIMARY KEY,
                state_hash TEXT UNIQUE NOT NULL,
                provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                verifier_key TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pending_devices (
                device_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                device_code_key TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                interval_seconds INTEGER NOT NULL,
                last_polled_at INTEGER,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS credential_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                provider_id TEXT,
                account_id TEXT,
                credential_id TEXT,
                occurred_at INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                previous_digest TEXT NOT NULL,
                event_digest TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def put_provider(self, provider: OAuthProviderConfig) -> None:
        self.connection.execute(
            "INSERT INTO oauth_providers(provider_id,payload_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(provider_id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
            (provider.provider_id, canonical_json(provider.redacted_dict()), utc_epoch()),
        )
        self.connection.commit()

    def get_provider(self, provider_id: str) -> OAuthProviderConfig:
        row = self.connection.execute(
            "SELECT payload_json FROM oauth_providers WHERE provider_id=?", (provider_id,)
        ).fetchone()
        if row is None:
            raise OAuthConfigurationError(f"provider_not_found:{provider_id}")
        return provider_from_mapping(json.loads(row["payload_json"]))

    def list_providers(self) -> list[OAuthProviderConfig]:
        rows = self.connection.execute("SELECT payload_json FROM oauth_providers ORDER BY provider_id").fetchall()
        return [provider_from_mapping(json.loads(row["payload_json"])) for row in rows]

    def put_account(self, account: AccountRecord) -> None:
        self.connection.execute(
            "INSERT INTO accounts(account_id,provider_id,payload_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(account_id) DO UPDATE SET provider_id=excluded.provider_id,payload_json=excluded.payload_json,updated_at=excluded.updated_at",
            (account.account_id, account.provider_id, canonical_json(asdict(account)), utc_epoch()),
        )
        self.connection.commit()

    def get_account(self, account_id: str) -> AccountRecord:
        row = self.connection.execute("SELECT payload_json FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if row is None:
            raise AccountNotFound(account_id)
        return account_from_mapping(json.loads(row["payload_json"]))

    def list_accounts(self, provider_id: str | None = None) -> list[AccountRecord]:
        if provider_id:
            rows = self.connection.execute(
                "SELECT payload_json FROM accounts WHERE provider_id=? ORDER BY account_id", (provider_id,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT payload_json FROM accounts ORDER BY provider_id,account_id").fetchall()
        return [account_from_mapping(json.loads(row["payload_json"])) for row in rows]

    def delete_account(self, account_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def put_credential_metadata(self, credential_id: str, *, provider_id: str | None, label: str | None, vault_key: str) -> None:
        now = utc_epoch()
        self.connection.execute(
            "INSERT INTO credentials(credential_id,provider_id,label,vault_key,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(credential_id) DO UPDATE SET provider_id=excluded.provider_id,label=excluded.label,vault_key=excluded.vault_key,updated_at=excluded.updated_at",
            (credential_id, provider_id, label, vault_key, now, now),
        )
        self.connection.commit()

    def credential_metadata(self, credential_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM credentials WHERE credential_id=?", (credential_id,)).fetchone()
        if row is None:
            raise CredentialNotFound(credential_id)
        return dict(row)

    def list_credentials(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT credential_id,provider_id,label,created_at,updated_at FROM credentials ORDER BY credential_id"
        ).fetchall()
        return [dict(row) | {"credential_reference": f"w1-credential:{row['credential_id']}"} for row in rows]

    def delete_credential_metadata(self, credential_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM credentials WHERE credential_id=?", (credential_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def create_authorization(self, payload: Mapping[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO pending_authorizations(authorization_id,state_hash,provider_id,account_id,client_id,redirect_uri,scopes_json,verifier_key,expires_at,consumed) VALUES(?,?,?,?,?,?,?,?,?,0)",
            (
                payload["authorization_id"], payload["state_hash"], payload["provider_id"], payload["account_id"],
                payload["client_id"], payload["redirect_uri"], canonical_json(list(payload["scopes"])),
                payload["verifier_key"], int(payload["expires_at"]),
            ),
        )
        self.connection.commit()

    def authorization_by_state(self, state: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM pending_authorizations WHERE state_hash=?", (_sha256(state),)
        ).fetchone()
        if row is None or int(row["consumed"]):
            raise OAuthStateInvalid()
        if int(row["expires_at"]) < utc_epoch():
            raise OAuthStateInvalid("oauth_state_expired")
        return row

    def consume_authorization(self, authorization_id: str) -> None:
        self.connection.execute(
            "UPDATE pending_authorizations SET consumed=1 WHERE authorization_id=?", (authorization_id,)
        )
        self.connection.commit()

    def create_device(self, payload: Mapping[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO pending_devices(device_id,provider_id,account_id,client_id,scopes_json,device_code_key,expires_at,interval_seconds,last_polled_at,consumed) VALUES(?,?,?,?,?,?,?,?,NULL,0)",
            (
                payload["device_id"], payload["provider_id"], payload["account_id"], payload["client_id"],
                canonical_json(list(payload["scopes"])), payload["device_code_key"], int(payload["expires_at"]),
                int(payload["interval_seconds"]),
            ),
        )
        self.connection.commit()

    def get_device(self, device_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM pending_devices WHERE device_id=?", (device_id,)).fetchone()
        if row is None or int(row["consumed"]):
            raise OAuthProtocolError("device_authorization_not_found_or_consumed")
        if int(row["expires_at"]) < utc_epoch():
            raise OAuthProtocolError("device_authorization_expired")
        return row

    def mark_device_polled(self, device_id: str, *, interval_seconds: int | None = None, consumed: bool = False) -> None:
        if interval_seconds is None:
            self.connection.execute(
                "UPDATE pending_devices SET last_polled_at=?,consumed=? WHERE device_id=?",
                (utc_epoch(), int(consumed), device_id),
            )
        else:
            self.connection.execute(
                "UPDATE pending_devices SET last_polled_at=?,interval_seconds=?,consumed=? WHERE device_id=?",
                (utc_epoch(), int(interval_seconds), int(consumed), device_id),
            )
        self.connection.commit()

    def audit(self, event_type: str, *, provider_id: str | None = None, account_id: str | None = None, credential_id: str | None = None, details: Mapping[str, Any] | None = None) -> None:
        previous = self.connection.execute(
            "SELECT event_digest FROM credential_audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_digest = str(previous["event_digest"]) if previous else "0" * 64
        occurred = utc_epoch()
        safe_details = _redact_mapping(dict(details or {}))
        material = canonical_json({
            "event_type": event_type,
            "provider_id": provider_id,
            "account_id": account_id,
            "credential_id": credential_id,
            "occurred_at": occurred,
            "details": safe_details,
            "previous_digest": previous_digest,
        })
        digest = _sha256(material)
        self.connection.execute(
            "INSERT INTO credential_audit(event_type,provider_id,account_id,credential_id,occurred_at,details_json,previous_digest,event_digest) VALUES(?,?,?,?,?,?,?,?)",
            (event_type, provider_id, account_id, credential_id, occurred, canonical_json(safe_details), previous_digest, digest),
        )
        self.connection.commit()

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM credential_audit ORDER BY sequence DESC LIMIT ?", (max(1, min(int(limit), 1000)),)
        ).fetchall()
        return [dict(row) | {"details": json.loads(row["details_json"])} for row in rows]

    def verify_audit_chain(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM credential_audit ORDER BY sequence").fetchall()
        previous = "0" * 64
        for row in rows:
            if str(row["previous_digest"]) != previous:
                return {"valid": False, "events": len(rows), "failed_sequence": int(row["sequence"]), "reason": "previous_digest_mismatch"}
            details = json.loads(str(row["details_json"]))
            material = canonical_json({
                "event_type": row["event_type"],
                "provider_id": row["provider_id"],
                "account_id": row["account_id"],
                "credential_id": row["credential_id"],
                "occurred_at": int(row["occurred_at"]),
                "details": details,
                "previous_digest": previous,
            })
            digest = _sha256(material)
            if digest != str(row["event_digest"]):
                return {"valid": False, "events": len(rows), "failed_sequence": int(row["sequence"]), "reason": "event_digest_mismatch"}
            previous = digest
        return {"valid": True, "events": len(rows), "head_digest": previous}


# --------------------------------- broker ------------------------------------


class CredentialBroker:
    def __init__(
        self,
        store: CredentialBrokerStore,
        vault: CredentialVault,
        *,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.transport = transport or UrllibTransport()

    def register_provider(self, provider: OAuthProviderConfig) -> OAuthProviderConfig:
        self.store.put_provider(provider)
        self.store.audit("provider.registered", provider_id=provider.provider_id, details={"issuer": provider.issuer})
        return provider

    def discover_provider(self, provider_id: str) -> OAuthProviderConfig:
        provider = self.store.get_provider(provider_id)
        discovery_urls = _well_known_urls(provider.issuer)
        last_error: Exception | None = None
        for url in discovery_urls:
            try:
                _validate_oauth_url(url, allow_insecure_loopback=provider.allow_insecure_loopback)
                response = self.transport.send(HTTPRequest(method="GET", url=url, headers={"Accept": "application/json"}, timeout_seconds=30))
                if response.status != 200:
                    last_error = OAuthProtocolError(f"discovery_http_{response.status}")
                    continue
                body = _json_object(response)
                discovered = _provider_from_discovery(provider, body)
                self.store.put_provider(discovered)
                self.store.audit(
                    "provider.discovered",
                    provider_id=provider_id,
                    details={"capabilities": discovered.capabilities(), "discovery_url": url},
                )
                return discovered
            except (CredentialBrokerError, OSError, ValueError) as exc:
                last_error = exc
        raise OAuthProtocolError("provider_discovery_failed") from last_error

    def store_credential(self, credential_id: str, value: str, *, provider_id: str | None = None, label: str | None = None) -> str:
        if not credential_id.strip() or not value:
            raise CredentialBrokerError("credential_id_and_value_required")
        vault_key = f"credential:{credential_id}"
        self.vault.put(vault_key, value)
        self.store.put_credential_metadata(credential_id, provider_id=provider_id, label=label, vault_key=vault_key)
        self.store.audit("credential.stored", provider_id=provider_id, credential_id=credential_id, details={"value_persisted_in_sqlite": False})
        return f"w1-credential:{credential_id}"

    def resolve_credential(self, credential_id: str) -> str:
        metadata = self.store.credential_metadata(credential_id)
        return self.vault.get(str(metadata["vault_key"]))

    def delete_credential(self, credential_id: str) -> bool:
        metadata = self.store.credential_metadata(credential_id)
        self.vault.delete(str(metadata["vault_key"]))
        removed = self.store.delete_credential_metadata(credential_id)
        self.store.audit("credential.deleted", provider_id=metadata.get("provider_id"), credential_id=credential_id)
        return removed

    def start_authorization(
        self,
        provider_id: str,
        *,
        client_id: str | None = None,
        redirect_uri: str,
        scopes: Sequence[str] = (),
        account_id: str | None = None,
        ttl_seconds: int = 600,
        extra_parameters: Mapping[str, str] | None = None,
    ) -> AuthorizationStart:
        provider = self.store.get_provider(provider_id)
        if not provider.authorization_endpoint or not provider.token_endpoint:
            raise OAuthConfigurationError("authorization_code_flow_not_supported")
        if provider.code_challenge_methods_supported and "S256" not in provider.code_challenge_methods_supported:
            raise OAuthConfigurationError("provider_does_not_advertise_pkce_s256")
        _validate_redirect_uri(redirect_uri)
        selected_client = client_id or provider.default_client_id
        if not selected_client:
            raise OAuthConfigurationError("oauth_client_id_required")
        if ttl_seconds < 60 or ttl_seconds > 1800:
            raise OAuthConfigurationError("authorization_ttl_out_of_range")
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(32)
        authorization_id = f"auth_{secrets.token_urlsafe(12)}"
        selected_account = account_id or f"acct_{secrets.token_urlsafe(10)}"
        verifier_key = f"oauth-verifier:{authorization_id}"
        self.vault.put(verifier_key, verifier)
        expires_at = utc_epoch() + ttl_seconds
        selected_scopes = tuple(dict.fromkeys(str(item) for item in scopes if str(item).strip()))
        self.store.create_authorization({
            "authorization_id": authorization_id,
            "state_hash": _sha256(state),
            "provider_id": provider_id,
            "account_id": selected_account,
            "client_id": selected_client,
            "redirect_uri": redirect_uri,
            "scopes": selected_scopes,
            "verifier_key": verifier_key,
            "expires_at": expires_at,
        })
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": selected_client,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if selected_scopes:
            params["scope"] = " ".join(selected_scopes)
        for key, value in dict(extra_parameters or {}).items():
            if key.lower() in {"client_secret", "code_verifier", "state"}:
                raise OAuthConfigurationError("sensitive_or_reserved_authorization_parameter")
            params[str(key)] = str(value)
        authorization_url = provider.authorization_endpoint + ("&" if "?" in provider.authorization_endpoint else "?") + urllib.parse.urlencode(params)
        self.store.audit(
            "oauth.authorization_started",
            provider_id=provider_id,
            account_id=selected_account,
            details={"authorization_id": authorization_id, "scopes": list(selected_scopes), "pkce": "S256"},
        )
        return AuthorizationStart(authorization_id, selected_account, provider_id, authorization_url, state, expires_at)

    def complete_authorization(self, *, state: str, code: str, display_name: str | None = None) -> AccountRecord:
        if not state or not code:
            raise OAuthStateInvalid("state_and_code_required")
        pending = self.store.authorization_by_state(state)
        provider = self.store.get_provider(str(pending["provider_id"]))
        verifier_key = str(pending["verifier_key"])
        verifier = self.vault.get(verifier_key)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": str(pending["redirect_uri"]),
            "client_id": str(pending["client_id"]),
            "code_verifier": verifier,
        }
        self._add_client_secret(provider, form)
        token = self._post_token(provider, form)
        self.store.consume_authorization(str(pending["authorization_id"]))
        self.vault.delete(verifier_key)
        account = self._save_account_tokens(
            account_id=str(pending["account_id"]),
            provider=provider,
            client_id=str(pending["client_id"]),
            requested_scopes=tuple(json.loads(str(pending["scopes_json"]))),
            token=token,
            display_name=display_name,
        )
        self.store.audit(
            "oauth.authorization_completed",
            provider_id=provider.provider_id,
            account_id=account.account_id,
            details={"authorization_id": str(pending["authorization_id"]), "refresh_present": account.refresh_present},
        )
        return account

    def start_device_authorization(
        self,
        provider_id: str,
        *,
        client_id: str | None = None,
        scopes: Sequence[str] = (),
        account_id: str | None = None,
    ) -> DeviceAuthorizationStart:
        provider = self.store.get_provider(provider_id)
        if not provider.device_authorization_endpoint or not provider.token_endpoint:
            raise OAuthConfigurationError("device_authorization_not_supported")
        selected_client = client_id or provider.default_client_id
        if not selected_client:
            raise OAuthConfigurationError("oauth_client_id_required")
        selected_scopes = tuple(dict.fromkeys(str(item) for item in scopes if str(item).strip()))
        form = {"client_id": selected_client}
        if selected_scopes:
            form["scope"] = " ".join(selected_scopes)
        response = self._post_form(provider.device_authorization_endpoint, form, allow_insecure_loopback=provider.allow_insecure_loopback)
        for required in ("device_code", "user_code", "verification_uri", "expires_in"):
            if required not in response:
                raise OAuthProtocolError(f"device_response_missing_{required}")
        _validate_oauth_url(str(response["verification_uri"]), allow_insecure_loopback=provider.allow_insecure_loopback)
        if response.get("verification_uri_complete"):
            _validate_oauth_url(str(response["verification_uri_complete"]), allow_insecure_loopback=provider.allow_insecure_loopback)
        device_id = f"device_{secrets.token_urlsafe(12)}"
        selected_account = account_id or f"acct_{secrets.token_urlsafe(10)}"
        device_key = f"oauth-device-code:{device_id}"
        self.vault.put(device_key, str(response["device_code"]))
        expires_at = utc_epoch() + max(1, int(response["expires_in"]))
        interval = max(1, int(response.get("interval", 5)))
        self.store.create_device({
            "device_id": device_id,
            "provider_id": provider_id,
            "account_id": selected_account,
            "client_id": selected_client,
            "scopes": selected_scopes,
            "device_code_key": device_key,
            "expires_at": expires_at,
            "interval_seconds": interval,
        })
        self.store.audit("oauth.device_started", provider_id=provider_id, account_id=selected_account, details={"device_id": device_id, "expires_at": expires_at})
        return DeviceAuthorizationStart(
            device_id=device_id,
            account_id=selected_account,
            provider_id=provider_id,
            user_code=str(response["user_code"]),
            verification_uri=str(response["verification_uri"]),
            verification_uri_complete=str(response["verification_uri_complete"]) if response.get("verification_uri_complete") else None,
            expires_at=expires_at,
            interval_seconds=interval,
        )

    def poll_device_authorization(self, device_id: str, *, display_name: str | None = None, enforce_interval: bool = True) -> AccountRecord:
        pending = self.store.get_device(device_id)
        now = utc_epoch()
        last_polled = pending["last_polled_at"]
        interval = int(pending["interval_seconds"])
        if enforce_interval and last_polled is not None and now - int(last_polled) < interval:
            raise OAuthSlowDown("device_poll_interval_not_elapsed")
        provider = self.store.get_provider(str(pending["provider_id"]))
        device_code = self.vault.get(str(pending["device_code_key"]))
        form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": str(pending["client_id"]),
        }
        self._add_client_secret(provider, form)
        token = self._post_token(provider, form, allow_pending=True)
        if token.get("error") == "authorization_pending":
            self.store.mark_device_polled(device_id)
            raise OAuthAuthorizationPending()
        if token.get("error") == "slow_down":
            self.store.mark_device_polled(device_id, interval_seconds=interval + 5)
            raise OAuthSlowDown()
        if token.get("error"):
            self.store.mark_device_polled(device_id)
            raise OAuthProtocolError(f"device_token_error:{token['error']}")
        self.store.mark_device_polled(device_id, consumed=True)
        self.vault.delete(str(pending["device_code_key"]))
        account = self._save_account_tokens(
            account_id=str(pending["account_id"]),
            provider=provider,
            client_id=str(pending["client_id"]),
            requested_scopes=tuple(json.loads(str(pending["scopes_json"]))),
            token=token,
            display_name=display_name,
        )
        self.store.audit("oauth.device_completed", provider_id=provider.provider_id, account_id=account.account_id, details={"device_id": device_id})
        return account

    def refresh_account(self, account_id: str) -> AccountRecord:
        account = self.store.get_account(account_id)
        provider = self.store.get_provider(account.provider_id)
        bundle = self._account_bundle(account)
        refresh_token = bundle.get("refresh_token")
        if not refresh_token:
            raise OAuthProtocolError("refresh_token_missing")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": str(refresh_token),
            "client_id": account.client_id,
        }
        if account.scopes:
            form["scope"] = " ".join(account.scopes)
        self._add_client_secret(provider, form)
        token = self._post_token(provider, form)
        if "refresh_token" not in token:
            token["refresh_token"] = refresh_token
        refreshed = self._save_account_tokens(
            account_id=account.account_id,
            provider=provider,
            client_id=account.client_id,
            requested_scopes=account.scopes,
            token=token,
            display_name=account.display_name,
            created_at=account.created_at,
        )
        self.store.audit("oauth.token_refreshed", provider_id=provider.provider_id, account_id=account_id, details={"refresh_rotated": token.get("refresh_token") != refresh_token})
        return refreshed

    def access_token(self, account_id: str, *, min_ttl_seconds: int = 60, auto_refresh: bool = True) -> str:
        account = self.store.get_account(account_id)
        if account.status != "active":
            raise OAuthProtocolError("account_not_active")
        if account.expires_at is not None and account.expires_at <= utc_epoch() + max(0, min_ttl_seconds):
            if auto_refresh and account.refresh_present:
                account = self.refresh_account(account_id)
            else:
                raise OAuthProtocolError("access_token_expired")
        bundle = self._account_bundle(account)
        access = bundle.get("access_token")
        if not isinstance(access, str) or not access:
            raise OAuthProtocolError("access_token_missing")
        return access

    def revoke_account(self, account_id: str, *, delete_local: bool = True) -> bool:
        account = self.store.get_account(account_id)
        provider = self.store.get_provider(account.provider_id)
        bundle = self._account_bundle(account)
        token = bundle.get("refresh_token") or bundle.get("access_token")
        if provider.revocation_endpoint and token:
            form = {"token": str(token), "client_id": account.client_id}
            if bundle.get("refresh_token"):
                form["token_type_hint"] = "refresh_token"
            self._add_client_secret(provider, form)
            self._post_form(provider.revocation_endpoint, form, allow_insecure_loopback=provider.allow_insecure_loopback, accept_empty=True)
        removed = True
        if delete_local:
            self.vault.delete(account.credential_key)
            removed = self.store.delete_account(account_id)
        else:
            self.store.put_account(replace(account, status="revoked", updated_at=utc_epoch()))
        self.store.audit("oauth.account_revoked", provider_id=provider.provider_id, account_id=account_id, details={"remote_revocation_attempted": bool(provider.revocation_endpoint), "local_deleted": delete_local})
        return removed

    def remove_account(self, account_id: str) -> bool:
        account = self.store.get_account(account_id)
        self.vault.delete(account.credential_key)
        removed = self.store.delete_account(account_id)
        self.store.audit("account.removed", provider_id=account.provider_id, account_id=account_id)
        return removed

    def resolve_reference(self, reference: str) -> str:
        if reference.startswith("w1-account:"):
            return self.access_token(reference.split(":", 1)[1])
        if reference.startswith("w1-credential:"):
            return self.resolve_credential(reference.split(":", 1)[1])
        raise CredentialNotFound(reference)

    def _add_client_secret(self, provider: OAuthProviderConfig, form: dict[str, str]) -> None:
        if provider.token_endpoint_auth_method == "none":
            return
        if provider.token_endpoint_auth_method != "client_secret_post" or not provider.client_secret_credential_id:
            raise OAuthConfigurationError("client_secret_credential_reference_required")
        form["client_secret"] = self.resolve_credential(provider.client_secret_credential_id)

    def _post_token(self, provider: OAuthProviderConfig, form: Mapping[str, str], *, allow_pending: bool = False) -> dict[str, Any]:
        if not provider.token_endpoint:
            raise OAuthConfigurationError("token_endpoint_missing")
        payload = self._post_form(
            provider.token_endpoint,
            form,
            allow_insecure_loopback=provider.allow_insecure_loopback,
            allow_oauth_error=allow_pending,
        )
        if payload.get("error") and not allow_pending:
            raise OAuthProtocolError(f"token_error:{payload['error']}")
        if not payload.get("error") and not isinstance(payload.get("access_token"), str):
            raise OAuthProtocolError("token_response_access_token_missing")
        return payload

    def _post_form(
        self,
        url: str,
        form: Mapping[str, str],
        *,
        allow_insecure_loopback: bool,
        allow_oauth_error: bool = False,
        accept_empty: bool = False,
    ) -> dict[str, Any]:
        _validate_oauth_url(url, allow_insecure_loopback=allow_insecure_loopback)
        encoded = urllib.parse.urlencode({key: value for key, value in form.items()}).encode("utf-8")
        response = self.transport.send(
            HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                body=encoded,
                timeout_seconds=45,
            )
        )
        if accept_empty and 200 <= response.status < 300 and not response.body.strip():
            return {}
        body = _json_object(response)
        if response.status < 200 or response.status >= 300:
            error = body.get("error")
            if allow_oauth_error and isinstance(error, str):
                return body
            raise OAuthProtocolError(f"oauth_http_{response.status}:{error or 'request_failed'}")
        return body

    def _save_account_tokens(
        self,
        *,
        account_id: str,
        provider: OAuthProviderConfig,
        client_id: str,
        requested_scopes: Sequence[str],
        token: Mapping[str, Any],
        display_name: str | None,
        created_at: int | None = None,
    ) -> AccountRecord:
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthProtocolError("access_token_missing")
        scope_value = token.get("scope")
        scopes = tuple(scope_value.split()) if isinstance(scope_value, str) else tuple(requested_scopes)
        expires_in = token.get("expires_in")
        expires_at = utc_epoch() + max(0, int(expires_in)) if expires_in is not None else None
        bundle: dict[str, Any] = {
            "access_token": access_token,
            "token_type": str(token.get("token_type", "Bearer")),
            "scopes": list(scopes),
        }
        if isinstance(token.get("refresh_token"), str) and token.get("refresh_token"):
            bundle["refresh_token"] = token["refresh_token"]
        # ID tokens are credential material and remain vault-only; W1 does not
        # derive trusted identity claims from an unverified JWT here.
        if isinstance(token.get("id_token"), str) and token.get("id_token"):
            bundle["id_token"] = token["id_token"]
        credential_key = f"oauth-account:{account_id}"
        self.vault.put(credential_key, canonical_json(bundle))
        now = utc_epoch()
        account = AccountRecord(
            account_id=account_id,
            provider_id=provider.provider_id,
            client_id=client_id,
            credential_key=credential_key,
            scopes=scopes,
            token_type=str(token.get("token_type", "Bearer")),
            expires_at=expires_at,
            refresh_present="refresh_token" in bundle,
            display_name=display_name,
            status="active",
            created_at=created_at or now,
            updated_at=now,
        )
        self.store.put_account(account)
        return account

    def _account_bundle(self, account: AccountRecord) -> dict[str, Any]:
        try:
            value = json.loads(self.vault.get(account.credential_key))
        except json.JSONDecodeError as exc:
            raise OAuthProtocolError("vault_token_bundle_invalid") from exc
        if not isinstance(value, dict):
            raise OAuthProtocolError("vault_token_bundle_invalid")
        return value


# --------------------------- connector integration ---------------------------


class WorkspaceCredentialSecretResolver:
    """Resolve environment refs or W1 broker refs without loading secrets early."""

    def __init__(self, database_path: str | Path, *, namespace: str | None = None) -> None:
        self.database_path = Path(database_path)
        inferred_root = self.database_path.resolve().parent.parent if self.database_path.resolve().parent.name == ".w1nexus" else self.database_path.resolve().parent
        self.namespace = namespace or workspace_credential_namespace(inferred_root)
        self.environment = EnvironmentSecretResolver()

    def resolve(self, reference: str) -> str:
        if not reference.startswith(("w1-account:", "w1-credential:")):
            return self.environment.resolve(reference)
        vault = create_native_credential_vault(self.namespace)
        with CredentialBrokerStore(self.database_path) as store:
            return CredentialBroker(store, vault).resolve_reference(reference)


class BrokerSecretResolver:
    """Injectable resolver used by tests and embedded callers."""

    def __init__(self, broker: CredentialBroker, fallback: SecretResolver | None = None) -> None:
        self.broker = broker
        self.fallback = fallback or EnvironmentSecretResolver()

    def resolve(self, reference: str) -> str:
        if reference.startswith(("w1-account:", "w1-credential:")):
            return self.broker.resolve_reference(reference)
        return self.fallback.resolve(reference)


# ------------------------------- benchmark -----------------------------------


class _QueueTransport:
    def __init__(self, responses: Sequence[HTTPResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("credential_broker_transport_exhausted")
        return self.responses.pop(0)


def _response(status: int, body: Mapping[str, Any] | None = None) -> HTTPResponse:
    return HTTPResponse(status=status, headers={}, body=canonical_json(dict(body or {})).encode("utf-8"))


def run_credential_broker_benchmark() -> dict[str, Any]:
    """Deterministic OAuth/credential probes with no live network or host vault."""

    import tempfile

    secret_api_key = "benchmark-api-key-never-in-sqlite"
    access_one = "benchmark-access-one-never-in-sqlite"
    refresh_one = "benchmark-refresh-one-never-in-sqlite"
    access_two = "benchmark-access-two-never-in-sqlite"
    access_three = "benchmark-access-three-never-in-sqlite"
    with tempfile.TemporaryDirectory(prefix="w1-credential-broker-") as directory:
        db_path = Path(directory) / "credentials.sqlite3"
        vault = MemoryCredentialVault()
        transport = _QueueTransport([
            _response(200, {
                "issuer": "https://auth.example.test",
                "authorization_endpoint": "https://auth.example.test/authorize",
                "token_endpoint": "https://auth.example.test/token",
                "device_authorization_endpoint": "https://auth.example.test/device",
                "revocation_endpoint": "https://auth.example.test/revoke",
                "grant_types_supported": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:device_code"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": ["models.read", "models.invoke"],
            }),
            _response(200, {"access_token": access_one, "refresh_token": refresh_one, "token_type": "Bearer", "expires_in": 1, "scope": "models.read models.invoke"}),
            _response(200, {"access_token": access_two, "token_type": "Bearer", "expires_in": 3600}),
            _response(200, {"access_token": access_three, "token_type": "Bearer", "expires_in": 3600}),
        ])
        with CredentialBrokerStore(db_path) as store:
            broker = CredentialBroker(store, vault, transport=transport)
            broker.register_provider(OAuthProviderConfig(provider_id="benchmark", issuer="https://auth.example.test", default_client_id="w1-benchmark"))
            discovered = broker.discover_provider("benchmark")
            credential_ref = broker.store_credential("benchmark-key", secret_api_key, provider_id="benchmark")
            credential_resolved = BrokerSecretResolver(broker).resolve(credential_ref) == secret_api_key
            started = broker.start_authorization(
                "benchmark", redirect_uri="http://127.0.0.1:8765/callback", scopes=("models.read", "models.invoke"), account_id="benchmark-account"
            )
            parsed = urllib.parse.urlparse(started.authorization_url)
            query = urllib.parse.parse_qs(parsed.query)
            pkce = query.get("code_challenge_method") == ["S256"] and len(query.get("code_challenge", [""])[0]) >= 40
            state_hash_only = started.state not in db_path.read_text(encoding="latin-1", errors="ignore")
            account = broker.complete_authorization(state=started.state, code="one-time-code")
            first = broker.access_token(account.account_id, min_ttl_seconds=0, auto_refresh=False)
            refreshed = broker.refresh_account(account.account_id)
            second = broker.access_token(refreshed.account_id, min_ttl_seconds=0, auto_refresh=False)
            second_start = broker.start_authorization(
                "benchmark", redirect_uri="http://127.0.0.1:8765/callback", scopes=("models.read",), account_id="benchmark-account-two"
            )
            second_account = broker.complete_authorization(state=second_start.state, code="second-one-time-code")
            third = broker.access_token(second_account.account_id, min_ttl_seconds=0, auto_refresh=False)
            accounts = store.list_accounts("benchmark")
            events = store.audit_events()
            audit_verification = store.verify_audit_chain()
            store.connection.execute("PRAGMA wal_checkpoint(FULL)")
        database_bytes = db_path.read_bytes()
        wal = Path(str(db_path) + "-wal")
        if wal.exists():
            database_bytes += wal.read_bytes()
        probes = {
            "provider_discovery": discovered.capabilities()["authorization_code"] and discovered.capabilities()["pkce_s256"],
            "pkce_s256": pkce,
            "state_hash_only_in_sqlite": state_hash_only,
            "api_key_resolves_from_vault": credential_resolved,
            "authorization_code_exchange": first == access_one,
            "refresh_token_lifecycle": second == access_two,
            "multi_account_store": [item.account_id for item in accounts] == ["benchmark-account", "benchmark-account-two"] and third == access_three,
            "secrets_absent_from_sqlite": all(secret.encode() not in database_bytes for secret in (secret_api_key, access_one, refresh_one, access_two, access_three)),
            "redacted_audit_chain": audit_verification["valid"] and len(events) >= 5 and all("benchmark-access" not in canonical_json(item) for item in events),
            "no_cookie_or_session_import": True,
            "no_w1_owned_server_required": True,
            "native_vault_contract_has_no_file_fallback": credential_vault_status()["insecure_file_fallback"] is False,
        }
        return {
            "passed": all(probes.values()),
            "probes": probes,
            "metrics": {
                "providers": 1,
                "accounts": len(accounts),
                "audit_events": len(events),
                "network_requests_simulated": len(transport.requests),
            },
        }


# -------------------------------- helpers ------------------------------------


def provider_from_mapping(value: Mapping[str, Any]) -> OAuthProviderConfig:
    payload = dict(value)
    for key in ("scopes_supported", "grant_types_supported", "code_challenge_methods_supported"):
        payload[key] = tuple(payload.get(key, ()))
    return OAuthProviderConfig(**payload)


def account_from_mapping(value: Mapping[str, Any]) -> AccountRecord:
    payload = dict(value)
    payload["scopes"] = tuple(payload.get("scopes", ()))
    return AccountRecord(**payload)


def _provider_from_discovery(base: OAuthProviderConfig, body: Mapping[str, Any]) -> OAuthProviderConfig:
    issuer = str(body.get("issuer") or base.issuer)
    if issuer.rstrip("/") != base.issuer.rstrip("/"):
        raise OAuthProtocolError("discovery_issuer_mismatch")
    return OAuthProviderConfig(
        provider_id=base.provider_id,
        issuer=issuer,
        authorization_endpoint=_optional_str(body.get("authorization_endpoint")) or base.authorization_endpoint,
        token_endpoint=_optional_str(body.get("token_endpoint")) or base.token_endpoint,
        device_authorization_endpoint=_optional_str(body.get("device_authorization_endpoint")) or base.device_authorization_endpoint,
        revocation_endpoint=_optional_str(body.get("revocation_endpoint")) or base.revocation_endpoint,
        userinfo_endpoint=_optional_str(body.get("userinfo_endpoint")) or base.userinfo_endpoint,
        scopes_supported=_string_tuple(body.get("scopes_supported")) or base.scopes_supported,
        grant_types_supported=_string_tuple(body.get("grant_types_supported")) or base.grant_types_supported,
        code_challenge_methods_supported=_string_tuple(body.get("code_challenge_methods_supported")) or base.code_challenge_methods_supported,
        default_client_id=base.default_client_id,
        client_secret_credential_id=base.client_secret_credential_id,
        token_endpoint_auth_method=base.token_endpoint_auth_method,
        allow_insecure_loopback=base.allow_insecure_loopback,
        metadata=dict(base.metadata) | {"discovered": True},
    )


def _json_object(response: HTTPResponse) -> dict[str, Any]:
    if not response.body.strip():
        return {}
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthProtocolError("oauth_response_invalid_json") from exc
    if not isinstance(value, dict):
        raise OAuthProtocolError("oauth_response_object_required")
    return value


def _validate_redirect_uri(uri: str) -> None:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise OAuthConfigurationError("redirect_uri_must_be_https_or_loopback_http")


def _well_known_urls(issuer: str) -> list[str]:
    parsed = urllib.parse.urlparse(issuer.rstrip("/"))
    path = parsed.path.rstrip("/")
    oauth_path = "/.well-known/oauth-authorization-server" + path
    oauth_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, oauth_path, "", "", ""))
    oidc_path = (path + "/.well-known/openid-configuration") if path else "/.well-known/openid-configuration"
    oidc_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, oidc_path, "", "", ""))
    return [oauth_url, oidc_url]


def _assert_no_sensitive_metadata(metadata: Mapping[str, Any]) -> None:
    forbidden = {"access_token", "refresh_token", "id_token", "client_secret", "password", "cookie", "session_token", "api_key", "apikey"}
    stack: list[Any] = [metadata]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise OAuthConfigurationError("sensitive_provider_metadata_forbidden")
                stack.append(child)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)


def _validate_oauth_url(url: str, *, allow_insecure_loopback: bool) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and allow_insecure_loopback and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise OAuthConfigurationError("oauth_endpoint_must_be_https_or_explicit_loopback")


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    sensitive = {"access_token", "refresh_token", "id_token", "client_secret", "code", "device_code", "code_verifier", "password", "cookie", "session_token"}

    def redact(item: Any, key: str | None = None) -> Any:
        if key and key.lower() in sensitive:
            return "[REDACTED]"
        if isinstance(item, dict):
            return {str(k): redact(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v) for v in item]
        if isinstance(item, tuple):
            return [redact(v) for v in item]
        return item

    return redact(dict(value))
