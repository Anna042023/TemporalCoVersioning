# Inspection and replay scripts

The root README provides the two dependency-free checks. This page is the command index for deterministic checks, figure regeneration, database replays, and external-trace experiments. Released measurements are under `data/reported/`.

## Evidence map

| Evidence | Main released files | Public handling |
|---|---|---|
| RQ1 baseline comparison | `RQ1_multistart_repeated.csv`, `RQ1_BASELINE_PROVENANCE.csv` | released tables only |
| RQ2 Time-Shuffle | `RQ2_multirate_timeshuffle.csv`, `RQ2_matched_timeshuffle_variable.csv` | released tables only |
| RQ3 exact-oracle quality | `RQ3_multistart_full_grid.csv`, `RQ3_stratified_exactness.csv` | `derive_reported_summaries.py` |
| RQ4 regime sweeps | `RQ4_query_update_state_stats.csv`, `RQ4_window_alignment_stats.csv` | figure regeneration from released tables |
| RQ5 HRC grounding | `RQ5_cpp_independent_predictions.csv`, `RQ5_final_probe_calibration_rep15.csv` | figure regeneration from released tables |
| compiler semantics | `structural_compiler_validation.csv`, `COMPILER_SEMANTIC_SUMMARY.csv` | `validate_compiler_semantics.py`, then `derive_reported_summaries.py` |
| RQ6 scaling | `canonical_compact_10x_summary.csv`, `scalability.csv` | figure regeneration from released tables |
| RQ7/RQ8 SQLite | `RQ7_sqlite_querylevel.csv`, `RQ7_RQ8_sqlite_summary.csv` | `sqlite_replay.py` |
| HSQLDB 2.7.4 | `hsqldb274/` | `run_hsqldb274_replay.sh` |
| DuckDB control | `RQ7_duckdb_*` | `duckdb_replay.py` |
| RQ9 chronological transfer | `RQ9_monitoring_trace_*` | `monitoring_trace_replay.py` |
| RQ10 QuestDB | `RQ10_questdb_two_root_*`, `RQ10_root2/` | `native_questdb_replay.py`; deterministic cross-root summary via `derive_reported_summaries.py` |
| RQ11 EC2 CPU | `RQ11_nab_pairwise_*` | `external_nab_pairwise_replay.py --trace cpu` |
| RQ11 NetworkIn | `RQ11_network_pairwise_*` | `external_nab_pairwise_replay.py --trace network` |

All file names above are relative to `data/reported/`; script names are relative to `scripts/`. “Released tables only” means no public measurement-generation pipeline is included. Summary and figure scripts consume released measurements rather than recreating the original measurement run.

## Checks, summaries, and figures

Dependency-free release checks:

```bash
python3 scripts/verify_theory_invariants.py
python3 scripts/verify_reported_results.py
```

`verify_reported_results.py` reads checked-in CSV/JSON evidence and evaluates the theory invariants in memory. It also recomputes key deterministic aggregations in memory and verifies that the checked-in summaries agree with their released source tables, without rerunning timing experiments or modifying the checkout.

Deterministic derived summaries from released tables:

```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/derive_reported_summaries.py
```

Figure regeneration from released tables:

```bash
python3 scripts/make_figures.py
```

The deterministic summary script rewrites derived files under `data/reported/`; run it in a clean clone when preserving checked-in bytes matters. Generated `checks/` and `figures/` directories are ignored by Git. Running `verify_theory_invariants.py` directly writes its machine-readable JSON under `checks/`; the public `verify_reported_results.py` uses its read-only `--stdout` mode. Figure generation only reads released tables and writes under `figures/`.

## Replay prerequisites

Optional Python dependencies used by figures and replay scripts are listed in `scripts/requirements.txt`. The HSQLDB replay additionally requires `java`/`javac`; QuestDB wrappers require `bash`, `curl`, and a QuestDB runtime archive or extracted runtime; the DuckDB replay requires the DuckDB CLI via `DUCKDB_CLI`.

## Database replay commands

SQLite-based controlled and chronological replays:

```bash
python3 scripts/sqlite_replay.py
python3 scripts/monitoring_trace_replay.py
```

HSQLDB 2.7.4:

```bash
scripts/run_hsqldb274_replay.sh /path/to/hsqldb.jar
```

DuckDB:

```bash
DUCKDB_CLI=/path/to/duckdb python3 scripts/duckdb_replay.py
```

QuestDB, using either a local runtime archive or an existing server:

```bash
scripts/run_native_questdb_local.sh /path/to/questdb-10.0.1-rt-linux-x86-64.tar.gz
python3 scripts/native_questdb_replay.py --dry-run
python3 scripts/native_questdb_replay.py --host http://127.0.0.1:9000 \
  --reps 4 --warmup 600 --migration-reps 15 --concurrent-readers 4
```

## External monitoring traces

EC2 CPU and NetworkIn replays:

```bash
python3 scripts/external_nab_pairwise_replay.py --trace cpu
python3 scripts/external_nab_pairwise_replay.py --trace network
```

Fixed-layout QuestDB semantic replay:

```bash
scripts/run_external_nab_questdb_fixed_layout.sh /path/to/questdb-10.0.1-rt-linux-x86-64.tar.gz
```

This fixed-layout run is a semantic execution check; it is not an estimate of the independently calibrated transfer effect.

## Replay boundary

Files under `data/reported/` are the released measurements. Several replay scripts regenerate files with the same names, and machine-sensitive timing can vary across systems. Run full replays in a clean clone or disposable working copy when the checked-in released files must remain unchanged.
