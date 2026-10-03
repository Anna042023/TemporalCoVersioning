# ⏱️ Temporal Co-Versioning

Code and data release for:

**Temporal Co-Versioning: Physical Design of Derived Query State for Historical Time-Series Queries**

Temporal co-versioning studies how independently updateable derived query states should share version boundaries for historical time-series queries. Grouping states can expose fewer historical fragment-version slices, but it can also increase lookup, resolution, and update costs.

## 🌟 Main Contributions

1. **Historical-query physical design.** Historical Execution Span (HES) counts exposed fragment-version slices, while Historical Resolution Cost (HRC) adds calibrated lookup and per-slice costs.
2. **Optimization structure.** The release includes the partition objective, exact merge delta, and an ordered contiguous dynamic program for co-versioned layouts.
3. **Cross-engine evidence.** The reported results cover exact-oracle studies, controlled database replays, chronological transfer, and external monitoring traces, including positive and null/mixed timing outcomes.

## 🔍 Method Overview

A temporal layout partitions derived states into co-versioned fragments. Sharing a fragment can reduce the historical versions exposed to a query, but may increase the cost of locating, resolving, or updating that fragment. The compact reference implementation evaluates this tradeoff and checks the ordered dynamic program against exhaustive contiguous segmentation.

## 📁 Repository Structure

This public repository keeps the compact reference code, inspection/replay scripts, and reported-result data needed to inspect the released evidence.

```text
TemporalCoVersioning/
├── code/
│   └── temporal_coversioning_reference.py
├── scripts/
│   ├── verify_reported_results.py
│   ├── README.md
│   └── ...
└── data/
    └── reported/
```

- **`code/`** contains the compact reference implementation.
- **`scripts/`** contains consistency checks, replay scripts, and figure regeneration. See [`scripts/README.md`](scripts/README.md).
- **`data/reported/`** contains the released machine-readable measurements and provenance.

## 🚀 Quick Start

From the repository root:

```bash
python3 code/temporal_coversioning_reference.py
python3 scripts/verify_reported_results.py
```

The second command checks the exact-oracle count, deterministic summaries against their released source tables, semantic-validation gates across the engine replays, the QuestDB null/abstention boundary, and the positive-versus-null/mixed external-trace transfer boundary.

Successful runs print:

```text
temporal_coversioning_reference: PASS
reported-result consistency: PASS
```

Both commands use only the Python standard library, and the released-result verifier does not modify the checkout.

## 🧪 Experimental Coverage

| Evaluation component | Focus |
|---|---|
| Exact-oracle study | optimizer quality against exact solutions |
| HRC study | structural versus calibrated resolution cost |
| SQLite / HSQLDB / DuckDB | controlled execution transfer |
| QuestDB | chronological native time-series replay |
| EC2 CPU trace | held-out external monitoring transfer |
| EC2 NetworkIn trace | independent null/mixed replication |

The release retains both positive and null/mixed execution results. Structural slice reductions are therefore treated as screening or ranking evidence, not as a guarantee of wall-clock improvement.

For optional dependencies, figure regeneration, and database/trace replay commands, see [`scripts/README.md`](scripts/README.md).
