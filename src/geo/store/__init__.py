"""SQLite store + content-addressed raw blobs. Append-only by construction.

Layout under GEO_HOME:
    geo.db            one file per agency
    raw/<sha256>      verbatim HTTP bodies and LLM completions

Triggers reject UPDATE/DELETE on run data: a Run, once written, is immutable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from geo.models import CitationSample, Finding, Fix, Locality, Mention, Run, RunKind, Severity, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tool_version TEXT NOT NULL,
    meta_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_client_time ON runs(client, started_at);

CREATE TABLE IF NOT EXISTS findings (
    run_id TEXT NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    check_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    fix_json TEXT,
    raw_ref TEXT,
    parser_version TEXT NOT NULL,
    PRIMARY KEY (run_id, idx)
);

CREATE TABLE IF NOT EXISTS samples (
    run_id TEXT NOT NULL REFERENCES runs(id),
    idx INTEGER NOT NULL,
    query_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    locality_json TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT,
    sample_idx INTEGER NOT NULL,
    raw_ref TEXT,
    error TEXT,
    answer_text TEXT NOT NULL,
    empty_reason TEXT,
    cited_urls_json TEXT NOT NULL,
    mentions_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    derived_at TEXT NOT NULL,
    -- Re-derivation (geo reparse) appends a new row with a new parser_version;
    -- readers take the most recently derived row per (run_id, idx).
    PRIMARY KEY (run_id, idx, parser_version)
);
CREATE INDEX IF NOT EXISTS samples_query ON samples(query_id, engine);

-- Every network artifact, with the hash of its verbatim body.
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- site | api
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    status INTEGER,
    request_json TEXT,           -- redacted request (no secrets)
    headers_json TEXT,
    raw_ref TEXT,
    fetched_at TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS artifacts_ref ON artifacts(raw_ref);
"""

IMMUTABLE_TABLES = ("runs", "findings", "samples", "artifacts")


def _triggers() -> str:
    out = []
    for t in IMMUTABLE_TABLES:
        for op in ("UPDATE", "DELETE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;"
            )
    return "\n".join(out)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        h = sha256(data)
        p = self.root / h
        if not p.exists():
            # Unique temp name: concurrent writers of identical bytes must not collide.
            tmp = p.with_name(f".{h}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(p)  # atomic; last writer wins with identical content
        return h

    def get(self, ref: str) -> bytes:
        p = self.root / ref
        if not p.exists():
            raise KeyError(f"blob {ref} not found in {self.root}")
        data = p.read_bytes()
        if sha256(data) != ref:
            raise ValueError(f"blob {ref} is corrupt (hash mismatch)")
        return data

    def exists(self, ref: str) -> bool:
        return (self.root / ref).exists()


class Store:
    def __init__(self, home: Path):
        self.home = home
        self.home.mkdir(parents=True, exist_ok=True)
        self.blobs = BlobStore(home / "raw")
        self.db_path = home / "geo.db"
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA + _triggers())
        self._artifact_seq: dict[str, int] = {}

    def close(self) -> None:
        self.conn.close()

    # --- raw artifacts -------------------------------------------------------------

    def put_blob(self, data: bytes) -> str:
        return self.blobs.put(data)

    def log_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        method: str,
        url: str,
        final_url: str | None,
        status: int | None,
        request: dict[str, Any] | None,
        headers: dict[str, str] | None,
        raw_ref: str | None,
        fetched_at: datetime,
        error: str | None = None,
    ) -> None:
        with self._lock:
            seq = self._artifact_seq.get(run_id)
            if seq is None:
                row = self.conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) FROM artifacts WHERE run_id=?", (run_id,)
                ).fetchone()
                seq = row[0]
            seq += 1
            self._artifact_seq[run_id] = seq
            self.conn.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    seq,
                    kind,
                    method,
                    url,
                    final_url,
                    status,
                    json.dumps(request) if request is not None else None,
                    json.dumps(headers) if headers is not None else None,
                    raw_ref,
                    fetched_at.isoformat(),
                    error,
                ),
            )
            self.conn.commit()

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def artifacts_for_ref(self, raw_ref: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM artifacts WHERE raw_ref=?", (raw_ref,)).fetchall()
        return [dict(r) for r in rows]

    # --- runs ----------------------------------------------------------------------

    def save_run(self, run: Run) -> None:
        """Write a complete Run in one transaction. There is no update path."""
        now = utcnow().isoformat()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.client,
                    run.kind.value,
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.tool_version,
                    json.dumps(run.meta, default=str),
                ),
            )
            self.conn.executemany(
                "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run.id,
                        i,
                        f.check_id,
                        f.severity.value,
                        f.title,
                        json.dumps(f.evidence, default=str),
                        f.fix.model_dump_json() if f.fix else None,
                        f.raw_ref,
                        f.parser_version,
                    )
                    for i, f in enumerate(run.findings)
                ],
            )
            self.conn.executemany(
                "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run.id,
                        i,
                        s.query_id,
                        s.query_text,
                        s.locality.model_dump_json(),
                        s.engine,
                        s.model,
                        s.sample_idx,
                        s.raw_ref,
                        s.error,
                        s.answer_text,
                        s.empty_reason,
                        json.dumps(s.cited_urls),
                        json.dumps([m.model_dump() for m in s.mentions]),
                        s.parser_version,
                        now,
                    )
                    for i, s in enumerate(run.samples)
                ],
            )

    def append_derivations(self, run_id: str, samples: list[tuple[int, CitationSample]]) -> int:
        """Append re-derived samples (same run/idx, new parser_version). Returns rows written."""
        now = utcnow().isoformat()
        rows = [
            (
                run_id, idx, s.query_id, s.query_text, s.locality.model_dump_json(), s.engine, s.model,
                s.sample_idx, s.raw_ref, s.error, s.answer_text, s.empty_reason, json.dumps(s.cited_urls),
                json.dumps([m.model_dump() for m in s.mentions]), s.parser_version, now,
            )
            for idx, s in samples
        ]
        with self._lock, self.conn:
            cur = self.conn.executemany("INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            return cur.rowcount

    def _row_to_run(self, r: sqlite3.Row) -> Run:
        run_id = r["id"]
        findings = [
            Finding(
                check_id=f["check_id"],
                severity=Severity(f["severity"]),
                title=f["title"],
                evidence=json.loads(f["evidence_json"]),
                fix=Fix.model_validate_json(f["fix_json"]) if f["fix_json"] else None,
                raw_ref=f["raw_ref"],
                parser_version=f["parser_version"],
            )
            for f in self.conn.execute(
                "SELECT * FROM findings WHERE run_id=? ORDER BY idx", (run_id,)
            )
        ]
        samples = [
            CitationSample(
                query_id=s["query_id"],
                query_text=s["query_text"],
                locality=Locality.model_validate_json(s["locality_json"]),
                engine=s["engine"],
                model=s["model"],
                sample_idx=s["sample_idx"],
                raw_ref=s["raw_ref"],
                error=s["error"],
                answer_text=s["answer_text"],
                empty_reason=s["empty_reason"],
                cited_urls=json.loads(s["cited_urls_json"]),
                mentions=[Mention(**m) for m in json.loads(s["mentions_json"])],
                parser_version=s["parser_version"],
            )
            for s in self.conn.execute(
                "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY idx ORDER BY derived_at DESC, "
                "parser_version DESC) AS rn FROM samples WHERE run_id=?) WHERE rn=1 ORDER BY idx",
                (run_id,),
            )
        ]
        return Run(
            id=run_id,
            client=r["client"],
            kind=RunKind(r["kind"]),
            started_at=datetime.fromisoformat(r["started_at"]),
            finished_at=datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None,
            tool_version=r["tool_version"],
            meta=json.loads(r["meta_json"]),
            findings=findings,
            samples=samples,
        )

    def get_run(self, run_id: str) -> Run:
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            # allow unique prefix
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE id LIKE ?", (run_id + "%",)
            ).fetchall()
            if len(rows) != 1:
                raise KeyError(f"run {run_id!r} not found" if not rows else f"run prefix {run_id!r} is ambiguous")
            r = rows[0]
        return self._row_to_run(r)

    def list_runs(
        self, client: str, kind: RunKind | None = None, limit: int | None = None
    ) -> list[Run]:
        """Newest first."""
        q = "SELECT * FROM runs WHERE client=?"
        args: list[Any] = [client]
        if kind:
            q += " AND kind=?"
            args.append(kind.value)
        q += " ORDER BY started_at DESC, id DESC"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [self._row_to_run(r) for r in self.conn.execute(q, args).fetchall()]

    def runs_with_samples(self, client: str, limit: int | None = None) -> list[Run]:
        """Newest first: runs (any kind) that contain citation samples."""
        q = (
            "SELECT r.* FROM runs r WHERE r.client=? AND EXISTS "
            "(SELECT 1 FROM samples s WHERE s.run_id = r.id) ORDER BY r.started_at DESC, r.id DESC"
        )
        args: list[Any] = [client]
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [self._row_to_run(r) for r in self.conn.execute(q, args).fetchall()]

    def runs_with_findings(self, client: str, limit: int | None = None) -> list[Run]:
        q = "SELECT * FROM runs WHERE client=? AND kind='audit' ORDER BY started_at DESC, id DESC"
        args: list[Any] = [client]
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [self._row_to_run(r) for r in self.conn.execute(q, args).fetchall()]
