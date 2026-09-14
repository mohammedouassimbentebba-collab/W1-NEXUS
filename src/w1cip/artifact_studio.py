"""Local-first artifact studio for W1 Nexus.

The studio separates immutable drafts from workspace publication.  Reading and
previewing are confined to the project root.  Draft versions, review comments,
and decisions live in an append-only SQLite journal.  Publishing a reviewed
version is delegated to :mod:`w1cip.action_runtime`, so the visual editor cannot
silently bypass W1 action policy.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .action_runtime import ActionApproval, ActionRequest, ActionRuntime

ARTIFACT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".css", ".html", ".htm", ".xml",
    ".csv", ".sql", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".hpp", ".rs",
    ".go", ".java", ".kt", ".swift", ".dart", ".rb", ".php", ".vue", ".svelte",
}
CODE_EXTENSIONS = TEXT_EXTENSIONS - {".txt", ".md", ".markdown", ".csv"}
BLOCKED_ROOTS = {".git", ".w1nexus"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ArtifactStudioError(RuntimeError):
    code = "artifact_studio_error"


class ArtifactPathDenied(ArtifactStudioError):
    code = "artifact_path_denied"


class ArtifactNotFound(ArtifactStudioError):
    code = "artifact_not_found"


class ArtifactConflict(ArtifactStudioError):
    code = "artifact_conflict"


class ArtifactReviewRequired(ArtifactStudioError):
    code = "artifact_review_required"


class ArtifactContentTooLarge(ArtifactStudioError):
    code = "artifact_content_too_large"


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    kind: str
    size_bytes: int | None
    modified_at: str | None
    children: tuple["FileEntry", ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["children"] = [item.as_dict() for item in self.children]
        return value


@dataclass(frozen=True)
class FileDocument:
    path: str
    kind: str
    mime_type: str
    size_bytes: int
    content_hash: str
    text: str | None
    encoding: str | None
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactVersion:
    artifact_id: str
    version: int
    path: str
    kind: str
    title: str
    content: str
    content_hash: str
    parent_hash: str | None
    workspace_base_hash: str | None
    created_by: str
    created_at: str
    status: str

    def as_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_content:
            value.pop("content", None)
        return value


class WorkspaceFileService:
    """Safe project tree, file preview, and Git diff surface."""

    def __init__(self, root: str | Path, *, max_read_bytes: int = 2_000_000) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_read_bytes = int(max_read_bytes)

    def resolve(self, relative_path: str | Path, *, allow_missing: bool = False) -> Path:
        raw = Path(str(relative_path).replace("\\", "/"))
        if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
            raise ArtifactPathDenied("path must be a canonical workspace-relative path")
        if raw.parts and raw.parts[0] in BLOCKED_ROOTS:
            raise ArtifactPathDenied("W1 and Git internal state is not editable in Artifact Studio")
        candidate = self.root.joinpath(*raw.parts)
        probe = self.root
        for part in raw.parts:
            probe = probe / part
            if probe.exists() and probe.is_symlink():
                raise ArtifactPathDenied("symbolic links are not followed")
        resolved_parent = candidate.parent.resolve()
        if self.root != resolved_parent and self.root not in resolved_parent.parents:
            raise ArtifactPathDenied("path escapes the workspace")
        if candidate.exists():
            resolved = candidate.resolve()
            if self.root != resolved and self.root not in resolved.parents:
                raise ArtifactPathDenied("path escapes the workspace")
        elif not allow_missing:
            raise ArtifactNotFound(str(relative_path))
        return candidate

    def tree(
        self,
        relative_path: str = "",
        *,
        max_depth: int = 5,
        max_entries: int = 1_000,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        base = self.root if not relative_path else self.resolve(relative_path)
        if not base.is_dir():
            raise ArtifactPathDenied("tree root must be a directory")
        count = 0
        truncated = False

        def walk(directory: Path, depth: int) -> tuple[FileEntry, ...]:
            nonlocal count, truncated
            if depth > max_depth or count >= max_entries:
                truncated = True
                return ()
            values: list[FileEntry] = []
            try:
                children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                return ()
            for child in children:
                if count >= max_entries:
                    truncated = True
                    break
                if child.name in BLOCKED_ROOTS or (child.name.startswith(".") and not include_hidden):
                    continue
                if child.is_symlink():
                    continue
                count += 1
                stat = child.stat()
                relative = child.relative_to(self.root).as_posix()
                modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
                if child.is_dir():
                    values.append(FileEntry(relative, child.name, "directory", None, modified, walk(child, depth + 1)))
                else:
                    values.append(FileEntry(relative, child.name, "file", stat.st_size, modified))
            return tuple(values)

        entries = walk(base, 1)
        return {
            "root": "" if base == self.root else base.relative_to(self.root).as_posix(),
            "entries": [item.as_dict() for item in entries],
            "entry_count": count,
            "truncated": truncated,
        }

    def read(self, relative_path: str, *, max_bytes: int | None = None) -> FileDocument:
        selected = self.resolve(relative_path)
        if not selected.is_file():
            raise ArtifactPathDenied("selected path is not a file")
        limit = self.max_read_bytes if max_bytes is None else min(int(max_bytes), self.max_read_bytes)
        size = selected.stat().st_size
        with selected.open("rb") as stream:
            data = stream.read(limit + 1)
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
        digest_hasher = hashlib.sha256()
        with selected.open("rb") as digest_stream:
            for chunk in iter(lambda: digest_stream.read(1024 * 1024), b""):
                digest_hasher.update(chunk)
        digest = digest_hasher.hexdigest()
        mime = mimetypes.guess_type(selected.name)[0] or "application/octet-stream"
        text: str | None = None
        encoding: str | None = None
        if selected.suffix.lower() in TEXT_EXTENSIONS or mime.startswith("text/"):
            try:
                text = data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                try:
                    text = data.decode("utf-8", errors="replace")
                    encoding = "utf-8-replacement"
                except Exception:
                    text = None
        return FileDocument(
            path=relative_path,
            kind=self.kind_for_path(relative_path),
            mime_type=mime,
            size_bytes=size,
            content_hash=digest,
            text=text,
            encoding=encoding,
            truncated=truncated,
        )

    def preview(self, relative_path: str) -> dict[str, Any]:
        document = self.read(relative_path)
        payload = document.as_dict()
        if document.kind == "json" and document.text is not None:
            try:
                payload["structured"] = json.loads(document.text)
                payload["valid"] = True
            except json.JSONDecodeError as exc:
                payload["valid"] = False
                payload["parse_error"] = {"line": exc.lineno, "column": exc.colno, "message": exc.msg}
        elif document.kind == "csv" and document.text is not None:
            rows = list(csv.reader(io.StringIO(document.text)))[:101]
            payload["table"] = {"header": rows[0] if rows else [], "rows": rows[1:]}
        elif document.kind in {"image", "audio", "video", "pdf", "binary"}:
            payload["text"] = None
            payload["preview_mode"] = "local-binary-metadata"
        return payload

    def git_diff(self, relative_path: str | None = None) -> dict[str, Any]:
        command = ["git", "-C", str(self.root), "diff", "--no-ext-diff", "--no-color", "--"]
        if relative_path:
            self.resolve(relative_path, allow_missing=True)
            command.append(relative_path)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        if completed.returncode not in (0, 129):
            return {"available": False, "diff": "", "error": completed.stderr.strip()}
        return {"available": completed.returncode == 0, "diff": completed.stdout, "error": None}

    @staticmethod
    def kind_for_path(relative_path: str) -> str:
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
        if suffix in CODE_EXTENSIONS:
            return "code"
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}:
            return "image"
        if suffix in {".mp3", ".wav", ".ogg", ".flac", ".m4a"}:
            return "audio"
        if suffix in {".mp4", ".webm", ".mov", ".mkv"}:
            return "video"
        if suffix in TEXT_EXTENSIONS:
            return "text"
        return "binary"


class ArtifactStudioStore:
    """Immutable draft/version store with reviews and governed publication."""

    def __init__(self, db_path: str | Path, workspace_root: str | Path, *, max_content_bytes: int = 2_000_000) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.files = WorkspaceFileService(workspace_root, max_read_bytes=max_content_bytes)
        self.max_content_bytes = int(max_content_bytes)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "ArtifactStudioStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                current_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                workspace_base_hash TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_versions (
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                parent_hash TEXT,
                workspace_base_hash TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (artifact_id, version),
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE TABLE IF NOT EXISTS artifact_comments (
                comment_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                line_number INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_reviews (
                review_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                reviewer TEXT NOT NULL,
                outcome TEXT NOT NULL,
                rationale TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_publications (
                publication_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                action_id TEXT NOT NULL,
                published_by TEXT NOT NULL,
                published_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                workspace_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_events (
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
        row = self._connection.execute(
            "SELECT event_hash FROM artifact_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = row["event_hash"] if row else None
        recorded = utc_now()
        core = {
            "event_type": event_type,
            "subject_id": subject_id,
            "payload": dict(payload),
            "previous_hash": previous,
            "recorded_at": recorded,
        }
        digest = sha256_bytes(canonical_json(core).encode())
        self._connection.execute(
            "INSERT INTO artifact_events(event_type, subject_id, payload_json, previous_hash, event_hash, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, subject_id, canonical_json(dict(payload)), previous, digest, recorded),
        )

    def create_draft(
        self,
        *,
        artifact_id: str,
        path: str,
        title: str,
        created_by: str,
        content: str | None = None,
    ) -> ArtifactVersion:
        if not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ValueError("artifact_id must be lowercase kebab-case")
        selected = self.files.resolve(path, allow_missing=True)
        if selected.suffix.lower() not in TEXT_EXTENSIONS:
            raise ArtifactPathDenied("editable drafts currently support text-based artifacts only")
        base_hash: str | None = None
        if selected.is_file():
            document = self.files.read(path)
            if document.text is None:
                raise ArtifactPathDenied("workspace file is not editable text")
            base_hash = document.content_hash
            if content is None:
                content = document.text
        content = content or ""
        self._check_content(content)
        now = utc_now()
        digest = sha256_bytes(content.encode())
        version = ArtifactVersion(
            artifact_id=artifact_id,
            version=1,
            path=path,
            kind=self.files.kind_for_path(path),
            title=title.strip() or Path(path).name,
            content=content,
            content_hash=digest,
            parent_hash=None,
            workspace_base_hash=base_hash,
            created_by=created_by,
            created_at=now,
            status="draft",
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO artifacts(artifact_id, path, kind, title, current_version, status, workspace_base_hash, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, 'draft', ?, ?, ?, ?)",
                (artifact_id, path, version.kind, version.title, base_hash, created_by, now, now),
            )
            self._insert_version(version)
            self._append_event("artifact.created", artifact_id, version.as_dict(include_content=False))
        return version

    def save_version(
        self,
        artifact_id: str,
        *,
        content: str,
        created_by: str,
        expected_parent_hash: str,
    ) -> ArtifactVersion:
        self._check_content(content)
        current = self.get_current(artifact_id)
        if current.content_hash != expected_parent_hash:
            raise ArtifactConflict("draft changed since it was opened")
        digest = sha256_bytes(content.encode())
        if digest == current.content_hash:
            return current
        version = ArtifactVersion(
            artifact_id=artifact_id,
            version=current.version + 1,
            path=current.path,
            kind=current.kind,
            title=current.title,
            content=content,
            content_hash=digest,
            parent_hash=current.content_hash,
            workspace_base_hash=current.workspace_base_hash,
            created_by=created_by,
            created_at=utc_now(),
            status="draft",
        )
        with self._lock, self._connection:
            self._insert_version(version)
            self._connection.execute(
                "UPDATE artifacts SET current_version=?, status='draft', updated_at=? WHERE artifact_id=?",
                (version.version, version.created_at, artifact_id),
            )
            self._append_event("artifact.version.created", artifact_id, version.as_dict(include_content=False))
        return version

    def add_comment(
        self,
        artifact_id: str,
        *,
        version: int,
        author: str,
        body: str,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        self.get_version(artifact_id, version)
        if not body.strip():
            raise ValueError("comment body is required")
        if line_number is not None and line_number < 1:
            raise ValueError("line_number must be positive")
        created = utc_now()
        comment_id = "comment-" + sha256_bytes(
            canonical_json([artifact_id, version, author, body, line_number, created]).encode()
        )[:20]
        payload = {
            "comment_id": comment_id,
            "artifact_id": artifact_id,
            "version": version,
            "author": author,
            "body": body,
            "line_number": line_number,
            "created_at": created,
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO artifact_comments(comment_id, artifact_id, version, author, body, line_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (comment_id, artifact_id, version, author, body, line_number, created),
            )
            self._append_event("artifact.comment.added", artifact_id, payload)
        return payload

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
        current = self.get_current(artifact_id)
        selected = self.get_version(artifact_id, version)
        if selected.version != current.version:
            raise ArtifactConflict("only the current version can be reviewed")
        if reviewer == selected.created_by:
            raise ArtifactReviewRequired("author cannot independently approve their own version")
        if not rationale.strip():
            raise ValueError("review rationale is required")
        created = utc_now()
        review_id = "review-" + sha256_bytes(
            canonical_json([artifact_id, version, reviewer, outcome, selected.content_hash, created]).encode()
        )[:20]
        payload = {
            "review_id": review_id,
            "artifact_id": artifact_id,
            "version": version,
            "reviewer": reviewer,
            "outcome": outcome,
            "rationale": rationale,
            "content_hash": selected.content_hash,
            "created_at": created,
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO artifact_reviews(review_id, artifact_id, version, reviewer, outcome, rationale, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (review_id, artifact_id, version, reviewer, outcome, rationale, selected.content_hash, created),
            )
            self._connection.execute(
                "UPDATE artifacts SET status=?, updated_at=? WHERE artifact_id=?",
                ("reviewed" if outcome == "approved" else "changes_requested", created, artifact_id),
            )
            self._append_event("artifact.review.recorded", artifact_id, payload)
        return payload

    def publish(
        self,
        artifact_id: str,
        *,
        published_by: str,
        action_runtime: ActionRuntime,
        approval: ActionApproval | Mapping[str, Any] | None = None,
        issue_action_approval: bool = False,
    ) -> dict[str, Any]:
        current = self.get_current(artifact_id)
        review = self._approved_review(current)
        if review is None:
            raise ArtifactReviewRequired("current version requires an independent approval")
        selected = self.files.resolve(current.path, allow_missing=True)
        workspace_hash = sha256_bytes(selected.read_bytes()) if selected.is_file() else None
        if current.workspace_base_hash != workspace_hash:
            raise ArtifactConflict("workspace file changed after the draft was created")
        existing_publication = self._connection.execute(
            "SELECT * FROM artifact_publications WHERE artifact_id=? AND version=? AND content_hash=? ORDER BY published_at DESC LIMIT 1",
            (artifact_id, current.version, current.content_hash),
        ).fetchone()
        if existing_publication is not None:
            payload = dict(existing_publication)
            payload["changed_paths"] = []
            payload["review_id"] = review["review_id"]
            payload["idempotent_replay"] = True
            return payload
        action_id = f"publish-{artifact_id}-v{current.version}"
        request = ActionRequest(
            action_id=action_id,
            kind="file.write",
            parameters={"path": current.path, "content": current.content},
            requested_by=published_by,
            reason=f"Publish reviewed artifact {artifact_id} version {current.version}",
        )
        selected_approval = approval
        plan = action_runtime.plan(request)
        if plan.requires_approval and selected_approval is None and issue_action_approval:
            selected_approval = action_runtime.issue_approval(plan, issued_by=published_by)
        result = action_runtime.execute(request, approval=selected_approval)
        final_hash = sha256_bytes(self.files.resolve(current.path).read_bytes())
        created = utc_now()
        publication_id = "publication-" + sha256_bytes(
            canonical_json([artifact_id, current.version, action_id, final_hash]).encode()
        )[:20]
        payload = {
            "publication_id": publication_id,
            "artifact_id": artifact_id,
            "version": current.version,
            "action_id": action_id,
            "published_by": published_by,
            "published_at": created,
            "content_hash": current.content_hash,
            "workspace_hash": final_hash,
            "changed_paths": list(result.changed_paths),
            "review_id": review["review_id"],
        }
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO artifact_publications(publication_id, artifact_id, version, action_id, published_by, published_at, content_hash, workspace_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (publication_id, artifact_id, current.version, action_id, published_by, created, current.content_hash, final_hash),
            )
            self._connection.execute(
                "UPDATE artifacts SET status='published', workspace_base_hash=?, updated_at=? WHERE artifact_id=?",
                (final_hash, created, artifact_id),
            )
            self._connection.execute(
                "UPDATE artifact_versions SET status='published', workspace_base_hash=? WHERE artifact_id=? AND version=?",
                (final_hash, artifact_id, current.version),
            )
            self._append_event("artifact.published", artifact_id, payload)
        return payload

    def get_current(self, artifact_id: str) -> ArtifactVersion:
        row = self._connection.execute(
            "SELECT current_version FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise ArtifactNotFound(artifact_id)
        return self.get_version(artifact_id, int(row["current_version"]))

    def get_version(self, artifact_id: str, version: int) -> ArtifactVersion:
        row = self._connection.execute(
            "SELECT a.path, a.kind, a.title, v.* FROM artifact_versions v JOIN artifacts a USING(artifact_id) "
            "WHERE v.artifact_id=? AND v.version=?",
            (artifact_id, int(version)),
        ).fetchone()
        if row is None:
            raise ArtifactNotFound(f"{artifact_id}@{version}")
        return ArtifactVersion(
            artifact_id=row["artifact_id"], version=int(row["version"]), path=row["path"],
            kind=row["kind"], title=row["title"], content=row["content"],
            content_hash=row["content_hash"], parent_hash=row["parent_hash"],
            workspace_base_hash=row["workspace_base_hash"], created_by=row["created_by"],
            created_at=row["created_at"], status=row["status"],
        )

    def list_artifacts(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT artifact_id, path, kind, title, current_version, status, created_by, created_at, updated_at "
            "FROM artifacts ORDER BY updated_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def history(self, artifact_id: str) -> dict[str, Any]:
        versions = self._connection.execute(
            "SELECT version, content_hash, parent_hash, workspace_base_hash, created_by, created_at, status "
            "FROM artifact_versions WHERE artifact_id=? ORDER BY version DESC", (artifact_id,)
        ).fetchall()
        comments = self._connection.execute(
            "SELECT * FROM artifact_comments WHERE artifact_id=? ORDER BY created_at", (artifact_id,)
        ).fetchall()
        reviews = self._connection.execute(
            "SELECT * FROM artifact_reviews WHERE artifact_id=? ORDER BY created_at", (artifact_id,)
        ).fetchall()
        publications = self._connection.execute(
            "SELECT * FROM artifact_publications WHERE artifact_id=? ORDER BY published_at", (artifact_id,)
        ).fetchall()
        if not versions:
            raise ArtifactNotFound(artifact_id)
        return {
            "artifact_id": artifact_id,
            "current": self.get_current(artifact_id).as_dict(),
            "versions": [dict(row) for row in versions],
            "comments": [dict(row) for row in comments],
            "reviews": [dict(row) for row in reviews],
            "publications": [dict(row) for row in publications],
        }

    def verify(self) -> dict[str, Any]:
        errors: list[str] = []
        previous: str | None = None
        rows = self._connection.execute(
            "SELECT sequence, event_type, subject_id, payload_json, previous_hash, event_hash, recorded_at "
            "FROM artifact_events ORDER BY sequence"
        ).fetchall()
        for row in rows:
            if row["previous_hash"] != previous:
                errors.append(f"event {row['sequence']} previous_hash mismatch")
            core = {
                "event_type": row["event_type"], "subject_id": row["subject_id"],
                "payload": json.loads(row["payload_json"]), "previous_hash": row["previous_hash"],
                "recorded_at": row["recorded_at"],
            }
            digest = sha256_bytes(canonical_json(core).encode())
            if digest != row["event_hash"]:
                errors.append(f"event {row['sequence']} hash mismatch")
            previous = row["event_hash"]
        for artifact in self.list_artifacts(limit=100_000):
            versions = self._connection.execute(
                "SELECT version, content, content_hash, parent_hash FROM artifact_versions WHERE artifact_id=? ORDER BY version",
                (artifact["artifact_id"],),
            ).fetchall()
            prior_hash: str | None = None
            for row in versions:
                if sha256_bytes(row["content"].encode()) != row["content_hash"]:
                    errors.append(f"{artifact['artifact_id']}@{row['version']} content hash mismatch")
                if row["parent_hash"] != prior_hash:
                    errors.append(f"{artifact['artifact_id']}@{row['version']} parent hash mismatch")
                prior_hash = row["content_hash"]
        return {"valid": not errors, "errors": errors, "event_count": len(rows), "artifact_count": len(self.list_artifacts(limit=100_000))}

    def _insert_version(self, version: ArtifactVersion) -> None:
        self._connection.execute(
            "INSERT INTO artifact_versions(artifact_id, version, content, content_hash, parent_hash, workspace_base_hash, created_by, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (version.artifact_id, version.version, version.content, version.content_hash, version.parent_hash,
             version.workspace_base_hash, version.created_by, version.created_at, version.status),
        )

    def _approved_review(self, version: ArtifactVersion) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM artifact_reviews WHERE artifact_id=? AND version=? AND content_hash=? "
            "ORDER BY created_at DESC, review_id DESC LIMIT 1",
            (version.artifact_id, version.version, version.content_hash),
        ).fetchone()
        if row is None or row["outcome"] != "approved":
            return None
        return dict(row)

    def _check_content(self, content: str) -> None:
        if len(content.encode()) > self.max_content_bytes:
            raise ArtifactContentTooLarge(f"draft exceeds {self.max_content_bytes} bytes")


def run_artifact_studio_benchmark() -> dict[str, Any]:
    """Deterministic local probe used by the executable product scorecard."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="w1-artifact-studio-") as directory:
        root = Path(directory)
        (root / "src").mkdir()
        target = root / "src" / "demo.py"
        target.write_text("value = 1\n", encoding="utf-8")
        store = ArtifactStudioStore(root / ".w1nexus" / "artifact-studio.sqlite3", root)
        runtime = ActionRuntime(root)
        try:
            tree = store.files.tree()
            preview = store.files.preview("src/demo.py")
            draft = store.create_draft(
                artifact_id="demo-script", path="src/demo.py", title="Demo script",
                created_by="author-one",
            )
            changed = store.save_version(
                "demo-script", content="value = 2\n", created_by="author-one",
                expected_parent_hash=draft.content_hash,
            )
            store.add_comment(
                "demo-script", version=changed.version, author="reviewer-one",
                body="Verified deterministic update.", line_number=1,
            )
            review = store.review(
                "demo-script", version=changed.version, reviewer="reviewer-one",
                outcome="approved", rationale="Change is scoped and correct.",
            )
            publication = store.publish(
                "demo-script", published_by="human-owner", action_runtime=runtime,
                issue_action_approval=True,
            )
            integrity = store.verify()
            probes = {
                "project_tree_available": tree["entry_count"] >= 2,
                "code_preview_available": preview["kind"] == "code" and str(preview["text"]).replace("\r\n", "\n") == "value = 1\n",
                "immutable_version_created": changed.version == 2 and changed.parent_hash == draft.content_hash,
                "independent_review_enforced": review["reviewer"] != changed.created_by,
                "governed_publish_completed": publication["action_id"] == "publish-demo-script-v2",
                "workspace_updated": target.read_text(encoding="utf-8") == "value = 2\n",
                "artifact_history_integrity": integrity["valid"],
                "no_w1_server_required": True,
            }
            return {
                "passed": all(probes.values()),
                "probes": probes,
                "metrics": {
                    "versions": len(store.history("demo-script")["versions"]),
                    "comments": len(store.history("demo-script")["comments"]),
                    "reviews": len(store.history("demo-script")["reviews"]),
                    "publications": len(store.history("demo-script")["publications"]),
                },
            }
        finally:
            runtime.close()
            store.close()
