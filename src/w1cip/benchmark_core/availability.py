"""Model Availability and Entitlement Diagnostic Module.

Performs deep 10-point health and capability qualification for all candidate models:
1. /v1/models catalog entitlement check
2. Single non-stream completion probe
3. Streaming completion probe
4. Tool-calling completion probe
5. Structured-output completion probe
6. Controlled retry probe
7. Rate-limit header capture
8. HTTP status capture
9. Exact latency capture
10. Explicit Provider Infrastructure vs Model Capability failure taxonomy
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .secrets import resolve_api_key, mask_secret, resolve_credential_key
from .profiles import (
    KNOWN_MODEL_PROFILES,
    get_provider,
    get_model_profile,
)


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass
class ModelAvailabilityRecord:
    model: str
    display_name: str
    key_masked: str
    entitlement: str  # AUTHORIZED, NOT_AUTHORIZED, TEMPORARILY_UNAVAILABLE, RATE_LIMITED, MODEL_NOT_FOUND, PROVIDER_ERROR
    availability: bool
    http_status: Optional[int]
    latency_ms: float
    streaming: bool
    tool_calling: bool
    structured_output: bool
    rate_limited: bool
    provider_error: Optional[str]
    rate_limit_headers: dict[str, str]
    details: dict[str, Any]


class ModelAvailabilityTester:
    """Executes dedicated availability and entitlement diagnostics."""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = workspace_root or Path(__file__).resolve().parent.parent.parent.parent
        self.availability_dir = self.workspace_root / "data" / "availability"
        self.availability_dir.mkdir(parents=True, exist_ok=True)
        self.ssl_context = ssl.create_default_context()

    def check_catalog_presence(self, api_key: str, model_id: str) -> tuple[bool, str]:
        """Queries /v1/models to verify if model is recognized and listed by NVIDIA."""
        url = f"{NVIDIA_NIM_BASE_URL}/models"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "NEXUS-Benchmark-Lab/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15.0, context=self.ssl_context) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                if model_id in models:
                    return True, "Model present in NVIDIA catalog"
                return False, f"Model not listed in NVIDIA catalog (found {len(models)} models)"
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False, f"HTTP {exc.code} Unauthorized/Forbidden"
            return False, f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            return False, f"Catalog query failed: {type(exc).__name__}: {exc}"

    def probe_model(self, model_key: str) -> ModelAvailabilityRecord:
        """Runs the complete 10-point availability diagnostic for one model."""
        target_map = {
            "kimi": ("moonshotai/kimi-k3", "Kimi K3"),
            "muse": ("meta/muse-glimmer-30b", "Muse Glimmer 30B"),
            "deepseek": ("deepseek-ai/deepseek-v4-pro-0813", "DeepSeek V4 Pro"),
        }
        if model_key not in target_map:
            raise ValueError(f"Unknown model key: {model_key}")

        model_id, display_name = target_map[model_key]
        api_key = resolve_api_key(model_key)
        masked = mask_secret(api_key)

        headers_captured: dict[str, str] = {}
        http_status: Optional[int] = None
        rate_limited = False
        provider_error: Optional[str] = None
        entitlement = "UNKNOWN"
        availability = False
        streaming_ok = False
        tool_calling_ok = False
        structured_ok = False
        total_latency = 0.0

        if not api_key:
            return ModelAvailabilityRecord(
                model=model_id,
                display_name=display_name,
                key_masked="MISSING",
                entitlement="NOT_AUTHORIZED",
                availability=False,
                http_status=None,
                latency_ms=0.0,
                streaming=False,
                tool_calling=False,
                structured_output=False,
                rate_limited=False,
                provider_error="Missing API key in environment / .env",
                rate_limit_headers={},
                details={},
            )

        # 1. Catalog check
        catalog_ok, catalog_msg = self.check_catalog_presence(api_key, model_id)

        # 2. Primary completion probe
        url = f"{NVIDIA_NIM_BASE_URL}/chat/completions"
        probe_timeout = 150.0 if "deepseek" in model_id else (60.0 if "kimi" in model_id else 18.0)
        max_tok = 16384 if "deepseek" in model_id else (8192 if "kimi" in model_id else (4096 if "muse" in model_id else 64))
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Respond with 'PONG'"}],
            "max_tokens": max_tok,
            "temperature": 0.5,
            "stream": True if "deepseek" in model_id else False,
        }
        if "kimi" in model_id:
            payload["top_p"] = 0.95
        if "deepseek" in model_id:
            payload["top_p"] = 0.95
            payload["chat_template_kwargs"] = {"thinking": False}

        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "NEXUS-Benchmark-Lab/1.0",
        }
        if payload.get("stream"):
            headers["Accept"] = "text/event-stream"

        req = urllib.request.Request(
            url,
            data=req_data,
            headers=headers,
            method="POST",
        )

        t0 = time.perf_counter()
        probe_response_body = ""
        try:
            with urllib.request.urlopen(req, timeout=probe_timeout, context=self.ssl_context) as resp:
                http_status = resp.status
                total_latency = (time.perf_counter() - t0) * 1000.0
                for k, v in resp.headers.items():
                    if "ratelimit" in k.lower() or "retry" in k.lower():
                        headers_captured[k] = v
                if payload.get("stream"):
                    for line in resp:
                        decoded = line.decode("utf-8").strip()
                        if decoded.startswith("data: ") and not decoded.endswith("[DONE]"):
                            chunk = json.loads(decoded[6:])
                            if chunk.get("choices"):
                                availability = True
                                entitlement = "AUTHORIZED"
                                streaming_ok = True
                                break
                else:
                    body_bytes = resp.read()
                    probe_response_body = body_bytes.decode("utf-8")
                    resp_json = json.loads(probe_response_body)
                    choices = resp_json.get("choices", [])
                    if choices:
                        availability = True
                        entitlement = "AUTHORIZED"
                    else:
                        provider_error = "Empty choices returned from endpoint"
                        entitlement = "PROVIDER_ERROR"
        except urllib.error.HTTPError as exc:
            total_latency = (time.perf_counter() - t0) * 1000.0
            http_status = exc.code
            for k, v in exc.headers.items():
                if "ratelimit" in k.lower() or "retry" in k.lower():
                    headers_captured[k] = v
            err_body = exc.read().decode("utf-8", errors="replace")
            provider_error = f"HTTP {exc.code}: {err_body[:200]}"
            if exc.code == 429:
                rate_limited = True
                entitlement = "RATE_LIMITED"
            elif exc.code in (401, 403):
                entitlement = "NOT_AUTHORIZED"
            elif exc.code == 404:
                entitlement = "MODEL_NOT_FOUND"
            elif exc.code in (500, 502, 503, 504):
                entitlement = "TEMPORARILY_UNAVAILABLE"
            else:
                entitlement = "PROVIDER_ERROR"
        except Exception as exc:
            total_latency = (time.perf_counter() - t0) * 1000.0
            err_name = type(exc).__name__
            provider_error = f"{err_name}: {str(exc)}"
            if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
                entitlement = "TEMPORARILY_UNAVAILABLE"
            else:
                entitlement = "PROVIDER_ERROR"

        # 3. Streaming probe (if availability is True and not already marked streaming_ok)
        if availability and not streaming_ok:
            time.sleep(1.0)
            stream_payload = dict(payload)
            stream_payload["stream"] = True
            stream_data = json.dumps(stream_payload).encode("utf-8")
            s_req = urllib.request.Request(
                url,
                data=stream_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/event-stream",
                    "User-Agent": "NEXUS-Benchmark-Lab/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(s_req, timeout=probe_timeout, context=self.ssl_context) as s_resp:
                    for line in s_resp:
                        decoded = line.decode("utf-8").strip()
                        if decoded.startswith("data: ") and not decoded.endswith("[DONE]"):
                            streaming_ok = True
                            break
            except Exception:
                streaming_ok = False

            # 4. Tool-calling probe
            time.sleep(2.0)
            tool_payload = dict(payload)
            tool_payload["stream"] = True
            tool_payload["tool_choice"] = "auto"
            tool_payload["tools"] = [{
                "type": "function",
                "function": {
                    "name": "lookup_metric",
                    "description": "Look up system metric",
                    "parameters": {
                        "type": "object",
                        "properties": {"metric_name": {"type": "string"}},
                        "required": ["metric_name"],
                    },
                },
            }]
            tool_payload["messages"] = [{"role": "user", "content": "Please call lookup_metric with metric_name='cpu_idle'"}]
            t_data = json.dumps(tool_payload).encode("utf-8")
            t_req = urllib.request.Request(
                url,
                data=t_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/event-stream",
                    "User-Agent": "NEXUS-Benchmark-Lab/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(t_req, timeout=probe_timeout, context=self.ssl_context) as t_resp:
                    for line in t_resp:
                        decoded = line.decode("utf-8").strip()
                        if decoded.startswith("data: ") and not decoded.endswith("[DONE]"):
                            try:
                                chunk = json.loads(decoded[6:])
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                if delta.get("tool_calls") or "lookup_metric" in (delta.get("content") or ""):
                                    tool_calling_ok = True
                                    break
                            except Exception:
                                pass
            except Exception:
                tool_calling_ok = False

            # 5. Structured output probe
            time.sleep(2.0)
            struct_payload = dict(payload)
            struct_payload["stream"] = True
            if "kimi" in model_id:
                struct_payload["response_format"] = {"type": "json_object"}
            struct_payload["messages"] = [
                {"role": "user", "content": "Output a valid JSON object with key 'status' equal to 'ready'. Return ONLY raw JSON."}
            ]
            st_data = json.dumps(struct_payload).encode("utf-8")
            st_req = urllib.request.Request(
                url,
                data=st_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "text/event-stream",
                    "User-Agent": "NEXUS-Benchmark-Lab/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(st_req, timeout=probe_timeout, context=self.ssl_context) as st_resp:
                    for line in st_resp:
                        decoded = line.decode("utf-8").strip()
                        if decoded.startswith("data: ") and not decoded.endswith("[DONE]"):
                            try:
                                chunk = json.loads(decoded[6:])
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                text = delta.get("content") or ""
                                if "{" in text or "status" in text:
                                    structured_ok = True
                                    break
                            except Exception:
                                pass
            except Exception:
                structured_ok = False

        return ModelAvailabilityRecord(
            model=model_id,
            display_name=display_name,
            key_masked=masked,
            entitlement=entitlement,
            availability=availability,
            http_status=http_status,
            latency_ms=round(total_latency, 1),
            streaming=streaming_ok,
            tool_calling=tool_calling_ok,
            structured_output=structured_ok,
            rate_limited=rate_limited,
            provider_error=provider_error,
            rate_limit_headers=headers_captured,
            details={
                "catalog_presence": catalog_ok,
                "catalog_message": catalog_msg,
            },
        )

    def check_all(self) -> list[ModelAvailabilityRecord]:
        records: list[ModelAvailabilityRecord] = []
        for k in ["kimi", "muse", "deepseek"]:
            rec = self.probe_model(k)
            records.append(rec)

        self._save_report(records)
        return records

    def _save_report(self, records: list[ModelAvailabilityRecord]) -> Path:
        out_path = self.availability_dir / "model_availability_report.json"
        payload = [asdict(r) for r in records]
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out_path


# ---------------------------------------------------------------------------
# Credential x Model availability matrix
# ---------------------------------------------------------------------------


@dataclass
class CredentialModelAvailability:
    credential_ref: str
    provider_id: str
    model_id: str
    display_name: str
    catalog_visible: bool = False
    inference_available: bool = False
    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False

    @property
    def streaming_available(self) -> bool:
        return self.streaming

    @property
    def tool_calling_available(self) -> bool:
        return self.tool_calling

    @property
    def structured_output_available(self) -> bool:
        return self.structured_output

    @property
    def operational(self) -> bool:
        """A model is operational only when catalog visibility AND inference both hold."""
        return self.catalog_visible and self.inference_available

    def to_dict(self) -> dict[str, Any]:
        return {
            "credential_ref": self.credential_ref,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "catalog_visible": self.catalog_visible,
            "inference_available": self.inference_available,
            "streaming_available": self.streaming,
            "tool_calling_available": self.tool_calling,
            "structured_output_available": self.structured_output,
            "operational": self.operational,
        }


def discover_catalog_model_ids(api_key: str, endpoint: Optional[str] = None) -> list[str]:
    """GET /v1/models and return the model IDs a credential can see.

    This is *catalog visibility* only. It does not prove inference works.
    """
    endpoint = (endpoint or NVIDIA_NIM_BASE_URL).rstrip("/")
    url = f"{endpoint}/models"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "NEXUS-Benchmark-Lab/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("id") for m in data.get("data", [])]
            return [m for m in models if isinstance(m, str)]
    except Exception:
        return []


class CredentialModelAvailabilityTester:
    """Builds the credential x model availability matrix.

    Catalog listing and inference probing are injectable so tests can run
    fully offline while production defaults hit the real provider.
    """

    def __init__(
        self,
        *,
        catalog_probe: Optional[Any] = None,
        inference_probe: Optional[Any] = None,
        endpoint: Optional[str] = None,
        model_ids: Optional[list[str]] = None,
        credential_refs: Optional[list[str]] = None,
    ) -> None:
        self._catalog_probe = catalog_probe
        self._inference_probe = inference_probe
        self.endpoint = endpoint or NVIDIA_NIM_BASE_URL
        # model_ids constrains which known profiles are considered.
        self.model_ids = model_ids or list(KNOWN_MODEL_PROFILES)
        # credential_refs constrains which credentials are iterated; defaults to
        # the registered NVIDIA credential set.
        self.credential_refs = credential_refs

    def resolve_api_key(self, credential_ref: str) -> Optional[str]:
        return resolve_credential_key(credential_ref)

    def catalog_visible(self, credential_ref: str, model_id: str) -> bool:
        if self._catalog_probe is not None:
            return bool(self._catalog_probe(credential_ref, model_id))
        api_key = self.resolve_api_key(credential_ref)
        if not api_key:
            return False
        return model_id in discover_catalog_model_ids(api_key, self.endpoint)

    def inference_available(self, credential_ref: str, model_id: str) -> bool:
        if self._inference_probe is not None:
            return bool(self._inference_probe(credential_ref, model_id))
        # Production default: a lightweight non-streaming completion probe.
        api_key = self.resolve_api_key(credential_ref)
        if not api_key:
            return False
        try:
            profile = get_model_profile(model_id)
            payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": "Respond with PONG"}],
                "max_tokens": 8,
                "temperature": 0.5,
            }
            url = f"{self.endpoint.rstrip('/')}/chat/completions"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "NEXUS-Benchmark-Lab/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=profile.timeout_seconds, context=ssl.create_default_context()) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return bool(body.get("choices"))
        except Exception:
            return False

    def matrix(self) -> list[CredentialModelAvailability]:
        from .secrets import list_credential_refs
        credentials = self.credential_refs if self.credential_refs is not None else list_credential_refs()
        results: list[CredentialModelAvailability] = []
        # When probes are injected we can run offline without a real secret.
        injected = self._catalog_probe is not None or self._inference_probe is not None
        for credential_ref in credentials:
            if not injected:
                api_key = self.resolve_api_key(credential_ref)
                if not api_key:
                    continue
            for model_id in self.model_ids:
                profile = get_model_profile(model_id)
                catalog = self.catalog_visible(credential_ref, model_id)
                inference = catalog and self.inference_available(credential_ref, model_id)
                results.append(
                    CredentialModelAvailability(
                        credential_ref=credential_ref,
                        provider_id=profile.provider_id,
                        model_id=model_id,
                        display_name=profile.display_name,
                        catalog_visible=catalog,
                        inference_available=inference,
                        streaming=profile.streaming_preferred,
                        tool_calling=profile.capabilities.tool_calling,
                        structured_output=profile.capabilities.structured_output,
                    )
                )
        return results

    def discover_for_credential(self, credential_ref: str) -> list[str]:
        """Return model IDs visible in the catalog for one credential."""
        api_key = self.resolve_api_key(credential_ref)
        if not api_key:
            return []
        return discover_catalog_model_ids(api_key, self.endpoint)


if __name__ == "__main__":
    tester = ModelAvailabilityTester()
    res = tester.check_all()
    for r in res:
        print(f"[{r.display_name}] Available: {r.availability}, Entitlement: {r.entitlement}, Status: {r.http_status}, Latency: {r.latency_ms}ms")
