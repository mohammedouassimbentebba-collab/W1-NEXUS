"""W1 Scientific Loop and Verification Lab.

This module provides a local-first, auditable scientific workflow with
preregistration, controlled execution, immutable observations, planned and
exploratory analyses, falsification, replication, independent review, and
reproducibility packages.

It deliberately avoids treating model prose as evidence.  Findings are only
publishable after preregistration, data integrity checks, planned analysis,
and independent review.  Statistical routines use Python's standard library
and clearly label normal-approximation p-values where exact methods are not
available.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import sqlite3
import statistics
import sys
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .memory import MemoryDraft, MemoryPrincipal, MemoryProvenance, MemoryStore
from .secure_execution import SecureExecutionFabric, SandboxRequest, request_from_mapping

CANONICAL_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
STUDY_STATUSES = {
    "draft", "preregistered", "running", "analyzed", "reviewed", "published", "withdrawn"
}
REVIEW_OUTCOMES = {"approved", "revision_required", "rejected"}
HYPOTHESIS_OUTCOMES = {"supported", "falsified", "inconclusive"}
ANALYSIS_METHODS = {
    "descriptive_mean", "difference_in_means", "proportion", "pearson_correlation"
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _valid_id(value: str) -> bool:
    return bool(value) and value[0] != "-" and value[-1] != "-" and "--" not in value and set(value) <= CANONICAL_ID_CHARS


def _require_id(value: str, field: str) -> None:
    if not _valid_id(value):
        raise ScientificValidationError(f"scientific_{field}_invalid")


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ScientificValidationError("scientific_analysis_empty_sample")
    return statistics.fmean(values)


def _sample_sd(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _normal_two_sided_p(z: float) -> float:
    return min(1.0, max(0.0, math.erfc(abs(z) / math.sqrt(2.0))))


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScientificValidationError(f"scientific_observation_{field}_not_numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ScientificValidationError(f"scientific_observation_{field}_not_finite")
    return number


class ScientificError(RuntimeError):
    code = "scientific_error"


class ScientificValidationError(ScientificError):
    code = "scientific_validation_error"


class ScientificStateError(ScientificError):
    code = "scientific_state_error"


class ScientificNotFound(ScientificError):
    code = "scientific_not_found"


class ScientificIntegrityError(ScientificError):
    code = "scientific_integrity_error"


@dataclass(frozen=True)
class ScientificPrincipal:
    principal_type: str
    principal_id: str

    def __post_init__(self) -> None:
        _require_id(self.principal_type, "principal_type")
        _require_id(self.principal_id, "principal_id")

    def as_dict(self) -> dict[str, str]:
        return {"principal_type": self.principal_type, "principal_id": self.principal_id}


@dataclass(frozen=True)
class StudySummary:
    study_id: str
    namespace_id: str
    project_id: str
    title: str
    status: str
    plan_hash: str
    creator: Mapping[str, str]
    created_at: str
    preregistered_at: str | None
    first_observation_at: str | None


class ScientificLab:
    def __init__(self, database_path: str | Path, *, workspace_root: str | Path | None = None) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else self.database_path.parent.parent
        self._connection = sqlite3.connect(str(self.database_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._initialise()

    def __enter__(self) -> "ScientificLab":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _initialise(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS studies(
              study_id TEXT PRIMARY KEY,
              namespace_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              plan_json TEXT NOT NULL,
              plan_hash TEXT NOT NULL,
              creator_type TEXT NOT NULL,
              creator_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              preregistered_at TEXT,
              first_observation_at TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_runs(
              run_id TEXT PRIMARY KEY,
              study_id TEXT NOT NULL REFERENCES studies(study_id),
              execution_id TEXT NOT NULL,
              request_json TEXT NOT NULL,
              result_json TEXT NOT NULL,
              request_digest TEXT NOT NULL,
              result_digest TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations(
              study_id TEXT NOT NULL REFERENCES studies(study_id),
              observation_id TEXT NOT NULL,
              data_json TEXT NOT NULL,
              source_run_id TEXT,
              recorded_by_type TEXT NOT NULL,
              recorded_by_id TEXT NOT NULL,
              recorded_at TEXT NOT NULL,
              previous_hash TEXT NOT NULL,
              observation_hash TEXT NOT NULL,
              PRIMARY KEY(study_id, observation_id)
            );
            CREATE TABLE IF NOT EXISTS analyses(
              analysis_record_id TEXT PRIMARY KEY,
              study_id TEXT NOT NULL REFERENCES studies(study_id),
              analysis_id TEXT NOT NULL,
              method TEXT NOT NULL,
              exploratory INTEGER NOT NULL,
              specification_json TEXT NOT NULL,
              result_json TEXT NOT NULL,
              data_digest TEXT NOT NULL,
              created_by_type TEXT NOT NULL,
              created_by_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(study_id, analysis_id, exploratory)
            );
            CREATE TABLE IF NOT EXISTS evaluations(
              study_id TEXT PRIMARY KEY REFERENCES studies(study_id),
              evaluation_json TEXT NOT NULL,
              evaluation_digest TEXT NOT NULL,
              evaluated_by_type TEXT NOT NULL,
              evaluated_by_id TEXT NOT NULL,
              evaluated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replications(
              replication_id TEXT PRIMARY KEY,
              original_study_id TEXT NOT NULL REFERENCES studies(study_id),
              replication_study_id TEXT NOT NULL REFERENCES studies(study_id),
              result_json TEXT NOT NULL,
              recorded_by_type TEXT NOT NULL,
              recorded_by_id TEXT NOT NULL,
              recorded_at TEXT NOT NULL,
              UNIQUE(original_study_id, replication_study_id)
            );
            CREATE TABLE IF NOT EXISTS scientific_reviews(
              review_id TEXT PRIMARY KEY,
              study_id TEXT NOT NULL REFERENCES studies(study_id),
              reviewer_type TEXT NOT NULL,
              reviewer_id TEXT NOT NULL,
              outcome TEXT NOT NULL,
              rationale TEXT NOT NULL,
              checks_json TEXT NOT NULL,
              reviewed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reproducibility_packages(
              package_id TEXT PRIMARY KEY,
              study_id TEXT NOT NULL REFERENCES studies(study_id),
              path TEXT NOT NULL,
              manifest_json TEXT NOT NULL,
              package_sha256 TEXT NOT NULL,
              created_by_type TEXT NOT NULL,
              created_by_id TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scientific_events(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              study_id TEXT,
              event_type TEXT NOT NULL,
              actor_type TEXT NOT NULL,
              actor_id TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              previous_hash TEXT NOT NULL,
              event_hash TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS observations_no_update
            BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'observations_are_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS observations_no_delete
            BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT, 'observations_are_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS analyses_no_update
            BEFORE UPDATE ON analyses BEGIN SELECT RAISE(ABORT, 'analyses_are_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS analyses_no_delete
            BEFORE DELETE ON analyses BEGIN SELECT RAISE(ABORT, 'analyses_are_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS scientific_events_no_update
            BEFORE UPDATE ON scientific_events BEGIN SELECT RAISE(ABORT, 'scientific_events_are_append_only'); END;
            CREATE TRIGGER IF NOT EXISTS scientific_events_no_delete
            BEFORE DELETE ON scientific_events BEGIN SELECT RAISE(ABORT, 'scientific_events_are_append_only'); END;
            """
        )
        self._connection.commit()

    def _event(self, event_type: str, actor: ScientificPrincipal, payload: Mapping[str, Any], study_id: str | None = None) -> None:
        previous = self._connection.execute("SELECT event_hash FROM scientific_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        occurred_at = utc_now()
        event_id = f"science-event-{uuid.uuid4().hex}"
        body = {
            "event_id": event_id, "study_id": study_id, "event_type": event_type,
            "actor": actor.as_dict(), "payload": dict(payload), "occurred_at": occurred_at,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        self._connection.execute(
            "INSERT INTO scientific_events(event_id,study_id,event_type,actor_type,actor_id,payload_json,occurred_at,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            (event_id, study_id, event_type, actor.principal_type, actor.principal_id,
             canonical_json(dict(payload)), occurred_at, previous_hash, event_hash),
        )

    @staticmethod
    def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        data = json.loads(json.dumps(plan))
        required = {"study_id", "namespace_id", "project_id", "title", "research_question", "hypotheses", "experiment", "analysis_plan", "falsification_criteria"}
        missing = sorted(required - set(data))
        if missing:
            raise ScientificValidationError(f"scientific_plan_missing:{','.join(missing)}")
        for field in ("study_id", "namespace_id", "project_id"):
            _require_id(str(data[field]), field)
        if not isinstance(data["title"], str) or not data["title"].strip():
            raise ScientificValidationError("scientific_title_invalid")
        if not isinstance(data["research_question"], str) or not data["research_question"].strip():
            raise ScientificValidationError("scientific_research_question_invalid")
        hypotheses = data["hypotheses"]
        if not isinstance(hypotheses, list) or not hypotheses:
            raise ScientificValidationError("scientific_hypotheses_required")
        hypothesis_ids: set[str] = set()
        prediction_ids: set[str] = set()
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                raise ScientificValidationError("scientific_hypothesis_object_required")
            hypothesis_id = str(hypothesis.get("hypothesis_id", ""))
            _require_id(hypothesis_id, "hypothesis_id")
            if hypothesis_id in hypothesis_ids:
                raise ScientificValidationError("scientific_duplicate_hypothesis_id")
            hypothesis_ids.add(hypothesis_id)
            if not str(hypothesis.get("statement", "")).strip() or not str(hypothesis.get("null_statement", "")).strip():
                raise ScientificValidationError("scientific_hypothesis_statement_required")
            predictions = hypothesis.get("predictions")
            if not isinstance(predictions, list) or not predictions:
                raise ScientificValidationError("scientific_prediction_required")
            for prediction in predictions:
                prediction_id = str(prediction.get("prediction_id", ""))
                _require_id(prediction_id, "prediction_id")
                if prediction_id in prediction_ids:
                    raise ScientificValidationError("scientific_duplicate_prediction_id")
                prediction_ids.add(prediction_id)
                if not str(prediction.get("metric", "")).strip():
                    raise ScientificValidationError("scientific_prediction_metric_required")
        experiment = data["experiment"]
        if not isinstance(experiment, dict):
            raise ScientificValidationError("scientific_experiment_object_required")
        if int(experiment.get("sample_size_min", 0)) <= 0:
            raise ScientificValidationError("scientific_sample_size_min_invalid")
        schema = experiment.get("observation_schema")
        if not isinstance(schema, dict) or "observation_id" not in schema:
            raise ScientificValidationError("scientific_observation_schema_required")
        analyses = data["analysis_plan"]
        if not isinstance(analyses, list) or not analyses:
            raise ScientificValidationError("scientific_analysis_plan_required")
        analysis_ids: set[str] = set()
        for spec in analyses:
            analysis_id = str(spec.get("analysis_id", ""))
            _require_id(analysis_id, "analysis_id")
            if analysis_id in analysis_ids:
                raise ScientificValidationError("scientific_duplicate_analysis_id")
            analysis_ids.add(analysis_id)
            if spec.get("method") not in ANALYSIS_METHODS:
                raise ScientificValidationError("scientific_analysis_method_invalid")
            alpha = float(spec.get("alpha", 0.05))
            if not 0 < alpha < 1:
                raise ScientificValidationError("scientific_alpha_invalid")
        criteria = data["falsification_criteria"]
        if not isinstance(criteria, list) or not criteria:
            raise ScientificValidationError("scientific_falsification_criteria_required")
        for criterion in criteria:
            _require_id(str(criterion.get("criterion_id", "")), "criterion_id")
            if criterion.get("hypothesis_id") not in hypothesis_ids:
                raise ScientificValidationError("scientific_criterion_unknown_hypothesis")
            if criterion.get("analysis_id") not in analysis_ids:
                raise ScientificValidationError("scientific_criterion_unknown_analysis")
            if criterion.get("rule") not in {"effect_direction_and_significance", "mean_threshold", "proportion_threshold", "correlation_threshold"}:
                raise ScientificValidationError("scientific_falsification_rule_invalid")
        replication = data.setdefault("replication_policy", {"minimum_replications": 0, "independent_required": True})
        if int(replication.get("minimum_replications", 0)) < 0:
            raise ScientificValidationError("scientific_minimum_replications_invalid")
        if data.get("classification", "internal") not in {"public", "internal", "confidential", "restricted"}:
            raise ScientificValidationError("scientific_classification_invalid")
        data.setdefault("classification", "internal")
        return data

    def create_study(self, plan: Mapping[str, Any], *, actor: ScientificPrincipal) -> StudySummary:
        data = self.validate_plan(plan)
        study_id = str(data["study_id"])
        now = utc_now()
        try:
            self._connection.execute(
                "INSERT INTO studies(study_id,namespace_id,project_id,title,status,plan_json,plan_hash,creator_type,creator_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (study_id, data["namespace_id"], data["project_id"], data["title"], "draft", canonical_json(data), sha256_json(data), actor.principal_type, actor.principal_id, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ScientificValidationError("scientific_study_id_exists") from exc
        self._event("study.created", actor, {"plan_hash": sha256_json(data)}, study_id)
        self._connection.commit()
        return self.get_study(study_id)

    def get_study(self, study_id: str) -> StudySummary:
        row = self._connection.execute("SELECT * FROM studies WHERE study_id=?", (study_id,)).fetchone()
        if row is None:
            raise ScientificNotFound(study_id)
        return StudySummary(
            study_id=row["study_id"], namespace_id=row["namespace_id"], project_id=row["project_id"],
            title=row["title"], status=row["status"], plan_hash=row["plan_hash"],
            creator={"principal_type": row["creator_type"], "principal_id": row["creator_id"]},
            created_at=row["created_at"], preregistered_at=row["preregistered_at"],
            first_observation_at=row["first_observation_at"],
        )

    def get_plan(self, study_id: str) -> dict[str, Any]:
        row = self._connection.execute("SELECT plan_json FROM studies WHERE study_id=?", (study_id,)).fetchone()
        if row is None:
            raise ScientificNotFound(study_id)
        return json.loads(row[0])

    def list_studies(self) -> list[dict[str, Any]]:
        return [asdict(self.get_study(row[0])) for row in self._connection.execute("SELECT study_id FROM studies ORDER BY created_at")]

    def preregister(self, study_id: str, *, actor: ScientificPrincipal) -> StudySummary:
        study = self.get_study(study_id)
        if study.status != "draft":
            raise ScientificStateError("scientific_preregistration_requires_draft")
        count = self._connection.execute("SELECT COUNT(*) FROM observations WHERE study_id=?", (study_id,)).fetchone()[0]
        if count:
            raise ScientificStateError("scientific_preregistration_after_observation_forbidden")
        now = utc_now()
        self._connection.execute("UPDATE studies SET status='preregistered',preregistered_at=?,updated_at=? WHERE study_id=?", (now, now, study_id))
        self._event("study.preregistered", actor, {"plan_hash": study.plan_hash}, study_id)
        self._connection.commit()
        return self.get_study(study_id)

    def execute_experiment(
        self,
        study_id: str,
        request: SandboxRequest | Mapping[str, Any],
        *,
        actor: ScientificPrincipal,
        fabric: SecureExecutionFabric | None = None,
    ) -> dict[str, Any]:
        study = self.get_study(study_id)
        if study.status not in {"preregistered", "running"}:
            raise ScientificStateError("scientific_experiment_requires_preregistration")
        plan = self.get_plan(study_id)
        experiment = plan["experiment"]
        req = request_from_mapping(request) if isinstance(request, Mapping) else request
        expected_command = tuple(str(x) for x in experiment.get("command", ()))
        if expected_command and tuple(req.argv) != expected_command:
            raise ScientificValidationError("scientific_experiment_command_differs_from_preregistration")
        expected_profile = experiment.get("sandbox_profile")
        if expected_profile and req.profile_id != expected_profile:
            raise ScientificValidationError("scientific_experiment_profile_differs_from_preregistration")
        owned_fabric = fabric is None
        fabric = fabric or SecureExecutionFabric(self.workspace_root)
        try:
            result = fabric.execute(req)
        finally:
            if owned_fabric:
                fabric.close()
        run_id = f"science-run-{uuid.uuid4().hex}"
        request_payload = req.redacted_mapping()
        result_payload = asdict(result)
        self._connection.execute(
            "INSERT INTO experiment_runs(run_id,study_id,execution_id,request_json,result_json,request_digest,result_digest,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, study_id, req.execution_id, canonical_json(request_payload), canonical_json(result_payload), sha256_json(request_payload), sha256_json(result_payload), utc_now()),
        )
        if result.status == "completed":
            self._connection.execute("UPDATE studies SET status='running',updated_at=? WHERE study_id=?", (utc_now(), study_id))
        self._event("experiment.executed", actor, {"run_id": run_id, "execution_id": req.execution_id, "status": result.status, "result_digest": sha256_json(result_payload)}, study_id)
        self._connection.commit()
        return {"run_id": run_id, "result": result_payload}

    def add_observations(
        self,
        study_id: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        actor: ScientificPrincipal,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        study = self.get_study(study_id)
        if study.status not in {"preregistered", "running"}:
            raise ScientificStateError("scientific_observations_require_preregistered_study")
        if not rows:
            raise ScientificValidationError("scientific_observations_required")
        analysis_count = self._connection.execute("SELECT COUNT(*) FROM analyses WHERE study_id=?", (study_id,)).fetchone()[0]
        if analysis_count:
            raise ScientificStateError("scientific_observations_after_analysis_forbidden")
        if source_run_id is not None:
            run = self._connection.execute(
                "SELECT study_id FROM experiment_runs WHERE run_id=?", (source_run_id,)
            ).fetchone()
            if run is None:
                raise ScientificValidationError("scientific_source_run_not_found")
            if run["study_id"] != study_id:
                raise ScientificValidationError("scientific_source_run_study_mismatch")
        plan = self.get_plan(study_id)
        schema = dict(plan["experiment"]["observation_schema"])
        required_fields = set(schema)
        previous_row = self._connection.execute("SELECT observation_hash FROM observations WHERE study_id=? ORDER BY recorded_at DESC,observation_id DESC LIMIT 1", (study_id,)).fetchone()
        previous_hash = previous_row[0] if previous_row else "0" * 64
        now = utc_now()
        inserted = 0
        for raw in rows:
            row = dict(raw)
            missing = sorted(required_fields - set(row))
            if missing:
                raise ScientificValidationError(f"scientific_observation_missing:{','.join(missing)}")
            observation_id = str(row.get("observation_id", ""))
            _require_id(observation_id, "observation_id")
            for field, declared_type in schema.items():
                if declared_type == "number":
                    _as_float(row[field], field)
                elif declared_type == "integer" and (isinstance(row[field], bool) or not isinstance(row[field], int)):
                    raise ScientificValidationError(f"scientific_observation_{field}_not_integer")
                elif declared_type == "string" and not isinstance(row[field], str):
                    raise ScientificValidationError(f"scientific_observation_{field}_not_string")
                elif declared_type == "boolean" and not isinstance(row[field], bool):
                    raise ScientificValidationError(f"scientific_observation_{field}_not_boolean")
            payload = {"study_id": study_id, "observation_id": observation_id, "data": row, "source_run_id": source_run_id, "recorded_by": actor.as_dict(), "recorded_at": now, "previous_hash": previous_hash}
            observation_hash = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
            try:
                self._connection.execute(
                    "INSERT INTO observations(study_id,observation_id,data_json,source_run_id,recorded_by_type,recorded_by_id,recorded_at,previous_hash,observation_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                    (study_id, observation_id, canonical_json(row), source_run_id, actor.principal_type, actor.principal_id, now, previous_hash, observation_hash),
                )
            except sqlite3.IntegrityError as exc:
                raise ScientificValidationError("scientific_duplicate_observation_id") from exc
            previous_hash = observation_hash
            inserted += 1
        if study.first_observation_at is None:
            self._connection.execute("UPDATE studies SET first_observation_at=?,status='running',updated_at=? WHERE study_id=?", (now, now, study_id))
        else:
            self._connection.execute("UPDATE studies SET status='running',updated_at=? WHERE study_id=?", (now, study_id))
        self._event("observations.recorded", actor, {"count": inserted, "source_run_id": source_run_id, "last_observation_hash": previous_hash}, study_id)
        self._connection.commit()
        return {"study_id": study_id, "inserted": inserted, "last_observation_hash": previous_hash}

    def observations(self, study_id: str) -> list[dict[str, Any]]:
        self.get_study(study_id)
        return [json.loads(row[0]) for row in self._connection.execute("SELECT data_json FROM observations WHERE study_id=? ORDER BY rowid", (study_id,))]

    def _data_digest(self, study_id: str) -> str:
        rows = self._connection.execute("SELECT observation_id,observation_hash FROM observations WHERE study_id=? ORDER BY rowid", (study_id,)).fetchall()
        return sha256_json([[row[0], row[1]] for row in rows])

    def run_analysis(
        self,
        study_id: str,
        analysis_id: str,
        *,
        actor: ScientificPrincipal,
        exploratory_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        study = self.get_study(study_id)
        if study.status not in {"running", "analyzed", "reviewed"}:
            raise ScientificStateError("scientific_analysis_requires_observations")
        data = self.observations(study_id)
        if not data:
            raise ScientificStateError("scientific_analysis_requires_observations")
        plan = self.get_plan(study_id)
        minimum_sample = int(plan["experiment"].get("sample_size_min", 1))
        if len(data) < minimum_sample:
            raise ScientificStateError("scientific_minimum_sample_not_met")
        exploratory = exploratory_spec is not None
        if exploratory:
            spec = dict(exploratory_spec or {})
            spec["analysis_id"] = analysis_id
            if spec.get("method") not in ANALYSIS_METHODS:
                raise ScientificValidationError("scientific_analysis_method_invalid")
        else:
            specs = {str(item["analysis_id"]): dict(item) for item in plan["analysis_plan"]}
            if analysis_id not in specs:
                raise ScientificValidationError("scientific_analysis_not_preregistered")
            spec = specs[analysis_id]
        result = self._compute_analysis(spec, data)
        result["analysis_id"] = analysis_id
        result["exploratory"] = exploratory
        result["p_value_method"] = "normal_approximation" if "p_value" in result else None
        record_id = f"analysis-{uuid.uuid4().hex}"
        try:
            self._connection.execute(
                "INSERT INTO analyses(analysis_record_id,study_id,analysis_id,method,exploratory,specification_json,result_json,data_digest,created_by_type,created_by_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, study_id, analysis_id, spec["method"], int(exploratory), canonical_json(spec), canonical_json(result), self._data_digest(study_id), actor.principal_type, actor.principal_id, utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ScientificValidationError("scientific_analysis_already_recorded") from exc
        self._connection.execute("UPDATE studies SET status='analyzed',updated_at=? WHERE study_id=?", (utc_now(), study_id))
        self._event("analysis.recorded", actor, {"analysis_record_id": record_id, "analysis_id": analysis_id, "exploratory": exploratory, "result_digest": sha256_json(result)}, study_id)
        self._connection.commit()
        return {"analysis_record_id": record_id, "specification": spec, "result": result}

    @staticmethod
    def _compute_analysis(spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        method = spec["method"]
        if method == "descriptive_mean":
            field = str(spec["value_field"])
            values = [_as_float(row[field], field) for row in rows]
            return {"method": method, "n": len(values), "mean": _mean(values), "sample_sd": _sample_sd(values), "minimum": min(values), "maximum": max(values)}
        if method == "difference_in_means":
            field = str(spec["value_field"])
            group_field = str(spec["group_field"])
            group_a, group_b = spec["group_a"], spec["group_b"]
            a = [_as_float(row[field], field) for row in rows if row.get(group_field) == group_a]
            b = [_as_float(row[field], field) for row in rows if row.get(group_field) == group_b]
            if len(a) < 2 or len(b) < 2:
                raise ScientificValidationError("scientific_difference_in_means_needs_two_per_group")
            mean_a, mean_b = _mean(a), _mean(b)
            var_a, var_b = statistics.variance(a), statistics.variance(b)
            se = math.sqrt(var_a / len(a) + var_b / len(b))
            effect = mean_a - mean_b
            z = effect / se if se else (math.inf if effect else 0.0)
            return {"method": method, "group_a": group_a, "group_b": group_b, "n_a": len(a), "n_b": len(b), "mean_a": mean_a, "mean_b": mean_b, "effect": effect, "standard_error": se, "z_score": z, "p_value": _normal_two_sided_p(z)}
        if method == "proportion":
            field = str(spec["success_field"])
            expected = spec.get("success_value", True)
            n = len(rows)
            if not n:
                raise ScientificValidationError("scientific_analysis_empty_sample")
            successes = sum(1 for row in rows if row.get(field) == expected)
            p = successes / n
            z = 1.959963984540054
            denom = 1 + z * z / n
            centre = (p + z * z / (2 * n)) / denom
            half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
            return {"method": method, "n": n, "successes": successes, "proportion": p, "wilson_95": [max(0.0, centre - half), min(1.0, centre + half)]}
        if method == "pearson_correlation":
            x_field, y_field = str(spec["x_field"]), str(spec["y_field"])
            pairs = [(_as_float(row[x_field], x_field), _as_float(row[y_field], y_field)) for row in rows]
            if len(pairs) < 3:
                raise ScientificValidationError("scientific_correlation_needs_three_rows")
            xs, ys = zip(*pairs)
            mean_x, mean_y = _mean(xs), _mean(ys)
            numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
            denom = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
            r = numerator / denom if denom else 0.0
            return {"method": method, "n": len(pairs), "correlation": r}
        raise ScientificValidationError("scientific_analysis_method_invalid")

    def analyses(self, study_id: str) -> list[dict[str, Any]]:
        return [
            {
                "analysis_record_id": row["analysis_record_id"], "analysis_id": row["analysis_id"],
                "method": row["method"], "exploratory": bool(row["exploratory"]),
                "specification": json.loads(row["specification_json"]), "result": json.loads(row["result_json"]),
                "data_digest": row["data_digest"], "created_by": {"principal_type": row["created_by_type"], "principal_id": row["created_by_id"]},
                "created_at": row["created_at"],
            }
            for row in self._connection.execute("SELECT * FROM analyses WHERE study_id=? ORDER BY created_at", (study_id,))
        ]

    def evaluate(self, study_id: str, *, actor: ScientificPrincipal) -> dict[str, Any]:
        plan = self.get_plan(study_id)
        analyses = {item["analysis_id"]: item for item in self.analyses(study_id) if not item["exploratory"]}
        planned_ids = {str(item["analysis_id"]) for item in plan["analysis_plan"]}
        if not planned_ids <= set(analyses):
            raise ScientificStateError("scientific_planned_analyses_incomplete")
        outcomes: dict[str, list[dict[str, Any]]] = {str(item["hypothesis_id"]): [] for item in plan["hypotheses"]}
        for criterion in plan["falsification_criteria"]:
            result = analyses[str(criterion["analysis_id"])]["result"]
            verdict = self._criterion_verdict(criterion, result)
            outcomes[str(criterion["hypothesis_id"])].append({"criterion_id": criterion["criterion_id"], "analysis_id": criterion["analysis_id"], "verdict": verdict, "basis": result})
        hypotheses = []
        for hypothesis in plan["hypotheses"]:
            items = outcomes[str(hypothesis["hypothesis_id"])]
            verdicts = {item["verdict"] for item in items}
            if "falsified" in verdicts:
                outcome = "falsified"
            elif verdicts == {"supported"}:
                outcome = "supported"
            else:
                outcome = "inconclusive"
            hypotheses.append({"hypothesis_id": hypothesis["hypothesis_id"], "outcome": outcome, "criteria": items})
        if self._connection.execute("SELECT 1 FROM evaluations WHERE study_id=?", (study_id,)).fetchone():
            raise ScientificStateError("scientific_evaluation_already_recorded")
        evaluation = {"study_id": study_id, "plan_hash": sha256_json(plan), "data_digest": self._data_digest(study_id), "hypotheses": hypotheses, "evaluated_at": utc_now(), "evaluated_by": actor.as_dict()}
        self._connection.execute(
            "INSERT INTO evaluations(study_id,evaluation_json,evaluation_digest,evaluated_by_type,evaluated_by_id,evaluated_at) VALUES(?,?,?,?,?,?)",
            (study_id, canonical_json(evaluation), sha256_json(evaluation), actor.principal_type, actor.principal_id, evaluation["evaluated_at"]),
        )
        self._event("hypotheses.evaluated", actor, {"evaluation_digest": sha256_json(evaluation), "outcomes": [{"hypothesis_id": x["hypothesis_id"], "outcome": x["outcome"]} for x in hypotheses]}, study_id)
        self._connection.commit()
        return evaluation

    @staticmethod
    def _criterion_verdict(criterion: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        rule = criterion["rule"]
        if rule == "effect_direction_and_significance":
            alpha = float(criterion.get("alpha", 0.05))
            effect = float(result.get("effect", 0.0))
            p_value = float(result.get("p_value", 1.0))
            expected = criterion.get("expected_direction")
            direction_ok = (expected == "positive" and effect > 0) or (expected == "negative" and effect < 0)
            if p_value <= alpha and direction_ok:
                return "supported"
            if p_value <= alpha and not direction_ok:
                return "falsified"
            return "inconclusive"
        if rule == "mean_threshold":
            mean = float(result["mean"])
            threshold = float(criterion["threshold"])
            operator = criterion.get("operator", "less_equal")
            ok = mean <= threshold if operator == "less_equal" else mean >= threshold
            return "supported" if ok else "falsified"
        if rule == "proportion_threshold":
            p = float(result["proportion"])
            threshold = float(criterion["threshold"])
            operator = criterion.get("operator", "greater_equal")
            ok = p >= threshold if operator == "greater_equal" else p <= threshold
            return "supported" if ok else "falsified"
        if rule == "correlation_threshold":
            r = float(result["correlation"])
            threshold = float(criterion["threshold"])
            expected = criterion.get("expected_direction", "positive")
            ok = r >= threshold if expected == "positive" else r <= -threshold
            return "supported" if ok else "falsified"
        raise ScientificValidationError("scientific_falsification_rule_invalid")

    def get_evaluation(self, study_id: str) -> dict[str, Any]:
        row = self._connection.execute("SELECT evaluation_json FROM evaluations WHERE study_id=?", (study_id,)).fetchone()
        if row is None:
            raise ScientificNotFound("scientific_evaluation_not_found")
        return json.loads(row[0])

    def compare_replication(self, original_study_id: str, replication_study_id: str, *, actor: ScientificPrincipal) -> dict[str, Any]:
        original = self.get_study(original_study_id)
        replication = self.get_study(replication_study_id)
        if original.project_id != replication.project_id or original.namespace_id != replication.namespace_id:
            raise ScientificValidationError("scientific_replication_scope_mismatch")
        original_eval = self.get_evaluation(original_study_id)
        replication_eval = self.get_evaluation(replication_study_id)
        policy = self.get_plan(original_study_id).get("replication_policy", {})
        if policy.get("independent_required", True) and original.creator == replication.creator:
            raise ScientificValidationError("scientific_replication_not_independent")
        original_outcomes = {x["hypothesis_id"]: x["outcome"] for x in original_eval["hypotheses"]}
        replication_outcomes = {x["hypothesis_id"]: x["outcome"] for x in replication_eval["hypotheses"]}
        comparisons = []
        for hypothesis_id, outcome in original_outcomes.items():
            replica = replication_outcomes.get(hypothesis_id, "missing")
            if replica == outcome and outcome in {"supported", "falsified"}:
                status = "replicated"
            elif "inconclusive" in {outcome, replica} or replica == "missing":
                status = "inconclusive"
            else:
                status = "contradicted"
            comparisons.append({"hypothesis_id": hypothesis_id, "original_outcome": outcome, "replication_outcome": replica, "status": status})
        overall = "replicated" if comparisons and all(x["status"] == "replicated" for x in comparisons) else ("contradicted" if any(x["status"] == "contradicted" for x in comparisons) else "inconclusive")
        result = {"original_study_id": original_study_id, "replication_study_id": replication_study_id, "overall": overall, "comparisons": comparisons}
        replication_id = f"replication-{uuid.uuid4().hex}"
        self._connection.execute(
            "INSERT INTO replications(replication_id,original_study_id,replication_study_id,result_json,recorded_by_type,recorded_by_id,recorded_at) VALUES(?,?,?,?,?,?,?)",
            (replication_id, original_study_id, replication_study_id, canonical_json(result), actor.principal_type, actor.principal_id, utc_now()),
        )
        self._event("replication.compared", actor, {"replication_id": replication_id, "overall": overall, "replication_study_id": replication_study_id}, original_study_id)
        self._connection.commit()
        return {"replication_id": replication_id, **result}

    def review(self, study_id: str, *, reviewer: ScientificPrincipal, outcome: str, rationale: str) -> dict[str, Any]:
        if outcome not in REVIEW_OUTCOMES:
            raise ScientificValidationError("scientific_review_outcome_invalid")
        if not rationale.strip():
            raise ScientificValidationError("scientific_review_rationale_required")
        study = self.get_study(study_id)
        if reviewer.as_dict() == dict(study.creator):
            raise ScientificValidationError("scientific_review_not_independent")
        plan = self.get_plan(study_id)
        analyses = self.analyses(study_id)
        evaluation = self.get_evaluation(study_id)
        analyst_keys = {(x["created_by"]["principal_type"], x["created_by"]["principal_id"]) for x in analyses}
        if (reviewer.principal_type, reviewer.principal_id) in analyst_keys:
            raise ScientificValidationError("scientific_reviewer_was_analyst")
        planned = {str(x["analysis_id"]) for x in plan["analysis_plan"]}
        recorded = {x["analysis_id"] for x in analyses if not x["exploratory"]}
        replication_count = self._connection.execute("SELECT COUNT(*) FROM replications WHERE original_study_id=?", (study_id,)).fetchone()[0]
        required_replications = int(plan.get("replication_policy", {}).get("minimum_replications", 0))
        checks = {
            "preregistered_before_observation": bool(study.preregistered_at and (not study.first_observation_at or study.preregistered_at <= study.first_observation_at)),
            "plan_hash_current": study.plan_hash == sha256_json(plan),
            "planned_analyses_complete": planned <= recorded,
            "data_chain_valid": self.verify(study_id=study_id)["ok"],
            "evaluation_present": bool(evaluation),
            "replication_requirement_met": replication_count >= required_replications,
            "exploratory_analyses_disclosed": all("exploratory" in item for item in analyses),
        }
        if outcome == "approved" and not all(checks.values()):
            raise ScientificStateError("scientific_review_cannot_approve_failed_checks")
        review_id = f"science-review-{uuid.uuid4().hex}"
        reviewed_at = utc_now()
        self._connection.execute(
            "INSERT INTO scientific_reviews(review_id,study_id,reviewer_type,reviewer_id,outcome,rationale,checks_json,reviewed_at) VALUES(?,?,?,?,?,?,?,?)",
            (review_id, study_id, reviewer.principal_type, reviewer.principal_id, outcome, rationale, canonical_json(checks), reviewed_at),
        )
        self._connection.execute("UPDATE studies SET status='reviewed',updated_at=? WHERE study_id=?", (reviewed_at, study_id))
        self._event("study.reviewed", reviewer, {"review_id": review_id, "outcome": outcome, "checks": checks}, study_id)
        self._connection.commit()
        return {"review_id": review_id, "study_id": study_id, "reviewer": reviewer.as_dict(), "outcome": outcome, "rationale": rationale, "checks": checks, "reviewed_at": reviewed_at}

    def reviews(self, study_id: str) -> list[dict[str, Any]]:
        return [
            {"review_id": row["review_id"], "reviewer": {"principal_type": row["reviewer_type"], "principal_id": row["reviewer_id"]}, "outcome": row["outcome"], "rationale": row["rationale"], "checks": json.loads(row["checks_json"]), "reviewed_at": row["reviewed_at"]}
            for row in self._connection.execute("SELECT * FROM scientific_reviews WHERE study_id=? ORDER BY reviewed_at", (study_id,))
        ]

    def experiment_runs(self, study_id: str) -> list[dict[str, Any]]:
        self.get_study(study_id)
        return [
            {
                "run_id": row["run_id"],
                "execution_id": row["execution_id"],
                "request": json.loads(row["request_json"]),
                "result": json.loads(row["result_json"]),
                "request_digest": row["request_digest"],
                "result_digest": row["result_digest"],
                "created_at": row["created_at"],
            }
            for row in self._connection.execute(
                "SELECT * FROM experiment_runs WHERE study_id=? ORDER BY created_at", (study_id,)
            )
        ]

    def build_reproducibility_package(self, study_id: str, output_path: str | Path, *, actor: ScientificPrincipal) -> dict[str, Any]:
        study = self.get_study(study_id)
        approved = [x for x in self.reviews(study_id) if x["outcome"] == "approved"]
        if not approved:
            raise ScientificStateError("scientific_package_requires_approved_review")
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        payloads: dict[str, bytes] = {
            "study.json": (json.dumps({"summary": asdict(study), "plan": self.get_plan(study_id)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "observations.jsonl": b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode() for row in self.observations(study_id)),
            "analyses.json": (json.dumps(self.analyses(study_id), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "evaluation.json": (json.dumps(self.get_evaluation(study_id), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "reviews.json": (json.dumps(self.reviews(study_id), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "replications.json": (json.dumps(self.replications(study_id), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "experiment-runs.json": (json.dumps(self.experiment_runs(study_id), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
            "environment.json": (json.dumps({
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "executable_name": Path(sys.executable).name,
                "w1_scientific_format": "0.1",
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        }
        manifest = {
            "format": "w1-scientific-reproducibility-package/0.1",
            "study_id": study_id,
            "plan_hash": study.plan_hash,
            "created_at": utc_now(),
            "created_by": actor.as_dict(),
            "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)} for name, content in payloads.items()},
        }
        payloads["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        temporary = output.with_suffix(output.suffix + ".tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(payloads.items()):
                archive.writestr(name, content)
        temporary.replace(output)
        package_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        package_id = f"science-package-{uuid.uuid4().hex}"
        self._connection.execute(
            "INSERT INTO reproducibility_packages(package_id,study_id,path,manifest_json,package_sha256,created_by_type,created_by_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (package_id, study_id, str(output), canonical_json(manifest), package_sha, actor.principal_type, actor.principal_id, manifest["created_at"]),
        )
        self._connection.execute("UPDATE studies SET status='published',updated_at=? WHERE study_id=?", (utc_now(), study_id))
        self._event("reproducibility_package.created", actor, {"package_id": package_id, "package_sha256": package_sha, "manifest_digest": sha256_json(manifest)}, study_id)
        self._connection.commit()
        return {"package_id": package_id, "path": str(output), "sha256": package_sha, "manifest": manifest}

    @staticmethod
    def verify_package(path: str | Path) -> dict[str, Any]:
        selected = Path(path).expanduser().resolve()
        with zipfile.ZipFile(selected, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            mismatches = []
            names = set(archive.namelist())
            expected_names = set(manifest["files"]) | {"manifest.json"}
            for missing in sorted(expected_names - names):
                mismatches.append(f"missing:{missing}")
            for extra in sorted(names - expected_names):
                mismatches.append(f"unexpected:{extra}")
            for name, expected in manifest["files"].items():
                if name not in names:
                    continue
                content = archive.read(name)
                actual = hashlib.sha256(content).hexdigest()
                if actual != expected["sha256"] or len(content) != int(expected["size_bytes"]):
                    mismatches.append(name)
        return {"ok": not mismatches, "path": str(selected), "package_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(), "mismatches": mismatches, "manifest": manifest}

    def replications(self, study_id: str) -> list[dict[str, Any]]:
        return [
            {"replication_id": row["replication_id"], **json.loads(row["result_json"]), "recorded_by": {"principal_type": row["recorded_by_type"], "principal_id": row["recorded_by_id"]}, "recorded_at": row["recorded_at"]}
            for row in self._connection.execute("SELECT * FROM replications WHERE original_study_id=? ORDER BY recorded_at", (study_id,))
        ]

    def publish_supported_hypothesis_to_memory(
        self,
        study_id: str,
        hypothesis_id: str,
        *,
        memory_store: MemoryStore,
        actor: MemoryPrincipal,
        visibility: str = "private",
    ) -> dict[str, Any]:
        study = self.get_study(study_id)
        approved = [x for x in self.reviews(study_id) if x["outcome"] == "approved"]
        if not approved:
            raise ScientificStateError("scientific_memory_publish_requires_approved_review")
        evaluation = self.get_evaluation(study_id)
        match = next((x for x in evaluation["hypotheses"] if x["hypothesis_id"] == hypothesis_id), None)
        if match is None or match["outcome"] != "supported":
            raise ScientificStateError("scientific_memory_publish_requires_supported_hypothesis")
        plan = self.get_plan(study_id)
        hypothesis = next(x for x in plan["hypotheses"] if x["hypothesis_id"] == hypothesis_id)
        draft = MemoryDraft(
            namespace_id=study.namespace_id,
            project_id=study.project_id,
            kind="fact",
            subject=study.title,
            predicate=f"scientific-finding-{hypothesis_id}",
            value=hypothesis["statement"],
            text=hypothesis["statement"],
            summary=f"Supported by W1 Scientific Loop study {study_id} after independent review.",
            confidence=0.9,
            sensitivity=plan.get("classification", "internal"),
            visibility=visibility,
            tags=("scientific-finding", "reviewed", "reproducible"),
            provenance=MemoryProvenance(
                source_type="protocol_entity",
                captured_by=actor,
                source_ref={"entity_type": "scientific_study", "study_id": study_id, "plan_hash": study.plan_hash, "hypothesis_id": hypothesis_id},
                source_excerpt=hypothesis["statement"][:1000],
            ),
        )
        record = memory_store.add(draft, actor=actor)
        self._event("finding.published_to_memory", ScientificPrincipal(actor.principal_type, actor.principal_id), {"memory_id": record.memory_id, "hypothesis_id": hypothesis_id}, study_id)
        self._connection.commit()
        return {"memory_id": record.memory_id, "revision": record.revision, "citation": f"memory:{record.memory_id}@{record.revision}"}

    def verify(self, *, study_id: str | None = None) -> dict[str, Any]:
        errors: list[str] = []
        previous = "0" * 64
        for row in self._connection.execute("SELECT * FROM scientific_events ORDER BY sequence"):
            body = {"event_id": row["event_id"], "study_id": row["study_id"], "event_type": row["event_type"], "actor": {"principal_type": row["actor_type"], "principal_id": row["actor_id"]}, "payload": json.loads(row["payload_json"]), "occurred_at": row["occurred_at"], "previous_hash": row["previous_hash"]}
            actual = hashlib.sha256(canonical_json(body).encode()).hexdigest()
            if row["previous_hash"] != previous or row["event_hash"] != actual:
                errors.append(f"event:{row['event_id']}")
            previous = row["event_hash"]
        studies = [study_id] if study_id else [row[0] for row in self._connection.execute("SELECT study_id FROM studies")]
        for selected in studies:
            previous = "0" * 64
            for row in self._connection.execute("SELECT * FROM observations WHERE study_id=? ORDER BY rowid", (selected,)):
                payload = {"study_id": selected, "observation_id": row["observation_id"], "data": json.loads(row["data_json"]), "source_run_id": row["source_run_id"], "recorded_by": {"principal_type": row["recorded_by_type"], "principal_id": row["recorded_by_id"]}, "recorded_at": row["recorded_at"], "previous_hash": row["previous_hash"]}
                actual = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
                if row["previous_hash"] != previous or row["observation_hash"] != actual:
                    errors.append(f"observation:{selected}:{row['observation_id']}")
                previous = row["observation_hash"]
            study = self.get_study(selected)
            if study.plan_hash != sha256_json(self.get_plan(selected)):
                errors.append(f"plan:{selected}")
        return {"ok": not errors, "errors": errors, "event_count": self._connection.execute("SELECT COUNT(*) FROM scientific_events").fetchone()[0], "study_count": len(studies)}

    def build_w1cip_bridge_manifest(self, study_id: str) -> dict[str, Any]:
        """Build a non-entity bridge manifest for W1-CIP Evidence/Verification.

        The manifest never pretends to be a legal W1-CIP entity because claim,
        task, role-assignment, and envelope references belong to the consuming
        session.  It supplies immutable digests, source semantics, suggested
        relations, and separation requirements so the orchestrator can create
        valid Evidence and Verification entities with the correct session refs.
        """
        study = self.get_study(study_id)
        plan = self.get_plan(study_id)
        evaluation = self.get_evaluation(study_id)
        analyses = self.analyses(study_id)
        reviews = self.reviews(study_id)
        integrity = self.verify(study_id=study_id)
        approved_review = next((item for item in reversed(reviews) if item["outcome"] == "approved"), None)
        packages = [
            {
                "package_id": row["package_id"],
                "path": row["path"],
                "sha256": row["package_sha256"],
                "manifest": json.loads(row["manifest_json"]),
            }
            for row in self._connection.execute(
                "SELECT * FROM reproducibility_packages WHERE study_id=? ORDER BY created_at", (study_id,)
            )
        ]
        hypothesis_map = {item["hypothesis_id"]: item for item in plan["hypotheses"]}
        claim_assessments = []
        for item in evaluation["hypotheses"]:
            outcome = item["outcome"]
            claim_assessments.append({
                "hypothesis_id": item["hypothesis_id"],
                "statement": hypothesis_map[item["hypothesis_id"]]["statement"],
                "scientific_outcome": outcome,
                "suggested_evidence_relation": "supports" if outcome == "supported" else ("refutes" if outcome == "falsified" else "contextualizes"),
                "requires_human_or_authorized_decision": True,
            })
        return {
            "format": "w1-scientific-to-w1cip-bridge/0.1",
            "study_ref": {"study_id": study_id, "plan_hash": study.plan_hash},
            "scope": {"namespace_id": study.namespace_id, "project_id": study.project_id},
            "source": {
                "source_type": "scientific_reproducibility_package" if packages else "scientific_study_ledger",
                "uri": f"w1-science://{study.namespace_id}/{study.project_id}/{study_id}",
                "classification": plan.get("classification", "internal"),
                "integrity_status": "verified" if integrity["ok"] else "disputed",
                "packages": packages,
            },
            "planned_analyses": [
                {
                    "analysis_id": item["analysis_id"],
                    "method": item["method"],
                    "exploratory": item["exploratory"],
                    "data_digest": item["data_digest"],
                    "result_digest": sha256_json(item["result"]),
                }
                for item in analyses
            ],
            "claim_assessments": claim_assessments,
            "independent_review": approved_review,
            "replications": self.replications(study_id),
            "recommended_w1cip_mapping": {
                "evidence_type": "computed_result",
                "capture_kind": "calculation",
                "verification_method": "calculation",
                "conclusion_status": "conclusive" if approved_review and integrity["ok"] else "qualified",
                "create_new_claim_contribution_if_missing": True,
                "do_not_treat_model_summary_as_evidence": True,
            },
            "required_role_separation": [
                ["hypothesis_author", "scientific_reviewer"],
                ["data_collector", "scientific_reviewer"],
                ["primary_analyst", "independent_replication_author"],
            ],
        }

    def export_study(self, study_id: str) -> dict[str, Any]:
        study = self.get_study(study_id)
        evaluation = None
        try:
            evaluation = self.get_evaluation(study_id)
        except ScientificNotFound:
            pass
        return {
            "summary": asdict(study), "plan": self.get_plan(study_id), "observations": self.observations(study_id),
            "experiment_runs": self.experiment_runs(study_id), "analyses": self.analyses(study_id), "evaluation": evaluation, "replications": self.replications(study_id),
            "reviews": self.reviews(study_id), "integrity": self.verify(study_id=study_id),
        }
