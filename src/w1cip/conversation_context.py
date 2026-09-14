"""Durable local conversation history with token-bounded context assembly.

Full messages are retained locally, while provider prompts receive only a
bounded context pack: recent exact turns, optional summaries, and governed
long-term-memory snippets.  The store does not automatically promote model
outputs into long-term memory.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .memory import MemoryPrincipal, MemoryQuery, MemoryStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    # Deliberately conservative tokenizer-independent estimate.
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


@dataclass(frozen=True)
class ConversationMessage:
    conversation_id: str
    sequence: int
    role: str
    content: str
    created_at: str
    model_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.content)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"token_estimate": self.token_estimate}


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    start_sequence: int
    end_sequence: int
    summary: str
    created_at: str
    source: str = "explicit"

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.summary)


@dataclass(frozen=True)
class ConversationContextPack:
    conversation_id: str
    token_budget: int
    estimated_tokens: int
    full_history_tokens: int
    estimated_tokens_saved: int
    recent_messages: tuple[ConversationMessage, ...]
    summary: ConversationSummary | None
    memory_context: str
    memory_citations: tuple[str, ...]
    omitted_messages: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "full_history_tokens": self.full_history_tokens,
            "estimated_tokens_saved": self.estimated_tokens_saved,
            "savings_ratio": (
                round(self.estimated_tokens_saved / self.full_history_tokens, 6)
                if self.full_history_tokens else 0.0
            ),
            "recent_messages": [m.as_dict() for m in self.recent_messages],
            "summary": (asdict(self.summary) | {"token_estimate": self.summary.token_estimate}) if self.summary else None,
            "memory_context": self.memory_context,
            "memory_citations": list(self.memory_citations),
            "omitted_messages": self.omitted_messages,
            "generated_at": self.generated_at,
        }


class ConversationStore:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._initialize()

    def __enter__(self) -> "ConversationStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    conversation_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    model_id TEXT,
                    metadata_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, sequence),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    conversation_id TEXT NOT NULL,
                    start_sequence INTEGER NOT NULL,
                    end_sequence INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary_hash TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, end_sequence),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                );
                """
            )

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        title: str | None = None,
        model_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ConversationMessage:
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("conversation_role_invalid")
        content = str(content)
        if not content.strip():
            raise ValueError("conversation_content_required")
        now = utc_now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence),0) AS seq FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            sequence = int(row["seq"]) + 1
            self._connection.execute(
                "INSERT INTO conversations(conversation_id,title,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET updated_at=excluded.updated_at",
                (conversation_id, title or "New conversation", now, now),
            )
            payload = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            self._connection.execute(
                "INSERT INTO conversation_messages(conversation_id,sequence,role,content,created_at,model_id,metadata_json,content_hash) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (conversation_id, sequence, role, content, now, model_id, payload, digest),
            )
        return ConversationMessage(conversation_id, sequence, role, content, now, model_id, dict(metadata or {}))

    def list_conversations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT c.conversation_id,c.title,c.created_at,c.updated_at,COUNT(m.sequence) AS message_count "
            "FROM conversations c LEFT JOIN conversation_messages m USING(conversation_id) "
            "GROUP BY c.conversation_id ORDER BY c.updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def messages(self, conversation_id: str, *, limit: int = 1000) -> tuple[ConversationMessage, ...]:
        rows = self._connection.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY sequence LIMIT ?",
            (conversation_id, int(limit)),
        ).fetchall()
        return tuple(ConversationMessage(
            row["conversation_id"], int(row["sequence"]), row["role"], row["content"], row["created_at"],
            row["model_id"], json.loads(row["metadata_json"]),
        ) for row in rows)

    def record_summary(
        self,
        conversation_id: str,
        *,
        start_sequence: int,
        end_sequence: int,
        summary: str,
        source: str = "explicit",
    ) -> ConversationSummary:
        if start_sequence < 1 or end_sequence < start_sequence or not summary.strip():
            raise ValueError("conversation_summary_invalid")
        now = utc_now()
        digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO conversation_summaries(conversation_id,start_sequence,end_sequence,summary,created_at,source,summary_hash) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id,end_sequence) DO UPDATE SET "
                "start_sequence=excluded.start_sequence,summary=excluded.summary,created_at=excluded.created_at,source=excluded.source,summary_hash=excluded.summary_hash",
                (conversation_id, start_sequence, end_sequence, summary, now, source, digest),
            )
        return ConversationSummary(conversation_id, start_sequence, end_sequence, summary, now, source)

    def latest_summary(self, conversation_id: str) -> ConversationSummary | None:
        row = self._connection.execute(
            "SELECT * FROM conversation_summaries WHERE conversation_id=? ORDER BY end_sequence DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            row["conversation_id"], int(row["start_sequence"]), int(row["end_sequence"]), row["summary"], row["created_at"], row["source"]
        )

    def build_context(
        self,
        conversation_id: str,
        *,
        query: str = "",
        token_budget: int = 2400,
        memory_store: MemoryStore | None = None,
        memory_namespace: str = "w1-local",
        memory_project: str = "default-project",
        memory_principal: MemoryPrincipal | None = None,
        memory_budget: int | None = None,
    ) -> ConversationContextPack:
        if not 128 <= token_budget <= 200000:
            raise ValueError("conversation_context_token_budget_invalid")
        messages = self.messages(conversation_id)
        full_history_tokens = sum(m.token_estimate + 6 for m in messages)
        summary = self.latest_summary(conversation_id)
        used = 24
        summary_tokens = 0
        if summary:
            summary_tokens = summary.token_estimate + 12
            if used + summary_tokens <= token_budget:
                used += summary_tokens
            else:
                summary = None

        memory_context = ""
        memory_citations: tuple[str, ...] = ()
        if memory_store is not None and query.strip() and memory_principal is not None:
            requested = int(memory_budget or min(800, max(128, token_budget // 4)))
            requested = min(requested, max(64, token_budget - used - 64))
            if requested >= 64:
                bundle = memory_store.build_context_bundle(
                    MemoryQuery(namespace_id=memory_namespace, project_id=memory_project, text=query, limit=20),
                    actor=memory_principal,
                    token_budget=requested,
                )
                if bundle.memories:
                    lines = [f"[{item.citation}] {item.content}" for item in bundle.memories]
                    memory_context = "\n".join(lines)
                    memory_citations = tuple(item.citation for item in bundle.memories)
                    used += bundle.estimated_tokens

        chosen: list[ConversationMessage] = []
        summary_end = summary.end_sequence if summary else 0
        # Newest exact turns win; old turns remain durably stored but are not replayed blindly.
        for message in reversed(messages):
            if message.sequence <= summary_end:
                continue
            cost = message.token_estimate + 8
            if used + cost > token_budget:
                continue
            chosen.append(message)
            used += cost
        chosen.reverse()
        omitted = max(0, len(messages) - len(chosen) - summary_end)
        saved = max(0, full_history_tokens - used)
        return ConversationContextPack(
            conversation_id=conversation_id,
            token_budget=token_budget,
            estimated_tokens=used,
            full_history_tokens=full_history_tokens,
            estimated_tokens_saved=saved,
            recent_messages=tuple(chosen),
            summary=summary,
            memory_context=memory_context,
            memory_citations=memory_citations,
            omitted_messages=omitted,
            generated_at=utc_now(),
        )
