"""Local-first federated search for AI capacity and model opportunities.

Step 49 deliberately does *not* require a W1-owned server.  Search requests are
issued directly from the user's local W1 process to a small allowlist of public
catalog/search APIs and the redaction-safe result cache lives inside the local
workspace.  Discovery is evidence, not entitlement: candidates are never
silently registered for routing and third-party results require review.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

INTELLIGENCE_SEARCH_VERSION = "1.0"
SEARCH_SOURCES = {"openrouter", "github", "huggingface"}
FREE_STATUSES = {"verified_free", "free_marker", "open_weights_candidate", "unknown"}
REVIEW_STATUSES = {"connectable", "manual_review", "local_candidate"}
_ALLOWED_HOSTS = {"openrouter.ai", "api.github.com", "huggingface.co"}
_DEFAULT_TIMEOUT = 8.0
_MAX_RESPONSE_BYTES = 4_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class IntelligenceSearchError(RuntimeError):
    code = "intelligence_search_error"


class IntelligenceSearchNetworkError(IntelligenceSearchError):
    code = "intelligence_search_network_error"


class IntelligenceSearchResponseError(IntelligenceSearchError):
    code = "intelligence_search_response_invalid"


@dataclass(frozen=True)
class SearchCandidate:
    candidate_id: str
    source_id: str
    source_kind: str
    title: str
    url: str
    provider: str | None = None
    model_id: str | None = None
    description: str = ""
    free_status: str = "unknown"
    terms_status: str = "unknown"
    review_status: str = "manual_review"
    requires_auth: bool = False
    score: float = 0.0
    discovered_at: str = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.source_id or not self.title:
            raise ValueError("search_candidate_identity_required")
        if self.source_id not in SEARCH_SOURCES:
            raise ValueError("search_candidate_source_invalid")
        if self.free_status not in FREE_STATUSES:
            raise ValueError("search_candidate_free_status_invalid")
        if self.review_status not in REVIEW_STATUSES:
            raise ValueError("search_candidate_review_status_invalid")
        if not 0 <= float(self.score) <= 1:
            raise ValueError("search_candidate_score_invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchReport:
    query: str
    sources: tuple[str, ...]
    candidates: tuple[SearchCandidate, ...]
    errors: tuple[Mapping[str, str], ...] = ()
    local_first: bool = True
    w1_owned_server_required: bool = False
    generated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "sources": list(self.sources),
            "candidates": [item.as_dict() for item in self.candidates],
            "count": len(self.candidates),
            "errors": [dict(item) for item in self.errors],
            "local_first": self.local_first,
            "w1_owned_server_required": self.w1_owned_server_required,
            "generated_at": self.generated_at,
        }


class SearchAdapter(Protocol):
    source_id: str

    def search(self, query: str, *, limit: int, client: "PublicJSONClient") -> Sequence[SearchCandidate]:
        ...


class PublicJSONClient:
    """Small HTTPS JSON client restricted to known public catalog hosts."""

    def __init__(self, *, timeout_seconds: float = _DEFAULT_TIMEOUT, opener: Any = urllib.request.urlopen) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.opener = opener

    def get_json(self, url: str, *, headers: Mapping[str, str] | None = None) -> Any:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
            raise IntelligenceSearchNetworkError("intelligence_search_host_not_allowed")
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "W1-Nexus-Intelligence-Search/1.0",
        }
        request_headers.update(dict(headers or {}))
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise IntelligenceSearchResponseError("intelligence_search_response_too_large")
            return json.loads(raw.decode("utf-8"))
        except IntelligenceSearchError:
            raise
        except urllib.error.HTTPError as exc:
            raise IntelligenceSearchNetworkError(f"http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise IntelligenceSearchNetworkError(type(exc).__name__) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntelligenceSearchResponseError("intelligence_search_json_invalid") from exc


class OpenRouterCatalogAdapter:
    source_id = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/models"

    @staticmethod
    def _zero(value: Any) -> bool:
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def search(self, query: str, *, limit: int, client: PublicJSONClient) -> Sequence[SearchCandidate]:
        payload = client.get_json(self.endpoint)
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise IntelligenceSearchResponseError("openrouter_models_missing")
        tokens = [token for token in query.casefold().split() if len(token) >= 3 and token not in {"free", "model", "models", "api", "llm", "ai"}]
        candidates: list[SearchCandidate] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            model_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or model_id).strip()
            description = str(row.get("description") or "")
            haystack = " ".join((model_id, name, description)).casefold()
            if tokens and not any(token in haystack for token in tokens):
                continue
            pricing = row.get("pricing") if isinstance(row.get("pricing"), Mapping) else {}
            explicit_free = model_id.endswith(":free")
            zero_priced = self._zero(pricing.get("prompt")) and self._zero(pricing.get("completion"))
            if not (explicit_free or zero_priced):
                continue
            free_status = "verified_free" if (explicit_free or zero_priced) else "unknown"
            score = 0.96 if explicit_free else 0.92
            candidates.append(SearchCandidate(
                candidate_id="or-" + hashlib.sha256(model_id.encode()).hexdigest()[:20],
                source_id=self.source_id,
                source_kind="hosted_model",
                title=name or model_id,
                url=f"https://openrouter.ai/{model_id}",
                provider="OpenRouter",
                model_id=model_id,
                description=description[:500],
                free_status=free_status,
                terms_status="verified_third_party",
                review_status="connectable",
                requires_auth=True,
                score=score,
                metadata={
                    "pricing": dict(pricing),
                    "context_length": row.get("context_length"),
                    "architecture": row.get("architecture"),
                    "source_api": self.endpoint,
                    "entitlement_inferred": False,
                },
            ))
            if len(candidates) >= limit:
                break
        return candidates


class GitHubRepositorySearchAdapter:
    source_id = "github"
    endpoint = "https://api.github.com/search/repositories"

    def search(self, query: str, *, limit: int, client: PublicJSONClient) -> Sequence[SearchCandidate]:
        phrase = (query.strip() or "free llm api")
        # Qualifiers bias discovery toward active API/gateway projects without claiming trust.
        search_query = f"{phrase} (llm OR ai) api gateway in:name,description,readme"
        url = self.endpoint + "?" + urllib.parse.urlencode({"q": search_query, "sort": "updated", "order": "desc", "per_page": min(limit, 30)})
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = client.get_json(url, headers=headers)
        rows = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise IntelligenceSearchResponseError("github_search_items_missing")
        candidates: list[SearchCandidate] = []
        keywords = {"free", "llm", "ai", "api", "gateway", "openai", "router", "model"}
        for row in rows[:limit]:
            if not isinstance(row, Mapping):
                continue
            full_name = str(row.get("full_name") or "").strip()
            if not full_name:
                continue
            description = str(row.get("description") or "")
            text = f"{full_name} {description}".casefold()
            matches = sum(1 for word in keywords if word in text)
            score = min(0.82, 0.28 + matches * 0.07)
            license_info = row.get("license") if isinstance(row.get("license"), Mapping) else {}
            candidates.append(SearchCandidate(
                candidate_id="gh-" + hashlib.sha256(full_name.encode()).hexdigest()[:20],
                source_id=self.source_id,
                source_kind="gateway_project",
                title=full_name,
                url=str(row.get("html_url") or f"https://github.com/{full_name}"),
                provider="GitHub",
                description=description[:500],
                free_status="free_marker" if "free" in text else "unknown",
                terms_status="unknown",
                review_status="manual_review",
                requires_auth=False,
                score=score,
                metadata={
                    "stars": int(row.get("stargazers_count") or 0),
                    "forks": int(row.get("forks_count") or 0),
                    "updated_at": row.get("updated_at"),
                    "archived": bool(row.get("archived", False)),
                    "license": license_info.get("spdx_id"),
                    "source_api": self.endpoint,
                    "github_token_used": bool(token),
                    "trust_inferred_from_stars": False,
                },
            ))
        return candidates


class HuggingFaceModelSearchAdapter:
    source_id = "huggingface"
    endpoint = "https://huggingface.co/api/models"

    def search(self, query: str, *, limit: int, client: PublicJSONClient) -> Sequence[SearchCandidate]:
        params = {
            "search": query.strip() or "instruct",
            "pipeline_tag": "text-generation",
            "sort": "downloads",
            "direction": "-1",
            "limit": min(limit, 50),
            "full": "true",
        }
        payload = client.get_json(self.endpoint + "?" + urllib.parse.urlencode(params))
        if not isinstance(payload, list):
            raise IntelligenceSearchResponseError("huggingface_models_missing")
        candidates: list[SearchCandidate] = []
        for row in payload[:limit]:
            if not isinstance(row, Mapping):
                continue
            model_id = str(row.get("id") or row.get("modelId") or "").strip()
            if not model_id:
                continue
            tags = [str(x) for x in row.get("tags", []) if isinstance(x, (str, int, float))]
            license_tag = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), None)
            candidates.append(SearchCandidate(
                candidate_id="hf-" + hashlib.sha256(model_id.encode()).hexdigest()[:20],
                source_id=self.source_id,
                source_kind="open_model_repository",
                title=model_id,
                url=f"https://huggingface.co/{model_id}",
                provider="Hugging Face Hub",
                model_id=model_id,
                description="Open model repository candidate for local/self-hosted use. Availability in a repository does not mean free hosted inference.",
                free_status="open_weights_candidate",
                terms_status="verified_third_party" if license_tag else "unknown",
                review_status="local_candidate",
                requires_auth=False,
                score=0.72 if license_tag else 0.62,
                metadata={
                    "downloads": int(row.get("downloads") or 0),
                    "likes": int(row.get("likes") or 0),
                    "pipeline_tag": row.get("pipeline_tag"),
                    "license": license_tag,
                    "tags": tags[:30],
                    "source_api": self.endpoint,
                    "hosted_inference_free_inferred": False,
                },
            ))
        return candidates


DEFAULT_ADAPTERS: tuple[SearchAdapter, ...] = (
    OpenRouterCatalogAdapter(),
    GitHubRepositorySearchAdapter(),
    HuggingFaceModelSearchAdapter(),
)


class IntelligenceSearchStore:
    """Redaction-safe local cache of public search candidates."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intelligence_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intelligence_candidates_updated
                    ON intelligence_candidates(updated_at DESC);
                """
            )

    def put_many(self, candidates: Iterable[SearchCandidate]) -> None:
        now = utc_now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for candidate in candidates:
                    payload = json.dumps(candidate.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    connection.execute(
                        "INSERT INTO intelligence_candidates(candidate_id,payload_json,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(candidate_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                        (candidate.candidate_id, payload, now),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get(self, candidate_id: str) -> SearchCandidate | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM intelligence_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            return None
        return SearchCandidate(**json.loads(row["payload_json"]))

    def list(self, *, limit: int = 100, source_id: str | None = None) -> list[SearchCandidate]:
        cap = max(1, min(int(limit), 1000))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM intelligence_candidates ORDER BY updated_at DESC LIMIT ?",
                (max(cap * 4, cap),),
            ).fetchall()
        items = [SearchCandidate(**json.loads(row["payload_json"])) for row in rows]
        if source_id:
            items = [item for item in items if item.source_id == source_id]
        return items[:cap]

    def clear(self) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM intelligence_candidates").fetchone()
            count = int(row["n"] if row else 0)
            connection.execute("DELETE FROM intelligence_candidates")
        return count


class IntelligenceSearchEngine:
    """Federated, on-demand search performed entirely by the local W1 process."""

    def __init__(
        self,
        store: IntelligenceSearchStore,
        *,
        adapters: Iterable[SearchAdapter] = DEFAULT_ADAPTERS,
        client: PublicJSONClient | None = None,
    ) -> None:
        self.store = store
        self.adapters = {adapter.source_id: adapter for adapter in adapters}
        self.client = client or PublicJSONClient()

    def search(self, query: str, *, sources: Iterable[str] | None = None, limit_per_source: int = 12) -> SearchReport:
        selected = tuple(dict.fromkeys(sources or self.adapters.keys()))
        unknown = set(selected) - set(self.adapters)
        if unknown:
            raise ValueError("unknown_intelligence_search_source:" + ",".join(sorted(unknown)))
        limit = max(1, min(int(limit_per_source), 50))
        candidates: list[SearchCandidate] = []
        errors: list[Mapping[str, str]] = []
        for source_id in selected:
            adapter = self.adapters[source_id]
            try:
                found = list(adapter.search(query, limit=limit, client=self.client))
                candidates.extend(found)
            except Exception as exc:
                errors.append({"source_id": source_id, "error": getattr(exc, "code", type(exc).__name__)})
        # Stable de-duplication and quality ordering across sources.
        deduped: dict[str, SearchCandidate] = {}
        for item in candidates:
            prior = deduped.get(item.candidate_id)
            if prior is None or item.score > prior.score:
                deduped[item.candidate_id] = item
        ordered = tuple(sorted(deduped.values(), key=lambda item: (-item.score, item.source_id, item.title.casefold())))
        self.store.put_many(ordered)
        return SearchReport(query=query, sources=selected, candidates=ordered, errors=tuple(errors))


def search_catalog() -> dict[str, Any]:
    return {
        "version": INTELLIGENCE_SEARCH_VERSION,
        "local_first": True,
        "w1_owned_server_required": False,
        "background_cloud_index": False,
        "on_demand": True,
        "cache": "workspace-local SQLite",
        "sources": [
            {
                "source_id": "openrouter",
                "kind": "public_model_catalog",
                "description": "Search free model variants from OpenRouter's public model catalog. Connection/auth remains user-controlled.",
                "auto_adopt": False,
            },
            {
                "source_id": "github",
                "kind": "repository_search",
                "description": "Discover open-source gateways/routers through GitHub Repository Search. Results always require manual review.",
                "auto_adopt": False,
            },
            {
                "source_id": "huggingface",
                "kind": "open_model_catalog",
                "description": "Discover open model repositories for local/self-hosted use. Hosted inference entitlement is never inferred.",
                "auto_adopt": False,
            },
        ],
        "security": {
            "https_only": True,
            "hardcoded_host_allowlist": sorted(_ALLOWED_HOSTS),
            "browser_cookie_import": False,
            "credentials_cached": False,
            "third_party_auto_routing": False,
        },
        "activation_gate": {
            "automatic_activation": False,
            "openrouter_requires_existing_connection": True,
            "openrouter_requires_persisted_model_discovery_match": True,
            "github_direct_activation": False,
            "huggingface_direct_activation": False,
            "default_third_party_free_routing": False,
            "raw_credentials_persisted": False,
        },
    }


def run_intelligence_search_benchmark() -> dict[str, Any]:
    """Deterministic no-network benchmark for local storage, ranking, and guardrails."""
    import tempfile

    class DemoAdapter:
        source_id = "openrouter"

        def search(self, query: str, *, limit: int, client: PublicJSONClient) -> Sequence[SearchCandidate]:
            return [
                SearchCandidate(
                    candidate_id="demo-free", source_id="openrouter", source_kind="hosted_model",
                    title="Demo Free Model", url="https://openrouter.ai/demo/free", provider="OpenRouter",
                    model_id="demo/model:free", free_status="verified_free", terms_status="verified_third_party",
                    review_status="connectable", requires_auth=True, score=0.95,
                    metadata={"entitlement_inferred": False},
                )
            ]

    with tempfile.TemporaryDirectory(prefix="w1-intelligence-search-") as directory:
        store = IntelligenceSearchStore(Path(directory) / "search.sqlite3")
        engine = IntelligenceSearchEngine(store, adapters=(DemoAdapter(),), client=PublicJSONClient())
        report = engine.search("demo", sources=("openrouter",))
        cached = store.list()
        catalog = search_catalog()
        probes = {
            "search_returns_candidate": report.count if hasattr(report, "count") else len(report.candidates),
            "free_candidate_preserved": bool(report.candidates and report.candidates[0].free_status == "verified_free"),
            "entitlement_not_inferred": bool(report.candidates and report.candidates[0].metadata.get("entitlement_inferred") is False),
            "cache_local": len(cached) == 1,
            "no_w1_server_required": catalog["w1_owned_server_required"] is False,
            "no_background_cloud_index": catalog["background_cloud_index"] is False,
            "cookies_not_imported": catalog["security"]["browser_cookie_import"] is False,
            "third_party_not_auto_routed": catalog["security"]["third_party_auto_routing"] is False,
        }
        probes["search_returns_candidate"] = probes["search_returns_candidate"] == 1
        return {"passed": all(bool(v) for v in probes.values()), "probes": probes, "candidate_count": len(report.candidates)}
