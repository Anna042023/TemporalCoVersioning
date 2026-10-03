#!/usr/bin/env python3
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "data" / "reported" / "hsqldb274"
raw = pd.read_csv(OUT / "RQ7_hsqldb274_raw.csv")
assert len(raw) == 3 * 3 * 4 * 120 * 2, len(raw)
assert set(raw.layout) == {"static", "temporal"}
balance = (
    raw.groupby(["run_id", "payload_bytes", "query_id", "layout"])
    .position.apply(lambda s: (s == 0).sum())
    .eq(2).all()
)
q = (
    raw.groupby(["payload_bytes", "query_id", "layout"], as_index=False)
    .agg(
        resolver_ms=("resolver_ms", "mean"),
        full_ms=("full_ms", "mean"),
        version_slices=("version_slices", "mean"),
        payload_bytes_seen=("payload_bytes_seen", "mean"),
        signal_sum=("signal_sum", "mean"),
    )
)
q.to_csv(OUT / "RQ7_hsqldb274_querylevel.csv", index=False)
sem = q.pivot(index=["payload_bytes", "query_id"], columns="layout", values="signal_sum")
absdiff = (sem["static"] - sem["temporal"]).abs()

def ci(x, seed, n=10000):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    draws = x[rng.integers(0, len(x), size=(n, len(x)))]
    return [float(v) for v in np.quantile(np.median(draws, axis=1), [0.025, 0.975])]

rows = []
for pb in [256, 1024, 4096]:
    d = q[q.payload_bytes == pb]
    s = d[d.layout == "static"].set_index("query_id")
    t = d[d.layout == "temporal"].set_index("query_id")
    rg = (s.resolver_ms - t.resolver_ms) / s.resolver_ms * 100
    fg = (s.full_ms - t.full_ms) / s.full_ms * 100
    rows.append({
        "payload_bytes": pb,
        "queries": 120,
        "independent_fresh_databases": 3,
        "timed_repetitions_per_database": 4,
        "timings_per_query_layout": 12,
        "static_slices_mean": float(s.version_slices.mean()),
        "temporal_slices_mean": float(t.version_slices.mean()),
        "slice_reduction_pct": float((1 - t.version_slices.mean() / s.version_slices.mean()) * 100),
        "resolver_gain_pct_median": float(rg.median()),
        "resolver_gain_ci_95": ci(rg, 20260923 + pb + 1),
        "full_gain_pct_median": float(fg.median()),
        "full_gain_ci_95": ci(fg, 20260923 + pb + 2),
        "positive_query_fraction": float((fg > 0).mean()),
        "semantic_max_abs_diff": float(absdiff.loc[pb].max()),
    })
summary = {
    "engine": "HSQLDB 2.7.4",
    "protocol": "Released RQ7 64-state / 120-window / 3-base / 4 position-balanced repetitions",
    "raw_rows": int(len(raw)),
    "position_balance_pass": bool(balance),
    "semantic_pairs": int(len(absdiff)),
    "semantic_mismatches_gt_1e-9": int((absdiff > 1e-9).sum()),
    "max_signal_abs_diff": float(absdiff.max()),
    "payloads": rows,
}
summary["gate_pass"] = bool(
    summary["position_balance_pass"]
    and summary["semantic_mismatches_gt_1e-9"] == 0
    and all(x["full_gain_ci_95"][0] > 0 for x in rows)
)
(OUT / "RQ7_hsqldb274_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
pd.DataFrame(rows).to_csv(OUT / "RQ7_hsqldb274_summary.csv", index=False)
print(json.dumps(summary, indent=2))
