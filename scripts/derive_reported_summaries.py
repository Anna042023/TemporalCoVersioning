#!/usr/bin/env python3
"""Regenerate deterministic summaries from released machine-readable tables.

This script only derives compact summaries from files already under
``data/reported/``. It does not rerun timing experiments.
"""
from pathlib import Path
import json
import os
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data' / 'reported'


def derive_closure_summaries() -> None:
    # RQ3 search ablation.
    rq3 = pd.read_csv(DATA / 'RQ3_multistart_full_grid.csv')
    rows = []
    for state, g in rq3.groupby('state_bytes'):
        rows.append({
            'scope': f'{int(state)}B', 'cases': len(g),
            'singleton_exact_pct': 100 * g.singleton_exact.mean(),
            'multistart_exact_pct': 100 * g.multistart_exact.mean(),
            'singleton_mean_ratio': g.singleton_ratio.mean(),
            'multistart_mean_ratio': g.multistart_ratio.mean(),
            'rescued_cases': int(((g.singleton_exact == 0) & (g.multistart_exact == 1)).sum()),
            'regressed_cases': int(((g.singleton_exact == 1) & (g.multistart_exact == 0)).sum()),
        })
    rows.append({
        'scope': 'ALL', 'cases': len(rq3),
        'singleton_exact_pct': 100 * rq3.singleton_exact.mean(),
        'multistart_exact_pct': 100 * rq3.multistart_exact.mean(),
        'singleton_mean_ratio': rq3.singleton_ratio.mean(),
        'multistart_mean_ratio': rq3.multistart_ratio.mean(),
        'rescued_cases': int(((rq3.singleton_exact == 0) & (rq3.multistart_exact == 1)).sum()),
        'regressed_cases': int(((rq3.singleton_exact == 1) & (rq3.multistart_exact == 0)).sum()),
    })
    ab = pd.DataFrame(rows)
    ab.to_csv(DATA / 'RQ3_optimizer_ablation_summary.csv', index=False)

    # Compiler semantic closure.
    cv = pd.read_csv(DATA / 'structural_compiler_validation.csv')
    cs = cv.groupby('topology', as_index=False).agg(
        traces=('seed', 'nunique'), checks=('fragment_query_checks', 'sum'),
        mismatches=('mismatches', 'sum'), mean_exposed_boundaries=('mean_exposed_boundaries', 'mean'),
        mean_fragments_touched=('mean_fragments_touched', 'mean'))
    cs.to_csv(DATA / 'COMPILER_SEMANTIC_SUMMARY.csv', index=False)

    # RQ8 migration/build overhead, paired with the query-level break-even summary.
    sql = pd.read_csv(DATA / 'RQ7_RQ8_sqlite_summary.csv')
    ms = sql[['payload_bytes', 'migration_ms_median', 'migration_ms_p95', 'median_query_saving_ms',
              'query_saving_ci_lo', 'query_saving_ci_hi', 'break_even_queries_median']].copy()
    ms.to_csv(DATA / 'RQ8_deployment_cost_summary.csv', index=False)

    summary = {
        'rq3_ablation': {
            'cases': int(len(rq3)),
            'singleton_exact_pct': float(100 * rq3.singleton_exact.mean()),
            'multistart_exact_pct': float(100 * rq3.multistart_exact.mean()),
            'rescued_cases': int(((rq3.singleton_exact == 0) & (rq3.multistart_exact == 1)).sum()),
            'regressed_cases': int(((rq3.singleton_exact == 1) & (rq3.multistart_exact == 0)).sum()),
            'singleton_mean_ratio': float(rq3.singleton_ratio.mean()),
            'multistart_mean_ratio': float(rq3.multistart_ratio.mean()),
        },
        'compiler': {
            'traces': int(cv.shape[0]), 'checks': int(cv.fragment_query_checks.sum()),
            'mismatches': int(cv.mismatches.sum()),
            'by_topology': cs.to_dict(orient='records'),
        },
        'rq8': ms.to_dict(orient='records'),
    }
    (DATA / 'EXPERIMENTAL_CLOSURE_SUMMARY.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n')


def derive_robustness_summaries() -> None:
    # RQ3: stratify the exact-oracle grid along every varied factor.
    rq3 = pd.read_csv(DATA / 'RQ3_multistart_full_grid.csv')
    rq3['gap_pct'] = (rq3['multistart_ratio'] - 1.0) * 100.0
    rows = []
    for factor in ['ratio', 'state_bytes', 'window_fraction', 'psi']:
        for value, g in rq3.groupby(factor):
            rows.append({
                'factor': factor, 'value': value, 'cases': len(g),
                'exact_rate_pct': 100.0 * g['multistart_exact'].mean(),
                'mean_gap_pct': g['gap_pct'].mean(),
                'worst_gap_pct': g['gap_pct'].max(),
            })
    strat = pd.DataFrame(rows)
    strat.to_csv(DATA / 'RQ3_stratified_exactness.csv', index=False)

    # RQ10: pair identical query IDs across two fresh QuestDB roots.
    r1 = pd.read_csv(DATA / 'RQ10_questdb_querylevel_raw.csv')
    r2 = pd.read_csv(DATA / 'RQ10_root2' / 'RQ10_questdb_querylevel_raw.csv')
    paired = r1.merge(r2, on=['query_id', 'segment'], suffixes=('_root1', '_root2'), validate='one_to_one')
    rows = []
    for segment, g in list(paired.groupby('segment')) + [('ALL', paired)]:
        rho = g['server_gain_pct_root1'].corr(g['server_gain_pct_root2'], method='spearman')
        sign = ((g['server_gain_pct_root1'] > 0) == (g['server_gain_pct_root2'] > 0)).mean() * 100.0
        rows.append({
            'segment': segment, 'queries': len(g), 'spearman_server_gain': rho,
            'sign_agreement_pct': sign,
            'root1_median_server_gain_pct': g['server_gain_pct_root1'].median(),
            'root2_median_server_gain_pct': g['server_gain_pct_root2'].median(),
        })
    pd.DataFrame(rows).to_csv(DATA / 'RQ10_cross_root_reproducibility.csv', index=False)

    # RQ11: pool matched pairs under alternate histogram/bin specifications.
    def pooled_bins(filename: str, metric: str) -> pd.DataFrame:
        df = pd.read_csv(DATA / filename)
        p = df.groupby('bins', as_index=False).agg(static_hes=('static_hes', 'sum'), temporal_hes=('temporal_hes', 'sum'))
        p['reduction_pct'] = (1.0 - p['temporal_hes'] / p['static_hes']) * 100.0
        p.insert(0, 'metric', metric)
        return p

    bins = pd.concat([
        pooled_bins('RQ11_nab_pairwise_bin_sensitivity.csv', 'CPU'),
        pooled_bins('RQ11_network_pairwise_bin_sensitivity.csv', 'NetworkIn')
    ], ignore_index=True)
    bins.to_csv(DATA / 'RQ11_pooled_bin_sensitivity.csv', index=False)

    summary = {
        'rq3': {
            'cases': int(len(rq3)),
            'exact_rate_pct': float(100 * rq3['multistart_exact'].mean()),
            'mean_gap_pct': float(rq3['gap_pct'].mean()),
            'worst_gap_pct': float(rq3['gap_pct'].max()),
            'state_bytes_exact_rate_pct': {
                str(int(k)): float(100 * v)
                for k, v in rq3.groupby('state_bytes')['multistart_exact'].mean().items()
            },
        },
        'rq10': {
            'paired_queries': int(len(paired)),
            'overall_spearman_server_gain': float(
                paired['server_gain_pct_root1'].corr(paired['server_gain_pct_root2'], method='spearman')),
            'overall_sign_agreement_pct': float(
                100 * ((paired['server_gain_pct_root1'] > 0) == (paired['server_gain_pct_root2'] > 0)).mean()),
        },
        'rq11': {
            row.metric: {
                str(int(row.bins)): float(row.reduction_pct)
                for _, row in bins[bins.metric == row.metric].iterrows()
            }
            for _, row in bins.drop_duplicates('metric').iterrows()
        },
    }
    with (DATA / 'ROBUSTNESS_EXTENSION_SUMMARY.json').open('w') as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


def main() -> None:
    derive_closure_summaries()
    derive_robustness_summaries()
    print('reported-summary derivation: PASS')


if __name__ == '__main__':
    main()
