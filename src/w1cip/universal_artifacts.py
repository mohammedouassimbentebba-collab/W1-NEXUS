"""Universal, local-first office artifact engine for W1 Nexus.

The engine stores one canonical JSON model for documents, spreadsheets, and
presentations.  Model-generated edits are represented as reviewable JSON
Pointer patches.  Exports are deterministic best-effort office files written
through :mod:`w1cip.action_runtime`, so binary publication follows the same
approval, backup, and audit rules as source-code changes.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from .action_runtime import ActionApproval, ActionRequest, ActionRuntime

ARTIFACT_SCHEMA_VERSION = "w1-artifact-0.1"
ARTIFACT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHEET_NAME_FORBIDDEN = re.compile(r"[\\/*?:\[\]]")
SUPPORTED_KINDS = {"document", "spreadsheet", "presentation"}
SUPPORTED_EXPORTS = {
    "document": {"docx", "pdf", "json"},
    "spreadsheet": {"xlsx", "pdf", "json"},
    "presentation": {"pptx", "pdf", "json"},
}
MAX_JSON_BYTES = 8_000_000
MAX_PATCH_OPERATIONS = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class UniversalArtifactError(RuntimeError):
    code = "universal_artifact_error"


class ArtifactValidationError(UniversalArtifactError):
    code = "artifact_validation_error"

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


class ArtifactPatchError(UniversalArtifactError):
    code = "artifact_patch_error"


class ArtifactVersionConflict(UniversalArtifactError):
    code = "artifact_version_conflict"


class ArtifactIndependentReviewRequired(UniversalArtifactError):
    code = "artifact_independent_review_required"


class ArtifactExportUnavailable(UniversalArtifactError):
    code = "artifact_export_unavailable"


@dataclass(frozen=True)
class UniversalArtifactRevision:
    artifact_id: str
    version: int
    kind: str
    title: str
    model: dict[str, Any]
    model_hash: str
    parent_hash: str | None
    patch: tuple[dict[str, Any], ...]
    created_by: str
    created_at: str
    status: str

    def as_dict(self, *, include_model: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["patch"] = [dict(item) for item in self.patch]
        if not include_model:
            value.pop("model", None)
        return value


@dataclass(frozen=True)
class ArtifactExportRecord:
    export_id: str
    artifact_id: str
    version: int
    format: str
    output_path: str
    output_hash: str
    output_size: int
    exported_by: str
    exported_at: str
    action_id: str
    validation: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any, *, maximum: int = 200_000) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    if len(value) > maximum:
        raise ValueError("text exceeds limit")
    return value


def validate_artifact_model(model: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the canonical W1 artifact model."""

    errors: list[str] = []
    if not isinstance(model, Mapping):
        raise ArtifactValidationError(("artifact must be a JSON object",))
    normalized = copy.deepcopy(dict(model))
    if normalized.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ARTIFACT_SCHEMA_VERSION}")
    artifact_id = normalized.get("artifact_id")
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
        errors.append("artifact_id must be lowercase kebab-case")
    kind = normalized.get("kind")
    if kind not in SUPPORTED_KINDS:
        errors.append("kind must be document, spreadsheet, or presentation")
    title = normalized.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        errors.append("title must be a non-empty string of at most 300 characters")
    locale = normalized.get("locale", "en")
    if not isinstance(locale, str) or len(locale) > 35:
        errors.append("locale must be a short language tag")
    normalized["locale"] = locale
    metadata = normalized.get("metadata", {})
    if not isinstance(metadata, Mapping):
        errors.append("metadata must be an object")
    else:
        normalized["metadata"] = dict(metadata)
    content = normalized.get("content")
    if not isinstance(content, Mapping):
        errors.append("content must be an object")
    elif kind == "document":
        _validate_document(dict(content), errors)
    elif kind == "spreadsheet":
        _validate_spreadsheet(dict(content), errors)
    elif kind == "presentation":
        _validate_presentation(dict(content), errors)
    try:
        size = len(canonical_json(normalized).encode("utf-8"))
        if size > MAX_JSON_BYTES:
            errors.append(f"canonical artifact exceeds {MAX_JSON_BYTES} bytes")
    except (TypeError, ValueError):
        errors.append("artifact must contain JSON-serializable finite values")
    if errors:
        raise ArtifactValidationError(errors)
    return normalized


def _validate_document(content: dict[str, Any], errors: list[str]) -> None:
    blocks = content.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("document.content.blocks must be a non-empty array")
        return
    if len(blocks) > 5_000:
        errors.append("document has too many blocks")
        return
    allowed = {"heading", "paragraph", "bullet_list", "numbered_list", "table", "page_break", "callout"}
    for index, block in enumerate(blocks):
        prefix = f"document block {index}"
        if not isinstance(block, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        block_type = block.get("type")
        if block_type not in allowed:
            errors.append(f"{prefix} has unsupported type")
            continue
        if block_type == "heading":
            if not isinstance(block.get("text"), str):
                errors.append(f"{prefix}.text must be string")
            if block.get("level", 1) not in {1, 2, 3, 4, 5, 6}:
                errors.append(f"{prefix}.level must be 1-6")
        elif block_type in {"paragraph", "callout"}:
            if not isinstance(block.get("text"), str):
                errors.append(f"{prefix}.text must be string")
        elif block_type in {"bullet_list", "numbered_list"}:
            items = block.get("items")
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                errors.append(f"{prefix}.items must be a string array")
        elif block_type == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows:
                errors.append(f"{prefix}.rows must be non-empty")
            elif not all(isinstance(row, list) for row in rows):
                errors.append(f"{prefix}.rows must contain arrays")
            else:
                width = len(rows[0])
                if width < 1 or any(len(row) != width for row in rows):
                    errors.append(f"{prefix}.rows must be rectangular")


def _validate_spreadsheet(content: dict[str, Any], errors: list[str]) -> None:
    sheets = content.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        errors.append("spreadsheet.content.sheets must be a non-empty array")
        return
    if len(sheets) > 100:
        errors.append("spreadsheet has too many sheets")
        return
    names: set[str] = set()
    for index, sheet in enumerate(sheets):
        prefix = f"sheet {index}"
        if not isinstance(sheet, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        name = sheet.get("name")
        if not isinstance(name, str) or not name or len(name) > 31 or SHEET_NAME_FORBIDDEN.search(name):
            errors.append(f"{prefix}.name is invalid for XLSX")
        elif name.casefold() in names:
            errors.append(f"duplicate sheet name {name}")
        else:
            names.add(name.casefold())
        rows = sheet.get("rows", [])
        if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
            errors.append(f"{prefix}.rows must contain arrays")
        elif len(rows) > 100_000:
            errors.append(f"{prefix} has too many rows")
        formulas = sheet.get("formulas", [])
        if not isinstance(formulas, list):
            errors.append(f"{prefix}.formulas must be an array")
        else:
            for formula in formulas:
                if not isinstance(formula, Mapping) or not isinstance(formula.get("cell"), str) or not str(formula.get("formula", "")).startswith("="):
                    errors.append(f"{prefix} formula entries require cell and =formula")
        charts = sheet.get("charts", [])
        if not isinstance(charts, list):
            errors.append(f"{prefix}.charts must be an array")


def _validate_presentation(content: dict[str, Any], errors: list[str]) -> None:
    slides = content.get("slides")
    if not isinstance(slides, list) or not slides:
        errors.append("presentation.content.slides must be a non-empty array")
        return
    if len(slides) > 1_000:
        errors.append("presentation has too many slides")
        return
    allowed = {"text", "table", "shape", "chart"}
    for index, slide in enumerate(slides):
        prefix = f"slide {index}"
        if not isinstance(slide, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(slide.get("title", ""), str):
            errors.append(f"{prefix}.title must be string")
        elements = slide.get("elements", [])
        if not isinstance(elements, list):
            errors.append(f"{prefix}.elements must be an array")
            continue
        for element_index, element in enumerate(elements):
            ep = f"{prefix} element {element_index}"
            if not isinstance(element, Mapping) or element.get("type") not in allowed:
                errors.append(f"{ep} has unsupported type")
                continue
            for key in ("x", "y", "w", "h"):
                if key in element and not isinstance(element[key], (int, float)):
                    errors.append(f"{ep}.{key} must be numeric")


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _pointer_parent(document: Any, pointer: str) -> tuple[Any, str]:
    if not pointer.startswith("/"):
        raise ArtifactPatchError("JSON Pointer must start with /")
    tokens = [_decode_pointer_token(item) for item in pointer.split("/")[1:]]
    if not tokens:
        raise ArtifactPatchError("root replacement is not supported")
    current = document
    for token in tokens[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ArtifactPatchError(f"invalid list path {pointer}") from exc
        elif isinstance(current, MutableMapping):
            if token not in current:
                raise ArtifactPatchError(f"missing path component {token}")
            current = current[token]
        else:
            raise ArtifactPatchError(f"cannot traverse {pointer}")
    return current, tokens[-1]


def _pointer_value(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    parent, token = _pointer_parent(document, pointer)
    if isinstance(parent, list):
        try:
            return parent[int(token)]
        except (ValueError, IndexError) as exc:
            raise ArtifactPatchError(f"invalid path {pointer}") from exc
    if isinstance(parent, Mapping) and token in parent:
        return parent[token]
    raise ArtifactPatchError(f"path not found {pointer}")


def apply_json_patch(model: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply a safe RFC-6902 subset: add, replace, remove, and test."""

    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise ArtifactPatchError("patch must be an array")
    if len(operations) > MAX_PATCH_OPERATIONS:
        raise ArtifactPatchError("patch contains too many operations")
    changed: Any = copy.deepcopy(dict(model))
    for index, raw in enumerate(operations):
        if not isinstance(raw, Mapping):
            raise ArtifactPatchError(f"patch operation {index} must be an object")
        operation = raw.get("op")
        path = raw.get("path")
        if operation not in {"add", "replace", "remove", "test"} or not isinstance(path, str):
            raise ArtifactPatchError(f"patch operation {index} is invalid")
        if path in {"/artifact_id", "/schema_version", "/kind"} and operation != "test":
            raise ArtifactPatchError(f"identity field {path} is immutable")
        if operation == "test":
            if _pointer_value(changed, path) != raw.get("value"):
                raise ArtifactPatchError(f"test operation failed at {path}")
            continue
        parent, token = _pointer_parent(changed, path)
        if isinstance(parent, list):
            if token == "-" and operation == "add":
                parent.append(copy.deepcopy(raw.get("value")))
                continue
            try:
                offset = int(token)
            except ValueError as exc:
                raise ArtifactPatchError(f"invalid list index {token}") from exc
            if operation == "add":
                if offset < 0 or offset > len(parent):
                    raise ArtifactPatchError(f"list insertion out of range at {path}")
                parent.insert(offset, copy.deepcopy(raw.get("value")))
            elif operation == "replace":
                if offset < 0 or offset >= len(parent):
                    raise ArtifactPatchError(f"list index out of range at {path}")
                parent[offset] = copy.deepcopy(raw.get("value"))
            else:
                if offset < 0 or offset >= len(parent):
                    raise ArtifactPatchError(f"list index out of range at {path}")
                parent.pop(offset)
        elif isinstance(parent, MutableMapping):
            if operation == "add":
                parent[token] = copy.deepcopy(raw.get("value"))
            elif operation == "replace":
                if token not in parent:
                    raise ArtifactPatchError(f"replace target missing at {path}")
                parent[token] = copy.deepcopy(raw.get("value"))
            else:
                if token not in parent:
                    raise ArtifactPatchError(f"remove target missing at {path}")
                del parent[token]
        else:
            raise ArtifactPatchError(f"patch target is not a container at {path}")
    return validate_artifact_model(changed)


class UniversalArtifactStore:
    """Append-only artifact models, patches, reviews, exports, and hash-chain."""

    def __init__(self, db_path: str | Path, workspace_root: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._initialize()

    def __enter__(self) -> "UniversalArtifactStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS office_artifacts (
                artifact_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                current_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS office_revisions (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                model_json TEXT NOT NULL,
                model_hash TEXT NOT NULL,
                parent_hash TEXT,
                patch_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (artifact_id, version)
            );
            CREATE TABLE IF NOT EXISTS office_reviews (
                review_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                reviewer TEXT NOT NULL,
                outcome TEXT NOT NULL,
                rationale TEXT NOT NULL,
                model_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS office_exports (
                export_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                format TEXT NOT NULL,
                output_path TEXT NOT NULL,
                output_hash TEXT NOT NULL,
                output_size INTEGER NOT NULL,
                exported_by TEXT NOT NULL,
                exported_at TEXT NOT NULL,
                action_id TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                UNIQUE(artifact_id, version, format, output_path, output_hash)
            );
            CREATE TABLE IF NOT EXISTS office_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _append_event(self, event_type: str, subject_id: str, payload: Mapping[str, Any]) -> None:
        previous_row = self._connection.execute(
            "SELECT event_hash FROM office_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = previous_row["event_hash"] if previous_row else None
        recorded = utc_now()
        core = {
            "event_type": event_type,
            "subject_id": subject_id,
            "payload": dict(payload),
            "previous_hash": previous,
            "recorded_at": recorded,
        }
        digest = sha256_bytes(canonical_json(core).encode("utf-8"))
        self._connection.execute(
            "INSERT INTO office_events(event_type,subject_id,payload_json,previous_hash,event_hash,recorded_at) VALUES(?,?,?,?,?,?)",
            (event_type, subject_id, canonical_json(dict(payload)), previous, digest, recorded),
        )

    def create(self, model: Mapping[str, Any], *, created_by: str) -> UniversalArtifactRevision:
        normalized = validate_artifact_model(model)
        artifact_id = str(normalized["artifact_id"])
        digest = sha256_bytes(canonical_json(normalized).encode("utf-8"))
        created = utc_now()
        revision = UniversalArtifactRevision(
            artifact_id=artifact_id,
            version=1,
            kind=str(normalized["kind"]),
            title=str(normalized["title"]),
            model=normalized,
            model_hash=digest,
            parent_hash=None,
            patch=(),
            created_by=created_by,
            created_at=created,
            status="draft",
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO office_artifacts(artifact_id,kind,title,current_version,status,created_by,created_at,updated_at) VALUES(?,?,?,1,'draft',?,?,?)",
                (artifact_id, revision.kind, revision.title, created_by, created, created),
            )
            self._insert_revision(revision)
            self._append_event("office.artifact.created", artifact_id, revision.as_dict(include_model=False))
        return revision

    def patch(
        self,
        artifact_id: str,
        operations: Sequence[Mapping[str, Any]],
        *,
        created_by: str,
        expected_parent_hash: str,
    ) -> UniversalArtifactRevision:
        current = self.current(artifact_id)
        if current.model_hash != expected_parent_hash:
            raise ArtifactVersionConflict("artifact changed since the patch was prepared")
        updated = apply_json_patch(current.model, operations)
        digest = sha256_bytes(canonical_json(updated).encode("utf-8"))
        if digest == current.model_hash:
            return current
        created = utc_now()
        revision = UniversalArtifactRevision(
            artifact_id=artifact_id,
            version=current.version + 1,
            kind=current.kind,
            title=str(updated["title"]),
            model=updated,
            model_hash=digest,
            parent_hash=current.model_hash,
            patch=tuple(dict(item) for item in operations),
            created_by=created_by,
            created_at=created,
            status="draft",
        )
        with self._lock, self._connection:
            self._insert_revision(revision)
            self._connection.execute(
                "UPDATE office_artifacts SET title=?,current_version=?,status='draft',updated_at=? WHERE artifact_id=?",
                (revision.title, revision.version, created, artifact_id),
            )
            self._append_event("office.artifact.patched", artifact_id, revision.as_dict(include_model=False))
        return revision

    def review(
        self,
        artifact_id: str,
        *,
        version: int,
        reviewer: str,
        outcome: str,
        rationale: str,
    ) -> dict[str, Any]:
        if outcome not in {"approved", "changes_requested", "rejected"}:
            raise ValueError("invalid review outcome")
        selected = self.revision(artifact_id, version)
        current = self.current(artifact_id)
        if selected.version != current.version:
            raise ArtifactVersionConflict("only the current revision can be reviewed")
        if selected.created_by == reviewer:
            raise ArtifactIndependentReviewRequired("the author cannot independently approve the revision")
        if not rationale.strip():
            raise ValueError("review rationale is required")
        created = utc_now()
        review_id = "office-review-" + sha256_bytes(
            canonical_json([artifact_id, version, reviewer, outcome, selected.model_hash, created]).encode("utf-8")
        )[:20]
        payload = {
            "review_id": review_id,
            "artifact_id": artifact_id,
            "version": version,
            "reviewer": reviewer,
            "outcome": outcome,
            "rationale": rationale,
            "model_hash": selected.model_hash,
            "created_at": created,
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO office_reviews(review_id,artifact_id,version,reviewer,outcome,rationale,model_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (review_id, artifact_id, version, reviewer, outcome, rationale, selected.model_hash, created),
            )
            status = "reviewed" if outcome == "approved" else "changes_requested"
            self._connection.execute(
                "UPDATE office_artifacts SET status=?,updated_at=? WHERE artifact_id=?",
                (status, created, artifact_id),
            )
            self._append_event("office.review.recorded", artifact_id, payload)
        return payload

    def export(
        self,
        artifact_id: str,
        *,
        format: str,
        output_path: str,
        exported_by: str,
        action_runtime: ActionRuntime,
        approval: ActionApproval | Mapping[str, Any] | None = None,
        issue_action_approval: bool = False,
    ) -> ArtifactExportRecord:
        revision = self.current(artifact_id)
        selected_format = format.lower().lstrip(".")
        if selected_format not in SUPPORTED_EXPORTS[revision.kind]:
            raise ArtifactExportUnavailable(f"{revision.kind} cannot export {selected_format}")
        review = self._approved_review(revision)
        if review is None:
            raise ArtifactIndependentReviewRequired("current revision requires independent approval before export")
        output = Path(output_path.replace("\\", "/"))
        if output.is_absolute() or ".." in output.parts or not output.parts:
            raise ArtifactExportUnavailable("output_path must be workspace-relative")
        if output.parts[0] in {".git", ".w1nexus"}:
            raise ArtifactExportUnavailable("output_path cannot target protected W1 or Git state")
        expected_suffix = "." + selected_format
        if output.suffix.lower() != expected_suffix:
            raise ArtifactExportUnavailable(f"output_path must end with {expected_suffix}")
        data = render_artifact(revision.model, selected_format)
        validation = validate_export_bytes(data, selected_format)
        if not validation["valid"]:
            raise ArtifactExportUnavailable("generated export failed structural validation")
        digest = sha256_bytes(data)
        existing = self._connection.execute(
            "SELECT * FROM office_exports WHERE artifact_id=? AND version=? AND format=? AND output_path=? AND output_hash=?",
            (artifact_id, revision.version, selected_format, output.as_posix(), digest),
        ).fetchone()
        if existing is not None:
            return self._record_from_row(existing)
        destination_key = sha256_bytes(output.as_posix().encode("utf-8"))[:12]
        action_id = f"office-export-{artifact_id}-v{revision.version}-{selected_format}-{destination_key}"
        staging_root = action_runtime.state_dir / "office-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_name = f"{action_id}-{digest[:16]}.bin"
        staging = staging_root / staging_name
        fd, temporary_name = tempfile.mkstemp(prefix=".w1-office-stage-", dir=str(staging_root))
        try:
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, staging)
            try:
                os.chmod(staging, 0o600)
            except OSError:
                pass
            request = ActionRequest(
                action_id=action_id,
                kind="file.publish_staged",
                parameters={
                    "path": output.as_posix(),
                    "staging_path": staging.relative_to(action_runtime.state_dir).as_posix(),
                    "sha256": digest,
                },
                requested_by=exported_by,
                reason=f"Publish reviewed {revision.kind} {artifact_id} as {selected_format}",
            )
            plan = action_runtime.plan(request)
            selected_approval = approval
            if plan.requires_approval and selected_approval is None and issue_action_approval:
                selected_approval = action_runtime.issue_approval(plan, issued_by=exported_by)
            result = action_runtime.execute(request, approval=selected_approval)
            # First execution consumes the staging file.  An idempotent retry
            # may return the prior result before dispatch, so remove any
            # restaged copy here as well.
            staging.unlink(missing_ok=True)
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        stored = self.workspace_root / output
        stored_digest = sha256_bytes(stored.read_bytes())
        if stored_digest != digest:
            raise ArtifactExportUnavailable("published export digest mismatch")
        exported_at = utc_now()
        export_id = "office-export-" + sha256_bytes(
            canonical_json([artifact_id, revision.version, selected_format, output.as_posix(), digest]).encode("utf-8")
        )[:20]
        record = ArtifactExportRecord(
            export_id=export_id,
            artifact_id=artifact_id,
            version=revision.version,
            format=selected_format,
            output_path=output.as_posix(),
            output_hash=digest,
            output_size=len(data),
            exported_by=exported_by,
            exported_at=exported_at,
            action_id=action_id,
            validation=validation,
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO office_exports(export_id,artifact_id,version,format,output_path,output_hash,output_size,exported_by,exported_at,action_id,validation_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (export_id, artifact_id, revision.version, selected_format, output.as_posix(), digest, len(data), exported_by, exported_at, action_id, canonical_json(validation)),
            )
            self._connection.execute(
                "UPDATE office_artifacts SET status='exported',updated_at=? WHERE artifact_id=?",
                (exported_at, artifact_id),
            )
            self._append_event("office.artifact.exported", artifact_id, {**record.as_dict(), "changed_paths": list(result.changed_paths)})
        return record

    def list_artifacts(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT artifact_id,kind,title,current_version,status,created_by,created_at,updated_at FROM office_artifacts ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def current(self, artifact_id: str) -> UniversalArtifactRevision:
        row = self._connection.execute(
            "SELECT current_version FROM office_artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self.revision(artifact_id, int(row["current_version"]))

    def revision(self, artifact_id: str, version: int) -> UniversalArtifactRevision:
        row = self._connection.execute(
            "SELECT a.kind,a.title,r.* FROM office_revisions r JOIN office_artifacts a USING(artifact_id) WHERE r.artifact_id=? AND r.version=?",
            (artifact_id, int(version)),
        ).fetchone()
        if row is None:
            raise KeyError(f"{artifact_id}@{version}")
        return UniversalArtifactRevision(
            artifact_id=row["artifact_id"],
            version=int(row["version"]),
            kind=row["kind"],
            title=json.loads(row["model_json"])["title"],
            model=json.loads(row["model_json"]),
            model_hash=row["model_hash"],
            parent_hash=row["parent_hash"],
            patch=tuple(json.loads(row["patch_json"])),
            created_by=row["created_by"],
            created_at=row["created_at"],
            status=row["status"],
        )

    def history(self, artifact_id: str) -> dict[str, Any]:
        current = self.current(artifact_id)
        revisions = self._connection.execute(
            "SELECT version,model_hash,parent_hash,patch_json,created_by,created_at,status FROM office_revisions WHERE artifact_id=? ORDER BY version DESC",
            (artifact_id,),
        ).fetchall()
        reviews = self._connection.execute(
            "SELECT * FROM office_reviews WHERE artifact_id=? ORDER BY created_at", (artifact_id,)
        ).fetchall()
        exports = self._connection.execute(
            "SELECT * FROM office_exports WHERE artifact_id=? ORDER BY exported_at", (artifact_id,)
        ).fetchall()
        return {
            "artifact_id": artifact_id,
            "current": current.as_dict(),
            "revisions": [{**dict(row), "patch": json.loads(row["patch_json"])} for row in revisions],
            "reviews": [dict(row) for row in reviews],
            "exports": [{**dict(row), "validation": json.loads(row["validation_json"])} for row in exports],
        }

    def verify(self) -> dict[str, Any]:
        errors: list[str] = []
        for artifact in self.list_artifacts(limit=100_000):
            previous: str | None = None
            rows = self._connection.execute(
                "SELECT version,model_json,model_hash,parent_hash FROM office_revisions WHERE artifact_id=? ORDER BY version",
                (artifact["artifact_id"],),
            ).fetchall()
            for row in rows:
                actual = sha256_bytes(canonical_json(json.loads(row["model_json"])).encode("utf-8"))
                if actual != row["model_hash"]:
                    errors.append(f"model hash mismatch {artifact['artifact_id']}@{row['version']}")
                if row["parent_hash"] != previous:
                    errors.append(f"revision chain mismatch {artifact['artifact_id']}@{row['version']}")
                previous = row["model_hash"]
        previous_event: str | None = None
        events = self._connection.execute(
            "SELECT sequence,event_type,subject_id,payload_json,previous_hash,event_hash,recorded_at FROM office_events ORDER BY sequence"
        ).fetchall()
        for row in events:
            core = {
                "event_type": row["event_type"],
                "subject_id": row["subject_id"],
                "payload": json.loads(row["payload_json"]),
                "previous_hash": row["previous_hash"],
                "recorded_at": row["recorded_at"],
            }
            actual = sha256_bytes(canonical_json(core).encode("utf-8"))
            if row["previous_hash"] != previous_event:
                errors.append(f"event chain mismatch at {row['sequence']}")
            if actual != row["event_hash"]:
                errors.append(f"event hash mismatch at {row['sequence']}")
            previous_event = row["event_hash"]
        return {
            "valid": not errors,
            "errors": errors,
            "artifact_count": len(self.list_artifacts(limit=100_000)),
            "event_count": len(events),
            "last_event_hash": previous_event,
        }

    def _insert_revision(self, revision: UniversalArtifactRevision) -> None:
        self._connection.execute(
            "INSERT INTO office_revisions(artifact_id,version,model_json,model_hash,parent_hash,patch_json,created_by,created_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                revision.artifact_id,
                revision.version,
                canonical_json(revision.model),
                revision.model_hash,
                revision.parent_hash,
                canonical_json([dict(item) for item in revision.patch]),
                revision.created_by,
                revision.created_at,
                revision.status,
            ),
        )

    def _approved_review(self, revision: UniversalArtifactRevision) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM office_reviews WHERE artifact_id=? AND version=? AND outcome='approved' AND model_hash=? ORDER BY created_at DESC LIMIT 1",
            (revision.artifact_id, revision.version, revision.model_hash),
        ).fetchone()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ArtifactExportRecord:
        return ArtifactExportRecord(
            export_id=row["export_id"], artifact_id=row["artifact_id"], version=int(row["version"]),
            format=row["format"], output_path=row["output_path"], output_hash=row["output_hash"],
            output_size=int(row["output_size"]), exported_by=row["exported_by"],
            exported_at=row["exported_at"], action_id=row["action_id"],
            validation=json.loads(row["validation_json"]),
        )


def render_artifact(model: Mapping[str, Any], format: str) -> bytes:
    normalized = validate_artifact_model(model)
    selected_format = format.lower().lstrip(".")
    kind = normalized["kind"]
    if selected_format not in SUPPORTED_EXPORTS[kind]:
        raise ArtifactExportUnavailable(f"{kind} cannot export {selected_format}")
    if selected_format == "json":
        return pretty_json(normalized).encode("utf-8")
    if selected_format == "docx":
        return _render_docx(normalized)
    if selected_format == "xlsx":
        return _render_xlsx(normalized)
    if selected_format == "pptx":
        return _render_pptx(normalized)
    if selected_format == "pdf":
        return _render_pdf(normalized)
    raise ArtifactExportUnavailable(selected_format)


def _render_docx(model: Mapping[str, Any]) -> bytes:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise ArtifactExportUnavailable("DOCX export requires python-docx") from exc

    document = Document()
    document.core_properties.title = str(model["title"])
    section = document.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    normal = document.styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10.5)
    rtl = str(model.get("locale", "")).lower().startswith(("ar", "fa", "he", "ur"))

    def format_paragraph(paragraph: Any) -> None:
        paragraph.paragraph_format.space_after = Pt(6)
        if rtl:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p_pr = paragraph._p.get_or_add_pPr()
            if p_pr.find(qn("w:bidi")) is None:
                p_pr.append(OxmlElement("w:bidi"))
            for run in paragraph.runs:
                run._element.get_or_add_rPr().append(OxmlElement("w:rtl"))

    title = document.add_heading(str(model["title"]), level=0)
    format_paragraph(title)
    for block in model["content"]["blocks"]:
        block_type = block["type"]
        if block_type == "heading":
            paragraph = document.add_heading(str(block.get("text", "")), level=int(block.get("level", 1)))
            format_paragraph(paragraph)
        elif block_type in {"paragraph", "callout"}:
            paragraph = document.add_paragraph(str(block.get("text", "")))
            if block_type == "callout":
                paragraph.style = document.styles["Intense Quote"]
            format_paragraph(paragraph)
        elif block_type in {"bullet_list", "numbered_list"}:
            style = "List Bullet" if block_type == "bullet_list" else "List Number"
            for item in block.get("items", []):
                paragraph = document.add_paragraph(str(item), style=style)
                format_paragraph(paragraph)
        elif block_type == "table":
            rows = block.get("rows", [])
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for row_index, row in enumerate(rows):
                for column_index, value in enumerate(row):
                    table.cell(row_index, column_index).text = "" if value is None else str(value)
                    for paragraph in table.cell(row_index, column_index).paragraphs:
                        format_paragraph(paragraph)
                        if row_index == 0:
                            for run in paragraph.runs:
                                run.bold = True
        elif block_type == "page_break":
            document.add_page_break()
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _render_xlsx(model: Mapping[str, Any]) -> bytes:
    """Render XLSX with artifact_tool when healthy, otherwise use OOXML.

    artifact_tool is the preferred rich spreadsheet backend, but it runs a
    separate WASM/RPC process that can be unavailable on some local systems.
    W1 therefore treats transport/runtime failures as a recoverable backend
    outage rather than making office export depend on a central service or a
    specific binary environment.
    """
    try:
        from artifact_tool import SpreadsheetFile, Workbook

        workbook = Workbook.create()
        for sheet_spec in model["content"]["sheets"]:
            sheet = workbook.worksheets.add(str(sheet_spec["name"]))
            rows = sheet_spec.get("rows", [])
            if rows:
                width = max((len(row) for row in rows), default=1)
                padded = [list(row) + [None] * (width - len(row)) for row in rows]
                sheet.get_range_by_indexes(0, 0, len(padded), width).values = padded
                if sheet_spec.get("header_rows", 1) > 0:
                    header_count = min(int(sheet_spec.get("header_rows", 1)), len(padded))
                    header = sheet.get_range_by_indexes(0, 0, header_count, width)
                    header.format = {
                        "fill": "#111827",
                        "font": {"bold": True, "color": "#FFFFFF"},
                        "horizontal_alignment": "center",
                        "vertical_alignment": "center",
                        "wrap_text": True,
                    }
                    sheet.freeze_panes.freeze_rows(header_count)
                used = sheet.get_range_by_indexes(0, 0, len(padded), width)
                used.format.wrap_text = True
                used.format.autofit_columns()
                for column in range(width):
                    selected = sheet.get_range_by_indexes(0, column, len(padded), 1)
                    text_width = max(
                        (len(str(row[column])) if column < len(row) and row[column] is not None else 0 for row in padded),
                        default=0,
                    )
                    selected.format.column_width = min(max(11, text_width * 1.15 + 2), 40)
            for formula in sheet_spec.get("formulas", []):
                sheet.get_range(str(formula["cell"])).formulas = [[str(formula["formula"])]]
                if formula.get("number_format"):
                    sheet.get_range(str(formula["cell"])).format.number_format = str(formula["number_format"])
            for item in sheet_spec.get("column_widths", []):
                if isinstance(item, Mapping) and item.get("range") and item.get("width"):
                    sheet.get_range(str(item["range"])).format.column_width = min(float(item["width"]), 60)
            for index, chart_spec in enumerate(sheet_spec.get("charts", [])):
                source = sheet.get_range(str(chart_spec["source_range"]))
                chart = sheet.charts.add(str(chart_spec.get("type", "line")), source)
                chart.title_text = str(chart_spec.get("title", f"Chart {index + 1}"))
                chart.has_legend = bool(chart_spec.get("legend", True))
                chart.legend.position = str(chart_spec.get("legend_position", "bottom"))
                chart.set_position(str(chart_spec.get("start_cell", "F2")), str(chart_spec.get("end_cell", "M18")))
        with tempfile.TemporaryDirectory(prefix="w1-xlsx-") as directory:
            selected = Path(directory) / "artifact.xlsx"
            SpreadsheetFile.export_xlsx(workbook).save(str(selected))
            return selected.read_bytes()
    except Exception:
        # The dependency-free path is deliberately deterministic and keeps W1
        # usable in offline/self-hosted installations even when the optional
        # artifact_tool subprocess cannot start or loses its RPC transport.
        return _render_xlsx_minimal(model)


def _render_xlsx_minimal(model: Mapping[str, Any]) -> bytes:
    """Dependency-free OOXML fallback for self-hosted W1 installations.

    The fallback preserves values, formulas, sheets, header styling, and column
    widths. Rich charts are emitted by the artifact_tool backend when present.
    """

    import xml.etree.ElementTree as ET

    def cell_ref(row: int, column: int) -> str:
        value = column + 1
        letters = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{row + 1}"

    def xml_bytes(root: ET.Element) -> bytes:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ET.register_namespace("", ns_main)
    ET.register_namespace("r", ns_rel)
    files: dict[str, bytes] = {}
    sheets = model["content"]["sheets"]
    workbook = ET.Element(f"{{{ns_main}}}workbook")
    workbook_sheets = ET.SubElement(workbook, f"{{{ns_main}}}sheets")
    workbook_rels = ET.Element(
        "Relationships",
        xmlns="http://schemas.openxmlformats.org/package/2006/relationships",
    )
    overrides: list[tuple[str, str]] = []
    for index, sheet_spec in enumerate(sheets, 1):
        ET.SubElement(
            workbook_sheets,
            f"{{{ns_main}}}sheet",
            {
                "name": str(sheet_spec["name"]),
                "sheetId": str(index),
                f"{{{ns_rel}}}id": f"rId{index}",
            },
        )
        ET.SubElement(
            workbook_rels,
            "Relationship",
            {
                "Id": f"rId{index}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{index}.xml",
            },
        )
        worksheet = ET.Element(f"{{{ns_main}}}worksheet")
        rows = sheet_spec.get("rows", [])
        width = max((len(row) for row in rows), default=0)
        if width:
            columns = ET.SubElement(worksheet, f"{{{ns_main}}}cols")
            for column in range(width):
                max_length = max(
                    (
                        len(str(row[column]))
                        if column < len(row) and row[column] is not None
                        else 0
                        for row in rows
                    ),
                    default=0,
                )
                ET.SubElement(
                    columns,
                    f"{{{ns_main}}}col",
                    {
                        "min": str(column + 1),
                        "max": str(column + 1),
                        "width": str(min(max(11, max_length * 1.15 + 2), 40)),
                        "customWidth": "1",
                    },
                )
        sheet_data = ET.SubElement(worksheet, f"{{{ns_main}}}sheetData")
        formula_map = {
            str(item["cell"]): str(item["formula"])[1:]
            for item in sheet_spec.get("formulas", [])
        }
        for row_index, row in enumerate(rows):
            row_node = ET.SubElement(
                sheet_data, f"{{{ns_main}}}row", {"r": str(row_index + 1)}
            )
            for column_index, value in enumerate(row):
                reference = cell_ref(row_index, column_index)
                attrs = {"r": reference}
                if row_index < int(sheet_spec.get("header_rows", 1)):
                    attrs["s"] = "1"
                cell = ET.SubElement(row_node, f"{{{ns_main}}}c", attrs)
                if reference in formula_map:
                    ET.SubElement(cell, f"{{{ns_main}}}f").text = formula_map[reference]
                elif isinstance(value, bool):
                    cell.set("t", "b")
                    ET.SubElement(cell, f"{{{ns_main}}}v").text = "1" if value else "0"
                elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                    ET.SubElement(cell, f"{{{ns_main}}}v").text = str(value)
                elif value is not None:
                    cell.set("t", "inlineStr")
                    inline = ET.SubElement(cell, f"{{{ns_main}}}is")
                    ET.SubElement(inline, f"{{{ns_main}}}t").text = str(value)
        files[f"xl/worksheets/sheet{index}.xml"] = xml_bytes(worksheet)
        overrides.append(
            (
                f"/xl/worksheets/sheet{index}.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            )
        )
    style_id = len(sheets) + 1
    ET.SubElement(
        workbook_rels,
        "Relationship",
        {
            "Id": f"rId{style_id}",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
            "Target": "styles.xml",
        },
    )
    ET.SubElement(
        workbook,
        f"{{{ns_main}}}calcPr",
        {"calcId": "191029", "fullCalcOnLoad": "1", "forceFullCalc": "1"},
    )
    files["xl/workbook.xml"] = xml_bytes(workbook)
    files["xl/_rels/workbook.xml.rels"] = xml_bytes(workbook_rels)
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{ns_main}">'
        '<fonts count="2"><font><sz val="11"/><name val="Aptos"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF111827"/>'
        '<bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    ).encode("utf-8")
    files["xl/styles.xml"] = styles
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    content_types = ET.Element(
        "Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
    )
    ET.SubElement(
        content_types,
        "Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(
        content_types,
        "Default",
        {"Extension": "xml", "ContentType": "application/xml"},
    )
    ET.SubElement(
        content_types,
        "Override",
        {
            "PartName": "/xl/workbook.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        },
    )
    ET.SubElement(
        content_types,
        "Override",
        {
            "PartName": "/xl/styles.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
        },
    )
    for part_name, content_type in overrides:
        ET.SubElement(
            content_types,
            "Override",
            {"PartName": part_name, "ContentType": content_type},
        )
    files["[Content_Types].xml"] = xml_bytes(content_types)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return stream.getvalue()

def _render_pptx(model: Mapping[str, Any]) -> bytes:
    try:
        from pptx import Presentation
        from pptx.chart.data import ChartData
        from pptx.dml.color import RGBColor
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise ArtifactExportUnavailable("PPTX export requires python-pptx") from exc

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]
    rtl = str(model.get("locale", "")).lower().startswith(("ar", "fa", "he", "ur"))
    for slide_spec in model["content"]["slides"]:
        slide = presentation.slides.add_slide(blank)
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor(10, 13, 18)
        title_box = slide.shapes.add_textbox(Inches(0.65), Inches(0.35), Inches(12.0), Inches(0.7))
        frame = title_box.text_frame
        frame.clear()
        paragraph = frame.paragraphs[0]
        paragraph.text = str(slide_spec.get("title", ""))
        paragraph.font.size = Pt(27)
        paragraph.font.bold = True
        paragraph.font.color.rgb = RGBColor(245, 247, 250)
        paragraph.alignment = PP_ALIGN.RIGHT if rtl else PP_ALIGN.LEFT
        for element in slide_spec.get("elements", []):
            x, y, w, h = _slide_geometry(element)
            element_type = element["type"]
            if element_type == "text":
                box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
                tf = box.text_frame
                tf.word_wrap = True
                tf.clear()
                lines = str(element.get("text", "")).splitlines() or [""]
                for line_index, line in enumerate(lines):
                    p = tf.paragraphs[0] if line_index == 0 else tf.add_paragraph()
                    p.text = line
                    p.font.size = Pt(float(element.get("font_size", 18)))
                    p.font.color.rgb = RGBColor(224, 231, 239)
                    p.alignment = PP_ALIGN.RIGHT if rtl else PP_ALIGN.LEFT
            elif element_type == "shape":
                shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
                shape.fill.solid()
                shape.fill.fore_color.rgb = _rgb(str(element.get("fill", "#1F2937")), RGBColor)
                shape.line.color.rgb = _rgb(str(element.get("line", "#4B5563")), RGBColor)
                shape.text = str(element.get("text", ""))
                for p in shape.text_frame.paragraphs:
                    p.font.size = Pt(float(element.get("font_size", 16)))
                    p.font.color.rgb = RGBColor(255, 255, 255)
                    p.alignment = PP_ALIGN.CENTER
            elif element_type == "table":
                rows = element.get("rows", [[""]])
                table = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h)).table
                for ri, row in enumerate(rows):
                    for ci, value in enumerate(row):
                        cell = table.cell(ri, ci)
                        cell.text = "" if value is None else str(value)
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = RGBColor(17, 24, 39) if ri == 0 else RGBColor(31, 41, 55)
                        for p in cell.text_frame.paragraphs:
                            p.font.size = Pt(11)
                            p.font.color.rgb = RGBColor(255, 255, 255)
                            p.font.bold = ri == 0
            elif element_type == "chart":
                categories = [str(item) for item in element.get("categories", [])]
                series = element.get("series", [])
                if categories and series:
                    chart_data = ChartData()
                    chart_data.categories = categories
                    for series_item in series:
                        chart_data.add_series(str(series_item.get("name", "Series")), tuple(float(v) for v in series_item.get("values", [])))
                    chart_type = {
                        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
                        "line": XL_CHART_TYPE.LINE,
                        "pie": XL_CHART_TYPE.PIE,
                    }.get(str(element.get("chart_type", "bar")), XL_CHART_TYPE.COLUMN_CLUSTERED)
                    chart = slide.shapes.add_chart(chart_type, Inches(x), Inches(y), Inches(w), Inches(h), chart_data).chart
                    chart.has_title = True
                    chart.chart_title.text_frame.text = str(element.get("title", "Chart"))
                    chart.has_legend = bool(element.get("legend", True))
    stream = io.BytesIO()
    presentation.save(stream)
    return stream.getvalue()


def _slide_geometry(element: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(element.get("x", 0.75)),
        float(element.get("y", 1.25)),
        float(element.get("w", 11.8)),
        float(element.get("h", 5.5)),
    )


def _rgb(value: str, rgb_type: Any) -> Any:
    cleaned = value.lstrip("#")
    if len(cleaned) != 6:
        cleaned = "1F2937"
    try:
        return rgb_type(int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
    except ValueError:
        return rgb_type(31, 41, 55)


def _render_pdf(model: Mapping[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ArtifactExportUnavailable("PDF export requires reportlab") from exc

    stream = io.BytesIO()
    rtl = str(model.get("locale", "")).lower().startswith(("ar", "fa", "he", "ur"))
    font_name = "Helvetica"
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).is_file():
            try:
                pdfmetrics.registerFont(TTFont("W1Unicode", candidate))
                font_name = "W1Unicode"
                break
            except Exception:
                pass
    page_size = landscape(A4) if model["kind"] == "presentation" else A4
    document = SimpleDocTemplate(
        stream,
        pagesize=page_size,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=str(model["title"]),
    )
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "W1Normal", parent=styles["BodyText"], fontName=font_name, fontSize=10.5,
        leading=15, alignment=TA_RIGHT if rtl else TA_LEFT, spaceAfter=6,
    )
    title_style = ParagraphStyle(
        "W1Title", parent=styles["Title"], fontName=font_name, fontSize=23,
        leading=28, alignment=TA_RIGHT if rtl else TA_LEFT, textColor=colors.HexColor("#111827"),
    )
    heading = ParagraphStyle(
        "W1Heading", parent=styles["Heading2"], fontName=font_name, fontSize=15,
        leading=19, alignment=TA_RIGHT if rtl else TA_LEFT, textColor=colors.HexColor("#111827"),
    )
    story: list[Any] = [Paragraph(_xml_text(str(model["title"])), title_style), Spacer(1, 6)]
    if model["kind"] == "document":
        for block in model["content"]["blocks"]:
            block_type = block["type"]
            if block_type == "heading":
                story.append(Paragraph(_xml_text(str(block.get("text", ""))), heading))
            elif block_type in {"paragraph", "callout"}:
                text = _xml_text(str(block.get("text", ""))).replace("\n", "<br/>")
                story.append(Paragraph(text, normal))
            elif block_type in {"bullet_list", "numbered_list"}:
                for index, item in enumerate(block.get("items", []), 1):
                    marker = "•" if block_type == "bullet_list" else f"{index}."
                    story.append(Paragraph(f"{marker} {_xml_text(str(item))}", normal))
            elif block_type == "table":
                table = Table([["" if value is None else str(value) for value in row] for row in block.get("rows", [])], repeatRows=1)
                table.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9CA3AF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 5),
                ]))
                story.extend([table, Spacer(1, 8)])
            elif block_type == "page_break":
                story.append(PageBreak())
    elif model["kind"] == "spreadsheet":
        for sheet_index, sheet in enumerate(model["content"]["sheets"]):
            if sheet_index:
                story.append(PageBreak())
            story.append(Paragraph(_xml_text(str(sheet["name"])), heading))
            rows = sheet.get("rows", [])[:200]
            if rows:
                table = Table([["" if v is None else str(v) for v in row] for row in rows], repeatRows=int(sheet.get("header_rows", 1)))
                table.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9CA3AF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(table)
    else:
        # One PDF page per presentation slide.
        story = []
        for slide_index, slide in enumerate(model["content"]["slides"]):
            if slide_index:
                story.append(PageBreak())
            story.append(Paragraph(_xml_text(str(slide.get("title", ""))), title_style))
            story.append(Spacer(1, 10))
            for element in slide.get("elements", []):
                if element["type"] in {"text", "shape"}:
                    story.append(Paragraph(_xml_text(str(element.get("text", ""))).replace("\n", "<br/>"), normal))
                elif element["type"] == "table":
                    rows = [["" if v is None else str(v) for v in row] for row in element.get("rows", [])]
                    if rows:
                        table = Table(rows, repeatRows=1)
                        table.setStyle(TableStyle([
                            ("FONTNAME", (0, 0), (-1, -1), font_name),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9CA3AF")),
                        ]))
                        story.append(table)
                elif element["type"] == "chart":
                    story.append(Paragraph(_xml_text(str(element.get("title", "Chart"))) + " - chart data preserved in source model", normal))
    document.build(story)
    return stream.getvalue()


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def validate_export_bytes(data: bytes, format: str) -> dict[str, Any]:
    selected = format.lower().lstrip(".")
    errors: list[str] = []
    metadata: dict[str, Any] = {"format": selected, "size_bytes": len(data), "sha256": sha256_bytes(data)}
    if not data:
        errors.append("empty export")
    elif selected == "pdf":
        if not data.startswith(b"%PDF-"):
            errors.append("missing PDF header")
        if b"%%EOF" not in data[-2048:]:
            errors.append("missing PDF EOF marker")
        metadata["pdf_header"] = data[:8].decode("ascii", errors="replace")
    elif selected in {"docx", "xlsx", "pptx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                required = {
                    "docx": {"[Content_Types].xml", "word/document.xml"},
                    "xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
                    "pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
                }[selected]
                missing = required - names
                if missing:
                    errors.append("missing package entries: " + ", ".join(sorted(missing)))
                bad_member = archive.testzip()
                if bad_member:
                    errors.append(f"corrupt zip member {bad_member}")
                metadata["package_entries"] = len(names)
        except zipfile.BadZipFile:
            errors.append("invalid OOXML zip package")
    elif selected == "json":
        try:
            validate_artifact_model(json.loads(data.decode("utf-8")))
        except Exception as exc:
            errors.append(f"invalid artifact JSON: {exc}")
    else:
        errors.append("unsupported format")
    return {"valid": not errors, "errors": errors, "metadata": metadata}


def reference_artifact_models() -> dict[str, dict[str, Any]]:
    """Return deterministic sample models used by demos and executable probes."""

    return {
        "document": {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_id": "reference-document",
            "kind": "document",
            "title": "W1 Nexus Verification Note",
            "locale": "en",
            "metadata": {"purpose": "reference-demo"},
            "content": {"blocks": [
                {"type": "heading", "level": 1, "text": "Verified workflow"},
                {"type": "paragraph", "text": "This document was generated from the canonical W1 artifact model."},
                {"type": "bullet_list", "items": ["Immutable revisions", "Independent review", "Governed export"]},
                {"type": "table", "rows": [["Capability", "Status"], ["DOCX", "Passed"], ["PDF", "Passed"]]},
            ]},
        },
        "spreadsheet": {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_id": "reference-spreadsheet",
            "kind": "spreadsheet",
            "title": "W1 Nexus Capability Matrix",
            "locale": "en",
            "metadata": {"purpose": "reference-demo"},
            "content": {"sheets": [{
                "name": "Capabilities",
                "header_rows": 1,
                "rows": [["Component", "Implemented", "Weight", "Total"], ["Governance", True, 10, None], ["Artifacts", True, 8, None], ["Cloud", False, 4, None]],
                "formulas": [{"cell": "D2", "formula": "=SUM(C2:C4)", "number_format": "0"}],
                "charts": [{"type": "bar", "source_range": "A1:C4", "title": "Capability weights", "start_cell": "F2", "end_cell": "M18"}],
            }]},
        },
        "presentation": {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_id": "reference-presentation",
            "kind": "presentation",
            "title": "W1 Nexus Artifact Engine",
            "locale": "en",
            "metadata": {"purpose": "reference-demo"},
            "content": {"slides": [
                {"title": "One model, many exports", "elements": [{"type": "text", "text": "DOCX • XLSX • PPTX • PDF\nLocal-first and governed", "x": 0.9, "y": 1.5, "w": 11.3, "h": 3.2, "font_size": 27}]},
                {"title": "Verification", "elements": [{"type": "table", "rows": [["Stage", "Result"], ["Schema", "Passed"], ["Review", "Passed"], ["Export", "Passed"]], "x": 1.1, "y": 1.5, "w": 10.8, "h": 3.8}]},
            ]},
        },
    }


def run_universal_artifact_benchmark() -> dict[str, Any]:
    """Run local, deterministic document/spreadsheet/presentation probes."""

    models = reference_artifact_models()
    formats = {
        "document": ("docx", "pdf", "json"),
        "spreadsheet": ("xlsx", "pdf", "json"),
        "presentation": ("pptx", "pdf", "json"),
    }
    probes: dict[str, bool] = {}
    outputs: dict[str, Any] = {}
    for kind, model in models.items():
        normalized = validate_artifact_model(model)
        probes[f"{kind}_model_valid"] = normalized["kind"] == kind
        outputs[kind] = {}
        for selected_format in formats[kind]:
            data = render_artifact(model, selected_format)
            validation = validate_export_bytes(data, selected_format)
            probes[f"{kind}_{selected_format}_export_valid"] = validation["valid"]
            outputs[kind][selected_format] = validation["metadata"]
    patched = apply_json_patch(models["document"], [
        {"op": "add", "path": "/content/blocks/-", "value": {"type": "paragraph", "text": "Reviewable patch applied."}}
    ])
    probes["reviewable_patch_applied"] = len(patched["content"]["blocks"]) == 5
    try:
        apply_json_patch(models["document"], [{"op": "replace", "path": "/artifact_id", "value": "changed"}])
        probes["identity_patch_rejected"] = False
    except ArtifactPatchError:
        probes["identity_patch_rejected"] = True
    return {"passed": all(probes.values()), "probes": probes, "outputs": outputs}
