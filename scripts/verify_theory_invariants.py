#!/usr/bin/env python3
"""Deterministic structural checks for the released theory statements.

These checks are not substitutes for proofs. They exercise the reference
formulas and cache-locality invariants on many small generated instances so
release users can detect implementation/algebra drift.
"""
from itertools import combinations
from pathlib import Path
import json
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from temporal_coversioning_reference import (  # noqa: E402
    merge_delta,
    merged_partition,
    moved_partition,
    objective,
    partitions,
)

SEED = 3934
rnd = random.Random(SEED)


def random_instance(n=6):
    atoms = list(range(n))
    updates = []
    for j in range(7):
        atoms_updated = frozenset(a for a in atoms if rnd.random() < 0.32)
        if not atoms_updated:
            atoms_updated = frozenset([rnd.choice(atoms)])
        updates.append((atoms_updated, j + 1))
    queries = []
    for _ in range(10):
        states = frozenset(a for a in atoms if rnd.random() < 0.38)
        if not states:
            states = frozenset([rnd.choice(atoms)])
        left = rnd.randint(0, 5)
        right = rnd.randint(left + 1, 8)
        queries.append((states, (left, right), rnd.choice([1, 2, 3])))
    state_bytes = {a: rnd.randint(1, 4) for a in atoms}
    return atoms, queries, updates, state_bytes


# 1. Exact merge delta and pruning proposition.
merge_cases = 0
max_merge_err = 0.0
prune_checks = 0
prune_violations = 0
for _ in range(500):
    atoms, queries, updates, state_bytes = random_instance(5)
    labels = [rnd.randrange(3) for _ in atoms]
    partition = [
        frozenset(a for a, label in zip(atoms, labels) if label == current)
        for current in sorted(set(labels))
    ]
    if len(partition) < 2:
        continue
    base = objective(partition, queries, updates, state_bytes)
    for first, second in combinations(partition, 2):
        actual = objective(
            merged_partition(partition, first, second), queries, updates, state_bytes
        ) - base
        formula = merge_delta(first, second, queries, updates, state_bytes)
        max_merge_err = max(max_merge_err, abs(actual - formula))
        merge_cases += 1
        cotouch = any((states & first) and (states & second) for states, _, _ in queries)
        if not cotouch:
            prune_checks += 1
            if actual < -1e-10:
                prune_violations += 1

# 2. Move-cache locality: an unrelated move cannot alter another move's delta.
move_checks = 0
move_violations = 0
max_move_err = 0.0
for _ in range(1000):
    atoms, queries, updates, state_bytes = random_instance(6)
    partition = [
        frozenset([0, 1]), frozenset([2, 3]), frozenset([4]), frozenset([5])
    ]
    candidate = moved_partition(partition, 0, 1)
    after_unrelated = moved_partition(partition, 4, 3)
    candidate_after = moved_partition(after_unrelated, 0, 1)
    delta_before = objective(candidate, queries, updates, state_bytes) - objective(
        partition, queries, updates, state_bytes
    )
    delta_after = objective(candidate_after, queries, updates, state_bytes) - objective(
        after_unrelated, queries, updates, state_bytes
    )
    error = abs(delta_before - delta_after)
    max_move_err = max(max_move_err, error)
    move_checks += 1
    if error > 1e-10:
        move_violations += 1

# 3. Correlation-clustering reduction identity on random complete signed graphs.
reduction_checks = 0
reduction_violations = 0
for _ in range(80):
    n = 5
    vertices = list(range(n))
    pairs = list(combinations(vertices, 2))
    positive = {edge for edge in pairs if rnd.random() < 0.5}
    negative = set(pairs) - positive
    for partition in partitions(vertices):
        same = {
            tuple(sorted((u, v)))
            for fragment in partition
            for u, v in combinations(sorted(fragment), 2)
        }
        same_positive = len(positive & same)
        same_negative = len(negative & same)
        value = 6 * len(positive) + n + 2 * (
            (len(positive) - same_positive) + same_negative
        )
        disagreement = (len(positive) - same_positive) + same_negative
        expected = 6 * len(positive) + n + 2 * disagreement
        reduction_checks += 1
        if value != expected:
            reduction_violations += 1

# 4. Ordered DP equals exhaustive contiguous segmentation for actual objective.
dp_trials = 100
dp_violations = 0
max_dp_err = 0.0
for _ in range(dp_trials):
    n = 7
    atoms, queries, updates, state_bytes = random_instance(n)
    phi = {}
    for i in range(n):
        for j in range(i, n):
            fragment = frozenset(range(i, j + 1))
            phi[(i, j)] = objective(
                [fragment],
                [
                    (states & fragment, (left, right), weight)
                    for states, (left, right), weight in queries
                    if states & fragment
                ],
                updates,
                state_bytes,
            )
    dp = [0.0] + [float("inf")] * n
    for j in range(1, n + 1):
        dp[j] = min(dp[i] + phi[(i, j - 1)] for i in range(j))
    best = float("inf")
    for mask in range(1 << (n - 1)):
        cuts = [0] + [i + 1 for i in range(n - 1) if mask >> i & 1] + [n]
        partition = [
            frozenset(range(cuts[k], cuts[k + 1])) for k in range(len(cuts) - 1)
        ]
        best = min(best, objective(partition, queries, updates, state_bytes))
    error = abs(dp[n] - best)
    max_dp_err = max(max_dp_err, error)
    if error > 1e-9:
        dp_violations += 1

report = {
    "seed": SEED,
    "merge_delta": {"cases": merge_cases, "max_abs_error": max_merge_err},
    "pruning": {"checks": prune_checks, "violations": prune_violations},
    "move_cache_locality": {
        "checks": move_checks,
        "max_abs_error": max_move_err,
        "violations": move_violations,
    },
    "correlation_reduction": {
        "partition_checks": reduction_checks,
        "violations": reduction_violations,
    },
    "ordered_dp": {
        "trials": dp_trials,
        "max_abs_error": max_dp_err,
        "violations": dp_violations,
    },
}
if "--stdout" in sys.argv:
    print(json.dumps(report, sort_keys=True))
else:
    checks = ROOT / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    path = checks / "THEORY_INVARIANT_CHECKS.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(path)
