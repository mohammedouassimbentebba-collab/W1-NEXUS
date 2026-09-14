"""Provider-neutral model adapter layer for NVIDIA NIM frontier models."""

from __future__ import annotations

import abc
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from .schema import (
    CumulativeTokenUsage,
    HealthCheckResult,
    ImageInput,
    ModelResponse,
    ModelRunConfig,
    ToolDefinition,
)
from .secrets import resolve_api_key, resolve_credential_key, scrub_secrets
from .token_meter import TokenMeter
from .profiles import (
    Credential,
    ModelProfile,
    ProviderConfig,
    NVIDIA_PROVIDER,
    get_model_profile,
)


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


class ModelAdapter(abc.ABC):
    """Abstract interface for benchmark model evaluation.

    An adapter is bound to a *model profile* and an optional *credential ref*.
    The secret is never stored on the adapter; it is resolved at execution time
    from a ``credential_ref``/``secret_ref``. Records and telemetry may persist
    ``credential_ref`` but never the API key.
    """

    def __init__(
        self,
        model_id: str,
        display_name: str,
        base_url: str = NVIDIA_NIM_BASE_URL,
        api_key: Optional[str] = None,
        *,
        provider_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
        profile: Optional[ModelProfile] = None,
        provider: Optional[ProviderConfig] = None,
    ) -> None:
        self.model_id = model_id
        self.display_name = display_name
        resolved_provider = provider or (NVIDIA_PROVIDER if not provider_id or provider_id == "nvidia" else None)
        self.provider_id = (provider_id or (resolved_provider.provider_id if resolved_provider else "nvidia"))
        self.base_url = (base_url if base_url else (resolved_provider.default_endpoint if resolved_provider else NVIDIA_NIM_BASE_URL)).rstrip("/")
        # A profile may override the endpoint via its provider; fall back to base_url.
        self.endpoint_url = self.base_url
        self.profile = profile or get_model_profile(model_id)
        if credential_ref:
            self.credential_ref = credential_ref
            self._resolved_key = api_key or resolve_credential_key(credential_ref) or resolve_api_key(model_id)
        else:
            # Infer normalized credential ref for provenance tracking
            import os
            key_low = model_id.lower()
            if "kimi" in key_low and os.environ.get("NVIDIA_API_KEY_KIMI"):
                self.credential_ref = f"{self.provider_id}-kimi"
            elif "muse" in key_low and os.environ.get("NVIDIA_API_KEY_MUSE"):
                self.credential_ref = f"{self.provider_id}-muse"
            elif "deepseek" in key_low and os.environ.get("NVIDIA_API_KEY_DEEPSEEK"):
                self.credential_ref = f"{self.provider_id}-deepseek"
            else:
                self.credential_ref = f"{self.provider_id}-main"
            self._resolved_key = api_key or resolve_credential_key(self.credential_ref) or resolve_api_key(model_id)
        self.token_meter = TokenMeter(model_id)
        self.ssl_context = ssl.create_default_context()

    @property
    def api_key(self) -> Optional[str]:
        """Backward-compatible accessor. Prefer resolving at call time."""
        return self._resolved_key

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        """Standard synchronous non-streaming text completion."""
        pass

    @abc.abstractmethod
    def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> Iterator[str]:
        """Streaming response token generator."""
        pass

    @abc.abstractmethod
    def tool_call(
        self,
        prompt: str,
        tools: Sequence[ToolDefinition],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        """Tool/function calling invocation."""
        pass

    @abc.abstractmethod
    def multimodal_input(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        """Multimodal image understanding invocation."""
        pass

    @abc.abstractmethod
    def structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        """Constrained decoding or JSON schema structured output."""
        pass

    def usage(self) -> CumulativeTokenUsage:
        return self.token_meter.get_cumulative()

    def health_check(self) -> HealthCheckResult:
        """Quick health probe verifying API reachability, authentication, and token accounting."""
        t0 = time.perf_counter()
        try:
            resp = self.generate("Ping. Respond with 'OK'.", config=ModelRunConfig(max_tokens=10, timeout_seconds=15.0))
            latency = (time.perf_counter() - t0) * 1000.0
            if resp.error:
                return HealthCheckResult(
                    healthy=False,
                    model=self.model_id,
                    provider="nvidia",
                    latency_ms=round(latency, 2),
                    message=f"Health probe returned error: {resp.error}",
                )
            return HealthCheckResult(
                healthy=True,
                model=self.model_id,
                provider="nvidia",
                latency_ms=round(latency, 2),
                message="Health check passed successfully",
                details={
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "content_preview": resp.content[:60],
                },
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return HealthCheckResult(
                healthy=False,
                model=self.model_id,
                provider="nvidia",
                latency_ms=round(latency, 2),
                message=f"Health check failed: {str(exc)}",
            )

    def _execute_http_post(
        self,
        payload: dict[str, Any],
        timeout_seconds: float = 120.0,
    ) -> tuple[dict[str, Any], float, Optional[float]]:
        """Executes HTTP POST using Python standard library with TLS verification."""
        if not self.api_key:
            raise RuntimeError(f"Missing API key for model {self.model_id}. Ensure NVIDIA_API_KEY is configured.")

        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "NEXUS-Benchmark-Lab/1.0",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds, context=self.ssl_context) as resp:
                body = resp.read().decode("utf-8")
                latency_ms = (time.perf_counter() - t0) * 1000.0
                parsed = json.loads(body)
                return parsed, latency_ms, None
        except urllib.error.HTTPError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            err_body = exc.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
                err_msg = err_json.get("error", {}).get("message", err_body)
            except Exception:
                err_msg = err_body
            raise RuntimeError(f"HTTP {exc.code}: {err_msg}") from exc
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            raise RuntimeError(f"Connection error: {str(exc)}") from exc

    def _execute_streaming_request(
        self,
        payload: dict[str, Any],
        timeout_seconds: float = 90.0,
    ) -> tuple[dict[str, Any], float, Optional[float]]:
        """Executes HTTP SSE streaming request and aggregates chunks."""
        if not self.api_key:
            raise RuntimeError(f"Missing API key for model {self.model_id}. Ensure NVIDIA_API_KEY is configured.")

        url = f"{self.base_url}/chat/completions"
        payload_stream = dict(payload)
        payload_stream["stream"] = True
        data = json.dumps(payload_stream).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
            "User-Agent": "NEXUS-Benchmark-Lab/1.0",
        }

        t0 = time.perf_counter()
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
            extra = {}
            if "chat_template_kwargs" in payload:
                extra["chat_template_kwargs"] = payload["chat_template_kwargs"]

            kwargs: dict[str, Any] = {
                "model": payload["model"],
                "messages": payload["messages"],
                "temperature": payload.get("temperature", 1.0),
                "top_p": payload.get("top_p", 0.95),
                "max_tokens": payload.get("max_tokens", 4096),
                "stream": True,
            }
            if "tools" in payload:
                kwargs["tools"] = payload["tools"]
                kwargs["tool_choice"] = payload.get("tool_choice", "auto")
            if "seed" in payload and payload["seed"] is not None:
                kwargs["seed"] = payload["seed"]
            if extra:
                kwargs["extra_body"] = extra

            stream_resp = client.chat.completions.create(**kwargs)
            deadline = t0 + timeout_seconds
            ttft_ms: Optional[float] = None
            first_chunk_ms: Optional[float] = None
            chunk_count: int = 0
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls_acc: list[dict[str, Any]] = []
            usage: dict[str, Any] = {}
            finish_reason = "stop"

            for chunk in stream_resp:
                now = time.perf_counter()
                chunk_count += 1
                if first_chunk_ms is None:
                    first_chunk_ms = (now - t0) * 1000.0

                if now > deadline:
                    raise TimeoutError(
                        f"Streaming request exceeded wall-clock timeout of {timeout_seconds:.1f}s "
                        f"(received {chunk_count} chunks, first_chunk={first_chunk_ms:.1f}ms)"
                    )

                if chunk.choices:
                    delta = chunk.choices[0].delta
                    has_text = False
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        reasoning_parts.append(delta.reasoning_content)
                        has_text = True
                    if delta.content:
                        content_parts.append(delta.content)
                        has_text = True
                    if has_text and ttft_ms is None:
                        ttft_ms = (now - t0) * 1000.0

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            while len(tool_calls_acc) <= idx:
                                tool_calls_acc.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if tc.id:
                                tool_calls_acc[idx]["id"] += tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["function"]["name"] += tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    usage = {
                        "prompt_tokens": getattr(u, "prompt_tokens", None),
                        "completion_tokens": getattr(u, "completion_tokens", None),
                        "total_tokens": getattr(u, "total_tokens", None),
                    }

            latency_ms = (time.perf_counter() - t0) * 1000.0
            msg_payload: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts),
                "reasoning_content": "".join(reasoning_parts) if reasoning_parts else None,
            }
            if tool_calls_acc:
                msg_payload["tool_calls"] = tool_calls_acc

            assembled = {
                "choices": [{
                    "message": msg_payload,
                    "finish_reason": finish_reason,
                }],
                "usage": usage or {
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                }
            }
            return assembled, latency_ms, ttft_ms
        except ImportError:
            pass
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            raise RuntimeError(f"Connection error: {str(exc)}") from exc


# ---------------------------------------------------------------------------
# 0. Generic Profile-Driven OpenAI Compatible Adapter
# ---------------------------------------------------------------------------

class OpenAICompatibleAdapter(ModelAdapter):
    """Generic profile-driven adapter for OpenAI-compatible and NVIDIA NIM models.

    Derives default parameters (streaming, temperature, top_p, reasoning_effort,
    chat_template_kwargs, timeout, max_tokens) from its ModelProfile contract.
    """

    def __init__(
        self,
        model_id: str,
        display_name: Optional[str] = None,
        base_url: str = NVIDIA_NIM_BASE_URL,
        api_key: Optional[str] = None,
        *,
        provider_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
        profile: Optional[ModelProfile] = None,
        provider: Optional[ProviderConfig] = None,
    ) -> None:
        prof = profile or get_model_profile(model_id)
        disp_name = display_name or prof.display_name
        super().__init__(
            model_id=prof.model_id,
            display_name=disp_name,
            base_url=base_url,
            api_key=api_key,
            provider_id=provider_id,
            credential_ref=credential_ref,
            profile=prof,
            provider=provider,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Derive parameters with profile fallbacks
        temperature = cfg.temperature if cfg.temperature > 0.0 else self.profile.default_temperature
        top_p = cfg.top_p if (cfg.top_p > 0.0 and cfg.top_p < 1.0) else self.profile.default_top_p
        max_tokens = max(cfg.max_tokens or 0, self.profile.min_max_tokens)
        timeout = max(cfg.timeout_seconds or 0, self.profile.timeout_seconds)

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if cfg.seed is not None:
            payload["seed"] = cfg.seed
        if cfg.stop_sequences:
            payload["stop"] = list(cfg.stop_sequences)
        if self.profile.reasoning_effort:
            payload["reasoning_effort"] = self.profile.reasoning_effort
        if self.profile.chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self.profile.chat_template_kwargs)
        if self.profile.extra_body:
            payload.update(self.profile.extra_body)
        if cfg.extra_body:
            payload.update(cfg.extra_body)

        stream = self.profile.streaming_preferred
        payload["stream"] = stream

        t0 = time.perf_counter()
        try:
            if stream:
                data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            else:
                data, latency_ms, ttft_ms = self._execute_http_post(payload, timeout)

            choices = data.get("choices", [])
            choice = choices[0] if choices else {}
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning_content = msg.get("reasoning_content")
            if not content and reasoning_content:
                content = reasoning_content
            finish_reason = choice.get("finish_reason", "stop")

            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = details.get("reasoning_tokens")

            self.token_meter.record_usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                is_estimated=False,
            )

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider=self.provider_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=usage.get("prompt_tokens_details", {}).get("cached_tokens"),
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                tool_calls=[],
                finish_reason=finish_reason,
                error=None,
                is_estimated=False,
                raw_response_metadata=scrub_secrets(data),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider=self.provider_id,
                latency_ms=latency_ms,
                error=str(exc),
            )

    def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> Iterator[str]:
        resp = self.generate(prompt, system_prompt, config)
        if resp.content:
            yield resp.content

    def tool_call(
        self,
        prompt: str,
        tools: Sequence[ToolDefinition],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

        temperature = cfg.temperature if cfg.temperature > 0.0 else self.profile.default_temperature
        top_p = cfg.top_p if (cfg.top_p > 0.0 and cfg.top_p < 1.0) else self.profile.default_top_p
        max_tokens = max(cfg.max_tokens or 0, self.profile.min_max_tokens)
        timeout = max(cfg.timeout_seconds or 0, self.profile.timeout_seconds)

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "tools": tool_defs,
            "tool_choice": "auto",
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if self.profile.chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self.profile.chat_template_kwargs)
        if self.profile.extra_body:
            payload.update(self.profile.extra_body)
        if cfg.extra_body:
            payload.update(cfg.extra_body)

        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            choices = data.get("choices", [])
            choice = choices[0] if choices else {}
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            raw_tools = msg.get("tool_calls") or []
            tool_calls: list[dict[str, Any]] = []
            for rt in raw_tools:
                fn = rt.get("function") or {}
                tool_calls.append({
                    "id": rt.get("id", f"call_{len(tool_calls)}"),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider=self.provider_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider=self.provider_id,
                latency_ms=latency_ms,
                error=str(exc),
            )

    def multimodal_input(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        if not self.profile.capabilities.multimodal:
            return ModelResponse(
                content="",
                model=self.model_id,
                provider=self.provider_id,
                error=f"Multimodal input not supported for model {self.model_id}",
            )
        import base64
        cfg = config or ModelRunConfig()
        content_items: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img.image_bytes).decode("ascii")
            content_items.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime_type};base64,{b64}"},
            })

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_items})

        payload = {
            "model": self.model_id,
            "messages": messages,
            "top_p": self.profile.default_top_p,
            "max_tokens": max(cfg.max_tokens or 0, self.profile.min_max_tokens),
            "stream": True,
        }
        timeout = max(cfg.timeout_seconds or 0, self.profile.timeout_seconds)
        data, latency_ms, _ = self._execute_streaming_request(payload, timeout)
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        usage = data.get("usage") or {}

        return ModelResponse(
            content=content,
            model=self.model_id,
            provider=self.provider_id,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
        )

    def structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        system = (system_prompt or "") + f"\nYou MUST return ONLY a JSON object strictly matching this schema: {json.dumps(schema)}"
        return self.generate(prompt, system.strip(), config)


# ---------------------------------------------------------------------------
# 1. Kimi K3 Adapter
# ---------------------------------------------------------------------------

class KimiK3Adapter(ModelAdapter):
    """Adapter for Moonshot Kimi K3 hosted on NVIDIA NIM."""

    def __init__(
        self,
        model_id: str = "moonshotai/kimi-k3",
        base_url: str = NVIDIA_NIM_BASE_URL,
        api_key: Optional[str] = None,
        *,
        provider_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            display_name="Kimi K3",
            base_url=base_url,
            api_key=api_key or resolve_api_key("kimi"),
            provider_id=provider_id,
            credential_ref=credential_ref,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Ensure max_tokens is sufficient for reasoning chains (official recommended >= 8192)
        max_tokens = max(cfg.max_tokens, 8192)
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": cfg.temperature,
            "top_p": 0.95,  # Strict NVIDIA validation: `top_p` is immutable and must be 0.95
            "max_tokens": max_tokens,
            "reasoning_effort": "max",
        }
        if cfg.stop_sequences:
            payload["stop"] = list(cfg.stop_sequences)
        if cfg.extra_body:
            payload.update(cfg.extra_body)

        timeout = max(cfg.timeout_seconds, 90.0)
        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            choices = data.get("choices", [])
            choice = choices[0] if choices else {}
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning_content = msg.get("reasoning_content")
            finish_reason = choice.get("finish_reason", "stop")

            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            
            details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = details.get("reasoning_tokens")

            # Update token accounting
            self.token_meter.record_usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                is_estimated=False,
            )

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider="nvidia",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_tokens=usage.get("prompt_tokens_details", {}).get("cached_tokens"),
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                tool_calls=[],
                finish_reason=finish_reason,
                error=None,
                is_estimated=False,
                raw_response_metadata=scrub_secrets(data),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider="nvidia",
                latency_ms=latency_ms,
                error=str(exc),
            )

    def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> Iterator[str]:
        # Minimal iterator fallback for streaming
        resp = self.generate(prompt, system_prompt, config)
        if resp.content:
            yield resp.content

    def tool_call(
        self,
        prompt: str,
        tools: Sequence[ToolDefinition],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "tools": tool_defs,
            "tool_choice": "auto",
            "temperature": cfg.temperature,
            "top_p": 0.95,
            "max_tokens": max(cfg.max_tokens or 0, 8192),
            "stream": True,
        }

        timeout = max(cfg.timeout_seconds, 60.0)
        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            choices = data.get("choices", [])
            choice = choices[0] if choices else {}
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            raw_tools = msg.get("tool_calls") or []
            tool_calls: list[dict[str, Any]] = []
            for rt in raw_tools:
                fn = rt.get("function") or {}
                tool_calls.append({
                    "id": rt.get("id", f"call_{len(tool_calls)}"),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider="nvidia",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider="nvidia",
                latency_ms=latency_ms,
                error=str(exc),
            )

    def multimodal_input(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        import base64
        cfg = config or ModelRunConfig()
        content_items: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img.image_bytes).decode("ascii")
            content_items.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime_type};base64,{b64}"},
            })

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_items})

        payload = {
            "model": self.model_id,
            "messages": messages,
            "top_p": 0.95,
            "max_tokens": max(cfg.max_tokens or 0, 8192),
            "stream": True,
        }

        timeout = max(cfg.timeout_seconds, 60.0)
        data, latency_ms, _ = self._execute_streaming_request(payload, timeout)
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content") or ""
        usage = data.get("usage") or {}

        return ModelResponse(
            content=content,
            model=self.model_id,
            provider="nvidia",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
        )

    def structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        system = (system_prompt or "") + f"\nYou MUST return ONLY a JSON object strictly matching this schema: {json.dumps(schema)}"
        return self.generate(prompt, system.strip(), config)


# ---------------------------------------------------------------------------
# 2. Muse Adapter
# ---------------------------------------------------------------------------

class MuseAdapter(ModelAdapter):
    """Adapter for Meta Muse (meta/muse-glimmer-30b) on NVIDIA NIM."""

    def __init__(
        self,
        model_id: str = "meta/muse-glimmer-30b",
        base_url: str = NVIDIA_NIM_BASE_URL,
        api_key: Optional[str] = None,
        *,
        provider_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            display_name="Muse (Glimmer 30B)",
            base_url=base_url,
            api_key=api_key or resolve_api_key("muse"),
            provider_id=provider_id,
            credential_ref=credential_ref,
        )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        temp = cfg.temperature if cfg.temperature > 0.0 else 0.7
        top_p = cfg.top_p if cfg.top_p > 0.0 else 0.95
        # Muse is a reasoning model that needs sufficient token headroom
        max_tokens = max(cfg.max_tokens or 0, 4096)

        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temp,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }

        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_http_post(payload, cfg.timeout_seconds)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            reasoning_content = msg.get("reasoning_content")
            if not content and reasoning_content:
                content = reasoning_content
            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")

            self.token_meter.record_usage(input_tokens, output_tokens, total_tokens=total_tokens)

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider="nvidia",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                finish_reason=choice.get("finish_reason", "stop"),
                raw_response_metadata=scrub_secrets(data),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider="nvidia",
                latency_ms=latency_ms,
                error=str(exc),
            )

    def stream(self, prompt: str, system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> Iterator[str]:
        resp = self.generate(prompt, system_prompt, config)
        if resp.content:
            yield resp.content

    def tool_call(self, prompt: str, tools: Sequence[ToolDefinition], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        # Fallback to structured prompting if native tool format is unsupported by Muse endpoint
        tool_prompt = prompt + "\nAvailable tools: " + json.dumps([{"name": t.name, "description": t.description} for t in tools])
        return self.generate(tool_prompt, system_prompt, config)

    def multimodal_input(self, prompt: str, images: Sequence[ImageInput], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        return ModelResponse(content="", model=self.model_id, provider="nvidia", error="Multimodal input not supported by Muse")

    def structured_output(self, prompt: str, schema: dict[str, Any], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        system = (system_prompt or "") + f"\nRespond in valid JSON adhering to schema: {json.dumps(schema)}"
        return self.generate(prompt, system.strip(), config)


# ---------------------------------------------------------------------------
# 3. DeepSeek V4 Pro Adapter
# ---------------------------------------------------------------------------

class DeepSeekV4ProAdapter(ModelAdapter):
    """Adapter for DeepSeek V4 Pro (deepseek-ai/deepseek-v4-pro-0813) on NVIDIA NIM."""

    def __init__(
        self,
        model_id: str = "deepseek-ai/deepseek-v4-pro-0813",
        base_url: str = NVIDIA_NIM_BASE_URL,
        api_key: Optional[str] = None,
        thinking: bool = False,
        *,
        provider_id: Optional[str] = None,
        credential_ref: Optional[str] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            display_name="DeepSeek V4 Pro",
            base_url=base_url,
            api_key=api_key or resolve_api_key("deepseek"),
            provider_id=provider_id,
            credential_ref=credential_ref,
        )
        self.thinking = thinking

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        config: Optional[ModelRunConfig] = None,
    ) -> ModelResponse:
        cfg = config or ModelRunConfig(temperature=1.0, top_p=0.95, max_tokens=16384, seed=42)
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        timeout = max(cfg.timeout_seconds or 0, 240.0)
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p or 0.95,
            "max_tokens": cfg.max_tokens or 16384,
            "seed": cfg.seed,
            "stream": True,
            "chat_template_kwargs": {"thinking": self.thinking},
        }
        if cfg.extra_body:
            payload.update(cfg.extra_body)

        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            choice = (data.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content") or ""
            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")

            details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = details.get("reasoning_tokens")

            self.token_meter.record_usage(
                input_tokens,
                output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
            )

            return ModelResponse(
                content=content,
                model=self.model_id,
                provider="nvidia",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                finish_reason=choice.get("finish_reason", "stop"),
                raw_response_metadata=scrub_secrets(data),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(
                content="",
                model=self.model_id,
                provider="nvidia",
                latency_ms=latency_ms,
                error=str(exc),
            )

    def stream(self, prompt: str, system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> Iterator[str]:
        resp = self.generate(prompt, system_prompt, config)
        if resp.content:
            yield resp.content

    def tool_call(self, prompt: str, tools: Sequence[ToolDefinition], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        cfg = config or ModelRunConfig()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

        timeout = max(cfg.timeout_seconds or 0, 240.0)
        payload = {
            "model": self.model_id,
            "messages": messages,
            "tools": tool_defs,
            "tool_choice": "auto",
            "temperature": cfg.temperature,
            "top_p": cfg.top_p or 0.95,
            "max_tokens": cfg.max_tokens or 16384,
            "stream": True,
            "chat_template_kwargs": {"thinking": self.thinking},
        }

        t0 = time.perf_counter()
        try:
            data, latency_ms, ttft_ms = self._execute_streaming_request(payload, timeout)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            raw_tools = msg.get("tool_calls") or []
            tool_calls: list[dict[str, Any]] = []
            for rt in raw_tools:
                fn = rt.get("function") or {}
                tool_calls.append({
                    "id": rt.get("id", f"call_{len(tool_calls)}"),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

            usage = data.get("usage") or {}
            return ModelResponse(
                content=content,
                model=self.model_id,
                provider="nvidia",
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                latency_ms=latency_ms,
                time_to_first_token_ms=ttft_ms,
                tool_calls=tool_calls,
                finish_reason=choice.get("finish_reason", "stop"),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ModelResponse(content="", model=self.model_id, provider="nvidia", latency_ms=latency_ms, error=str(exc))

    def multimodal_input(self, prompt: str, images: Sequence[ImageInput], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        return ModelResponse(content="", model=self.model_id, provider="nvidia", error="Multimodal not supported on this endpoint variant")

    def structured_output(self, prompt: str, schema: dict[str, Any], system_prompt: Optional[str] = None, config: Optional[ModelRunConfig] = None) -> ModelResponse:
        system = (system_prompt or "") + f"\nOutput ONLY a valid JSON object matching schema: {json.dumps(schema)}. No markdown fences."
        return self.generate(prompt, system.strip(), config)


def get_model_adapter(
    model_name_or_key: str,
    *,
    credential_ref: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> ModelAdapter:
    """Factory helper to obtain the appropriate ModelAdapter.

    Backward compatible: ``get_model_adapter("kimi")`` continues to work.
    An optional ``credential_ref`` selects a specific credential (EXPLICIT);
    when omitted the adapter falls back to the legacy per-family env mapping.
    """
    key = model_name_or_key.lower()
    profile = get_model_profile(key)
    if profile.model_id == "moonshotai/kimi-k3":
        return KimiK3Adapter(credential_ref=credential_ref, provider_id=provider_id)
    elif profile.model_id == "meta/muse-glimmer-30b":
        return MuseAdapter(credential_ref=credential_ref, provider_id=provider_id)
    elif profile.model_id == "deepseek-ai/deepseek-v4-pro-0813":
        return DeepSeekV4ProAdapter(credential_ref=credential_ref, provider_id=provider_id)
    else:
        return OpenAICompatibleAdapter(
            model_id=profile.model_id,
            display_name=profile.display_name,
            provider_id=provider_id or profile.provider_id,
            credential_ref=credential_ref,
            profile=profile,
        )


def resolve_model(
    provider: str = "nvidia",
    model_id: str = "moonshotai/kimi-k3",
    credential_ref: Optional[str] = None,
    *,
    selector: Optional[Any] = None,
    entitlements: Optional[Mapping[str, Sequence[str]]] = None,
) -> ModelAdapter:
    """Resolve a ModelAdapter using explicit or auto credential selection.

    EXPLICIT mode: when credential_ref is given, selector resolves to that exact credential.
    AUTO mode: when credential_ref is None, selects an authorized credential for the requested model.
    """
    from .profiles import (
        AutoCredentialSelector,
        Credential,
        CredentialSelector,
        ExplicitCredentialSelector,
        get_model_profile,
        get_provider,
    )
    from .secrets import list_credentials

    # Ensure provider exists
    get_provider(provider)
    profile = get_model_profile(model_id)

    available_creds = list_credentials(provider)
    if not available_creds:
        available_creds = [
            Credential(credential_ref=f"{provider}-main", provider_id=provider, secret_ref="NVIDIA_API_KEY")
        ]

    if selector is None:
        if credential_ref is not None:
            selector = ExplicitCredentialSelector(credential_ref)
        else:
            selector = AutoCredentialSelector()

    selected_cred = selector.select(
        provider_id=provider,
        model_id=profile.model_id,
        available_credentials=available_creds,
        entitlements=entitlements,
    )

    return get_model_adapter(
        profile.model_id,
        credential_ref=selected_cred.credential_ref,
        provider_id=provider,
    )

