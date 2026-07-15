"""Performance at scale (FR-193).

Synthetic public workspaces at 1k / 10k (/ 100k, opt-in) objects, asserting the
NFR targets in [PRODUCT_REQUIREMENTS.md]. These are marked ``slow`` and run in a
dedicated, non-blocking CI job — CI runner variance makes hard latency numbers a
poor *merge* gate, but a gross regression (an accidental O(n^2), a dropped index)
still trips them. Actual timings are printed so a human can watch the trend.

The 100k tier is gated behind ``STRATA_PERF_100K=1`` because generating and
indexing 100k notes is minutes, not seconds; the 1k/10k tiers run by default.

Latency budgets are the NFR value times ``_CI_SLACK`` to absorb shared-runner
noise; the assertion catches order-of-magnitude regressions, and the printed
p95 catches the subtle ones by eye.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.services.container import Paths, Services

pytestmark = [pytest.mark.slow, pytest.mark.performance]

# Shared CI runners are slower and noisier than the dev machine the NFRs target.
# The multiplier keeps the assertion a regression alarm, not a benchmark.
_CI_SLACK = 6.0


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _build_workspace(tmp_path: Path, count: int) -> tuple[Services, str]:
    paths = Paths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "data" / "logs",
        default_workspace=tmp_path / "workspace",
    )
    services = Services(paths, environment="test")
    services.workspace.create(paths.default_workspace, "Perf", seed_demo=False)
    layer, _recovery = services.workspace.create_layer("Bench", visibility="public")
    store = services.workspace.layer_store(layer.id)

    # Distinctive, searchable content: every note shares a common term and carries
    # a unique one, so a query hits a predictable, non-trivial number of rows.
    for i in range(count):
        store.write_note(
            folder_path=f"F{i % 50}",
            title=f"Note {i:06d}",
            content=(
                f"knowledge base entry {i}. It discusses encryption and threat "
                f"modelling for topic-{i % 500}. unique-marker-{i:06d} end."
            ),
        )
    services.search.invalidate()
    return services, layer.id


def _measure_fts(services: Services, layer_id: str, tmp_path: Path, term: str) -> list[float]:
    # NFR-007..009 govern the *FTS query* — the SQLite FTS5 primitive. Measure it
    # directly on a freshly-built index over the corpus, not through the hybrid
    # ranker (BM25 + recency + graph + tag + property scoring), which is a richer
    # operation with no separate NFR budget.
    from app.infrastructure.search.index import SqliteIndex, tokenize

    notes = services.notes.list_notes([layer_id])
    index = SqliteIndex(layer_id, tmp_path / "fts.sqlite")
    index.rebuild(notes)
    terms = tokenize(term)

    index.search(terms, 50)  # warm
    samples: list[float] = []
    for _ in range(50):
        start = time.perf_counter()
        index.search(terms, 50)
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


@pytest.mark.parametrize(
    ("count", "fts_budget_ms"),
    [
        (1_000, 30.0),  # NFR-007
        (10_000, 80.0),  # NFR-008
    ],
)
def test_search_latency_at_scale(tmp_path: Path, count: int, fts_budget_ms: float) -> None:
    services, layer_id = _build_workspace(tmp_path, count)
    samples = _measure_fts(services, layer_id, tmp_path, "encryption")
    p95 = _percentile(samples, 95)
    print(f"\nFTS p95 @ {count:>6} objects: {p95:.1f} ms (NFR {fts_budget_ms} ms)")
    assert p95 <= fts_budget_ms * _CI_SLACK, f"{p95:.1f}ms exceeds budget"


@pytest.mark.parametrize(("count", "open_budget_ms"), [(1_000, 300.0), (10_000, 1_500.0)])
def test_workspace_reopen_at_scale(tmp_path: Path, count: int, open_budget_ms: float) -> None:
    services, _ = _build_workspace(tmp_path, count)
    services.workspace.close()

    start = time.perf_counter()
    services.workspace.open(services.paths.default_workspace)
    listed = services.notes.list_notes()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print(f"\nReopen @ {count:>6} objects: {elapsed_ms:.0f} ms (NFR {open_budget_ms} ms)")
    assert len(listed) == count
    assert elapsed_ms <= open_budget_ms * _CI_SLACK


@pytest.mark.skipif(
    os.environ.get("STRATA_PERF_100K") != "1",
    reason="100k tier is opt-in (STRATA_PERF_100K=1); it takes minutes to generate.",
)
def test_search_latency_100k(tmp_path: Path) -> None:
    services, layer_id = _build_workspace(tmp_path, 100_000)
    samples = _measure_fts(services, layer_id, tmp_path, "encryption")
    p95 = _percentile(samples, 95)
    print(f"\nFTS p95 @ 100000 objects: {p95:.1f} ms (NFR 250 ms)")
    assert p95 <= 250.0 * _CI_SLACK
