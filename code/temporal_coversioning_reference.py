#!/usr/bin/env python3
"""Compact reference implementation for Temporal Co-Versioning.

This file exposes the partition objective, the exact merge-delta formula, and
an ordered dynamic program for contiguous partitions.  It is intentionally
small and deterministic: database replays and reported-result checks live in
`scripts/`.
"""
from __future__ import annotations

from itertools import combinations
import math


def partitions(seq):
    """Yield all set partitions of *seq* (for small exhaustive checks)."""
    seq = list(seq)
    if not seq:
        yield []
        return
    first = seq[0]
    for rest in partitions(seq[1:]):
        yield [frozenset([first])] + rest
        for i in range(len(rest)):
            yield rest[:i] + [rest[i] | frozenset([first])] + rest[i + 1 :]


def boundaries(fragment, updates):
    """Return update times that invalidate at least one state in *fragment*."""
    return {t for atoms, t in updates if atoms & fragment}


def objective(partition, queries, updates, state_bytes,
              alpha=0.3, beta=0.2, sigma=1.1, lam=0.05):
    """Evaluate the released HRC-style partition objective."""
    bsets = {fragment: boundaries(fragment, updates) for fragment in partition}
    total = 0.0
    for states, (left, right), weight in queries:
        for fragment in partition:
            if states & fragment:
                bset = bsets[fragment]
                internal = sum(left < t < right for t in bset)
                lookup = alpha + beta * math.log2(1 + len(bset))
                total += weight * (lookup + sigma * (1 + internal))
    total += lam * sum(
        sum(state_bytes[a] for a in fragment) * len(bsets[fragment])
        for fragment in partition
    )
    return total


def merge_delta(first, second, queries, updates, state_bytes,
                alpha=0.3, beta=0.2, sigma=1.1, lam=0.05):
    """Exact objective change produced by merging two fragments."""
    bf = boundaries(first, updates)
    bg = boundaries(second, updates)
    bh = bf | bg
    common = bf & bg
    only_f = bf - bg
    only_g = bg - bf
    lookup = lambda b: alpha + beta * math.log2(1 + len(b))

    delta = 0.0
    for states, (left, right), weight in queries:
        touches_f = bool(states & first)
        touches_g = bool(states & second)
        if touches_f and touches_g:
            row = (
                lookup(bh) - lookup(bf) - lookup(bg)
                - sigma * (1 + sum(left < t < right for t in common))
            )
        elif touches_f:
            row = lookup(bh) - lookup(bf) + sigma * sum(
                left < t < right for t in only_g
            )
        elif touches_g:
            row = lookup(bh) - lookup(bg) + sigma * sum(
                left < t < right for t in only_f
            )
        else:
            continue
        delta += weight * row

    bytes_f = sum(state_bytes[a] for a in first)
    bytes_g = sum(state_bytes[a] for a in second)
    return delta + lam * (bytes_f * len(only_g) + bytes_g * len(only_f))


def merged_partition(partition, first, second):
    return [part for part in partition if part not in (first, second)] + [first | second]


def moved_partition(partition, atom, dst_idx=None, split=False):
    parts = [set(fragment) for fragment in partition]
    src_idx = next(i for i, fragment in enumerate(parts) if atom in fragment)
    if split:
        if len(parts[src_idx]) == 1:
            return None
        parts[src_idx].remove(atom)
        parts.append({atom})
    else:
        if dst_idx == src_idx:
            return None
        parts[src_idx].remove(atom)
        parts[dst_idx].add(atom)
        if not parts[src_idx]:
            parts.pop(src_idx)
    return [frozenset(fragment) for fragment in parts]


def ordered_contiguous_dp(n, queries, updates, state_bytes,
                          alpha=0.3, beta=0.2, sigma=1.1, lam=0.05):
    """Return the minimum objective and one optimal contiguous partition."""
    phi = {}
    for i in range(n):
        for j in range(i, n):
            fragment = frozenset(range(i, j + 1))
            restricted = [
                (states & fragment, interval, weight)
                for states, interval, weight in queries
                if states & fragment
            ]
            phi[(i, j)] = objective(
                [fragment], restricted, updates, state_bytes,
                alpha, beta, sigma, lam,
            )

    dp = [0.0] + [float("inf")] * n
    prev = [-1] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j):
            candidate = dp[i] + phi[(i, j - 1)]
            if candidate < dp[j]:
                dp[j] = candidate
                prev[j] = i

    blocks = []
    j = n
    while j > 0:
        i = prev[j]
        blocks.append(frozenset(range(i, j)))
        j = i
    blocks.reverse()
    return dp[n], blocks


def _self_test():
    state_bytes = {0: 2, 1: 1, 2: 3, 3: 2}
    updates = [
        (frozenset({0, 1}), 1),
        (frozenset({2}), 2),
        (frozenset({1, 3}), 3),
        (frozenset({0, 2}), 4),
    ]
    queries = [
        (frozenset({0, 1}), (0, 4), 2),
        (frozenset({2, 3}), (1, 5), 1),
        (frozenset({0, 3}), (0, 5), 3),
    ]

    first = frozenset({0, 1})
    second = frozenset({2, 3})
    partition = [first, second]
    actual = objective(merged_partition(partition, first, second), queries, updates, state_bytes) - objective(
        partition, queries, updates, state_bytes
    )
    formula = merge_delta(first, second, queries, updates, state_bytes)
    assert abs(actual - formula) < 1e-12

    dp_cost, _ = ordered_contiguous_dp(4, queries, updates, state_bytes)
    exhaustive = float("inf")
    for mask in range(1 << 3):
        cuts = [0] + [i + 1 for i in range(3) if mask >> i & 1] + [4]
        candidate = [frozenset(range(cuts[k], cuts[k + 1])) for k in range(len(cuts) - 1)]
        exhaustive = min(exhaustive, objective(candidate, queries, updates, state_bytes))
    assert abs(dp_cost - exhaustive) < 1e-12


if __name__ == "__main__":
    _self_test()
    print("temporal_coversioning_reference: PASS")
