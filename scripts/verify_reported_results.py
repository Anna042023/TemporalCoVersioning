#!/usr/bin/env python3
"""Verify released-result invariants using only the Python standard library."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "reported"
SCRIPTS = ROOT / "scripts"


def theory_report() -> dict:
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "verify_theory_invariants.py"), "--stdout"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def close(a: float, b: float, *, atol: float = 1e-9, rtol: float = 1e-9) -> bool:
    return math.isclose(float(a), float(b), abs_tol=atol, rel_tol=rtol)


def read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def verify_derived_summaries(rq3: list[dict[str, str]]) -> None:
    """Check that checked-in deterministic summaries agree with their source tables."""

    # RQ3 optimizer-ablation headline row.
    ablation = {row["scope"]: row for row in read_csv("RQ3_optimizer_ablation_summary.csv")}
    require("ALL" in ablation, "RQ3 ablation summary lacks ALL row")
    all_row = ablation["ALL"]
    n = len(rq3)
    singleton_exact = [int(row["singleton_exact"]) for row in rq3]
    multistart_exact = [int(row["multistart_exact"]) for row in rq3]
    singleton_ratio = [float(row["singleton_ratio"]) for row in rq3]
    multistart_ratio = [float(row["multistart_ratio"]) for row in rq3]
    require(int(all_row["cases"]) == n, "RQ3 ablation case count drift")
    require(close(all_row["singleton_exact_pct"], 100 * sum(singleton_exact) / n), "RQ3 singleton exact-rate drift")
    require(close(all_row["multistart_exact_pct"], 100 * sum(multistart_exact) / n), "RQ3 multistart exact-rate drift")
    require(close(all_row["singleton_mean_ratio"], sum(singleton_ratio) / n), "RQ3 singleton mean-ratio drift")
    require(close(all_row["multistart_mean_ratio"], sum(multistart_ratio) / n), "RQ3 multistart mean-ratio drift")
    require(
        int(all_row["rescued_cases"]) == sum(s == 0 and m == 1 for s, m in zip(singleton_exact, multistart_exact)),
        "RQ3 rescued-case count drift",
    )
    require(
        int(all_row["regressed_cases"]) == sum(s == 1 and m == 0 for s, m in zip(singleton_exact, multistart_exact)),
        "RQ3 regressed-case count drift",
    )

    # Compiler semantic summary must be a direct aggregation of the released validation table.
    compiler_raw = read_csv("structural_compiler_validation.csv")
    compiler_summary = {row["topology"]: row for row in read_csv("COMPILER_SEMANTIC_SUMMARY.csv")}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in compiler_raw:
        grouped[row["topology"]].append(row)
    require(set(grouped) == set(compiler_summary), "compiler-summary topology set drift")
    for topology, rows in grouped.items():
        out = compiler_summary[topology]
        require(int(out["traces"]) == len({row["seed"] for row in rows}), f"{topology} trace-count drift")
        require(int(out["checks"]) == sum(int(row["fragment_query_checks"]) for row in rows), f"{topology} check-count drift")
        require(int(out["mismatches"]) == sum(int(row["mismatches"]) for row in rows), f"{topology} mismatch-count drift")
        require(close(out["mean_exposed_boundaries"], sum(float(row["mean_exposed_boundaries"]) for row in rows) / len(rows)), f"{topology} boundary-mean drift")
        require(close(out["mean_fragments_touched"], sum(float(row["mean_fragments_touched"]) for row in rows) / len(rows)), f"{topology} fragment-mean drift")

    # RQ8 deployment summary is a projection of the released SQLite summary.
    rq8_source = {row["payload_bytes"]: row for row in read_csv("RQ7_RQ8_sqlite_summary.csv")}
    rq8_derived = {row["payload_bytes"]: row for row in read_csv("RQ8_deployment_cost_summary.csv")}
    require(set(rq8_source) == set(rq8_derived), "RQ8 deployment-summary payload set drift")
    projection = [
        "migration_ms_median", "migration_ms_p95", "median_query_saving_ms",
        "query_saving_ci_lo", "query_saving_ci_hi", "break_even_queries_median",
    ]
    for payload, src in rq8_source.items():
        dst = rq8_derived[payload]
        for field in projection:
            require(close(dst[field], src[field]), f"RQ8 {payload} {field} drift")

    # RQ3 stratified summary must match the full exact-oracle grid.
    stratified = {(row["factor"], float(row["value"])): row for row in read_csv("RQ3_stratified_exactness.csv")}
    for factor in ["ratio", "state_bytes", "window_fraction", "psi"]:
        groups: dict[float, list[dict[str, str]]] = defaultdict(list)
        for row in rq3:
            groups[float(row[factor])].append(row)
        for value, rows in groups.items():
            out = stratified[(factor, value)]
            exact = [int(row["multistart_exact"]) for row in rows]
            gaps = [(float(row["multistart_ratio"]) - 1.0) * 100.0 for row in rows]
            require(int(out["cases"]) == len(rows), f"RQ3 stratified {factor}={value} case-count drift")
            require(close(out["exact_rate_pct"], 100 * sum(exact) / len(rows)), f"RQ3 stratified {factor}={value} exact-rate drift")
            require(close(out["mean_gap_pct"], sum(gaps) / len(rows)), f"RQ3 stratified {factor}={value} mean-gap drift")
            require(close(out["worst_gap_pct"], max(gaps)), f"RQ3 stratified {factor}={value} worst-gap drift")

    # RQ11 pooled bin sensitivity must equal the sum of the two matched-pair rows per bin.
    pooled = {(row["metric"], int(row["bins"])): row for row in read_csv("RQ11_pooled_bin_sensitivity.csv")}
    for metric, source_name in [
        ("CPU", "RQ11_nab_pairwise_bin_sensitivity.csv"),
        ("NetworkIn", "RQ11_network_pairwise_bin_sensitivity.csv"),
    ]:
        sums: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for row in read_csv(source_name):
            bins = int(row["bins"])
            sums[bins][0] += int(row["static_hes"])
            sums[bins][1] += int(row["temporal_hes"])
        for bins, (static_hes, temporal_hes) in sums.items():
            out = pooled[(metric, bins)]
            reduction = (1.0 - temporal_hes / static_hes) * 100.0
            require(int(out["static_hes"]) == static_hes, f"RQ11 {metric} bins={bins} static-HES drift")
            require(int(out["temporal_hes"]) == temporal_hes, f"RQ11 {metric} bins={bins} temporal-HES drift")
            require(close(out["reduction_pct"], reduction), f"RQ11 {metric} bins={bins} reduction drift")


def main() -> None:
    # Structural invariants: evaluate in memory so this public verifier is read-only.
    theory = theory_report()
    require(theory["pruning"]["violations"] == 0, "safe-pruning violation")
    require(theory["move_cache_locality"]["violations"] == 0, "move-cache locality violation")
    require(theory["correlation_reduction"]["violations"] == 0, "correlation-reduction violation")
    require(theory["ordered_dp"]["violations"] == 0, "ordered-DP violation")
    require(theory["merge_delta"]["max_abs_error"] < 1e-10, "merge-delta numerical drift")

    # Exact-oracle study.
    rq3 = read_csv("RQ3_multistart_full_grid.csv")
    require(len(rq3) == 288, "unexpected RQ3 instance count")
    require(sum(int(row["multistart_exact"]) for row in rq3) == 278, "RQ3 exact-match count changed")
    verify_derived_summaries(rq3)

    # Controlled engine semantic validation.
    sqlite = read_json("RQ7_sqlite_validation.json")
    require(int(sqlite["semantic_mismatches_gt_1e-9"]) == 0, "SQLite semantic mismatch")

    hsqldb = read_json("hsqldb274/RQ7_hsqldb274_summary.json")
    require(bool(hsqldb["gate_pass"]), "HSQLDB released-result gate does not pass")
    require(int(hsqldb["semantic_mismatches_gt_1e-9"]) == 0, "HSQLDB semantic mismatch")

    duckdb = read_json("RQ7_duckdb_validation.json")
    require(int(duckdb["semantic_mismatches_gt_1e-9"]) == 0, "DuckDB semantic mismatch")
    require(int(duckdb["slice_layout_query_mismatches"]) == 0, "DuckDB slice-layout mismatch")

    chronological = read_json("RQ9_monitoring_trace_validation.json")
    require(int(chronological["semantic_mismatches_gt_1e-9"]) == 0, "chronological replay semantic mismatch")

    # Native QuestDB evidence and its reported null/abstention boundary.
    questdb = read_json("RQ10_questdb_validation.json")
    questdb_two_root = read_json("RQ10_questdb_two_root_validation.json")
    require(bool(questdb["passed"]), "QuestDB native validation does not pass")
    require(bool(questdb_two_root["passed"]), "QuestDB two-root validation does not pass")

    rq10 = read_csv("RQ10_questdb_two_root_summary.csv")
    require(
        all(float(row["server_gain_ci_lo"]) <= 0 <= float(row["server_gain_ci_hi"]) for row in rq10),
        "QuestDB released-result boundary changed",
    )

    abstention = read_json("RQ10_abstention_evidence.json")
    require(bool(abstention["all_server_gain_intervals_cross_zero"]), "QuestDB abstention summary changed")

    # External-trace transfer: positive CPU result, null/mixed NetworkIn replication,
    # plus an independent fixed-layout semantic execution check.
    cpu = read_json("RQ11_nab_pairwise_pooled.json")
    network = read_json("RQ11_network_pairwise_pooled.json")
    require(float(cpu["reduction_pct"]) > 0 and float(cpu["ci_lo"]) > 0, "EC2 CPU transfer boundary changed")
    require(float(network["ci_lo"]) <= 0 <= float(network["ci_hi"]), "EC2 NetworkIn null/mixed boundary changed")

    fixed_layout = read_json("RQ11_questdb_fixed_layout_validation.json")
    require(bool(fixed_layout["passed"]) and int(fixed_layout["mismatches"]) == 0, "RQ11 fixed-layout validation failed")

    print("reported-result consistency: PASS")


if __name__ == "__main__":
    main()
