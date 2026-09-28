from datetime import timedelta
import sqlite3

import pytest

from geo.models import CitationSample, Finding, Locality, Mention, Run, RunKind, Severity, compute_rates
from geo.store import Store


def _sample(q="q1", engine="perplexity", i=0, mentioned=True, error=None):
    return CitationSample(
        query_id=q, query_text=q, locality=Locality(city="Orange"), engine=engine, sample_idx=i, error=error,
        mentions=[] if error else [Mention(entity="client", mentioned=mentioned, cited=False)],
    )


def test_rate_is_mentions_over_successful_samples():
    ss = [_sample(i=0), _sample(i=1, mentioned=False), _sample(i=2), _sample(i=3, error="HTTP 500")]
    (r,) = compute_rates(ss, ["client"])
    assert (r.mentioned, r.n, r.errors, r.label) == (2, 3, 1, "2/3")


def test_run_roundtrip_and_blobs(tmp_path):
    st = Store(tmp_path)
    ref = st.put_blob(b"raw completion")
    run = Run(client="c", kind=RunKind.audit,
              findings=[Finding(check_id="robots.blocks_x", severity=Severity.blocker, title="t", evidence={"a": [1]}, raw_ref=ref)],
              samples=[_sample()])
    st.save_run(run)
    back = st.get_run(run.id[:10])
    assert back.findings == run.findings
    assert back.samples[0].mentions == run.samples[0].mentions
    assert st.blobs.get(ref) == b"raw completion"


def test_runs_are_append_only(tmp_path):
    st = Store(tmp_path)
    run = Run(client="c", kind=RunKind.audit)
    st.save_run(run)
    with pytest.raises(sqlite3.IntegrityError):
        st.save_run(run)  # same id twice
    for sql in ("UPDATE runs SET client='x'", "DELETE FROM runs"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            st.conn.execute(sql)


def test_blob_corruption_is_detected(tmp_path):
    st = Store(tmp_path)
    ref = st.put_blob(b"hello")
    (tmp_path / "raw" / ref).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupt"):
        st.blobs.get(ref)


def test_list_runs_newest_first(tmp_path):
    st = Store(tmp_path)
    a = Run(client="c", kind=RunKind.audit)
    b = Run(client="c", kind=RunKind.audit, started_at=a.started_at + timedelta(days=1))
    st.save_run(a)
    st.save_run(b)
    assert [r.id for r in st.list_runs("c")] == [b.id, a.id]
