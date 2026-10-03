#!/usr/bin/env python3
"""QuestDB-native replay for Temporal Co-Versioning external validity.

This harness reuses the released RQ9 event stream but executes the time-series
portion and version-resolution queries inside a native QuestDB deployment.
It deliberately reports native-engine numbers separately from the existing
SQLite evidence; no values are synthesized when QuestDB is unavailable.

Expected server: QuestDB >= 9.x with REST /api/v1/sql/execute and WAL tables.
Current official docs (2026) use QuestDB 10.0.1 in the quick start.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import os
import platform
import random
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import numpy as np
import requests

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data" / "reported"
TRACE_FILE = DATA / "RQ9_monitoring_trace_events.csv"
SEED = 20260923
N_ATOMS = 64
HOT = list(range(16))
T = 30 * 24 * 60
PAYLOAD_BYTES = 1024
EPOCH = "2026-01-01T00:00:00.000000Z"


def layouts():
    cold = list(range(16, 64))
    static = [[2*i, 2*i+1] + cold[6*i:6*i+6] for i in range(8)]
    temporal = [[2*i, 2*i+1] for i in range(8)]
    temporal += [cold[6*i:6*i+6] for i in range(8)]
    return {"static": static, "temporal": temporal}

LAYOUTS = layouts()


def updates_for_atom(a: int):
    """Exact invalidation generator used by RQ9."""
    rg = random.Random(SEED + 17 * a)
    out = []
    if a < 16:
        pair = a // 2
        phase = 90 + 19 * pair
        t = phase
        while t < T:
            out.append(max(1, min(T - 1, t + rg.randint(-25, 25))))
            t += 12 * 60
        for _ in range(6):
            out.append(rg.randrange(1, T))
    else:
        period = [180, 240, 360, 480][a % 4]
        phase = rg.randrange(15, period)
        t = phase
        while t < T:
            out.append(max(1, min(T - 1, t + rg.randint(-18, 18))))
            t += period
    for day in [20, 22]:
        center = day * 24 * 60 + 2 * 60 + (a % 9) * 3
        if a >= 16 or a % 4 == 0:
            for off in [0, 12, 37]:
                tt = center + off
                if 0 < tt < T:
                    out.append(tt)
    return sorted(set(out))

UPDATES = {a: updates_for_atom(a) for a in range(N_ATOMS)}


def qdb_ts(minute: int) -> str:
    # Let QuestDB perform timestamp arithmetic, avoiding Python timezone drift.
    return f"dateadd('m',{int(minute)},'{EPOCH}')"


def build_version_rows(layout_name: str):
    fatoms, versions = [], []
    for fid, atoms in enumerate(LAYOUTS[layout_name]):
        for a in atoms:
            fatoms.append((layout_name, fid, a))
        b = sorted({t for a in atoms for t in UPDATES[a] if 0 < t < T})
        cuts = [0] + b + [T]
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            versions.append((layout_name, fid, lo, hi, PAYLOAD_BYTES))
    return fatoms, versions


class QuestDB:
    def __init__(self, host: str, timeout: float = 120.0):
        self.host = host.rstrip('/')
        self.timeout = timeout
        self.local = threading.local()

    def _session(self):
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
        return self.local.session

    def execute(self, sql: str, timings: bool = True):
        t0 = time.perf_counter_ns()
        r = self._session().get(
            self.host + '/api/v1/sql/execute',
            params={'query': sql, 'timings': 'true' if timings else 'false'},
            timeout=self.timeout,
        )
        client_ms = (time.perf_counter_ns() - t0) / 1e6
        try:
            obj = r.json()
        except Exception:
            obj = {}
        if not r.ok or 'error' in obj:
            msg = obj.get('error', r.text[:1000])
            pos = obj.get('position')
            where = f" at position {pos}" if pos is not None else ''
            raise RuntimeError(f"QuestDB SQL error{where}: {msg}\nSQL: {sql}")
        ti = obj.get('timings') or {}
        # QuestDB timings are reported in nanoseconds in server logs/REST.
        exec_ms = float(ti.get('execute', 0.0)) / 1e6 if ti else float('nan')
        compile_ms = float(ti.get('compiler', 0.0)) / 1e6 if ti else float('nan')
        return obj, exec_ms, compile_ms, client_ms

    def wait_wal(self, table: str):
        """Block until WAL writes are visible to readers.

        QuestDB 10.0.1 documents wait_wal_table(); a native-evidence run must
        not silently replace this visibility barrier with a sleep.
        """
        t0 = time.perf_counter_ns()
        self.execute(f"SELECT wait_wal_table('{table}')", timings=True)
        return (time.perf_counter_ns() - t0) / 1e6, True


def chunks(xs: list, n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


def drop_tables(db: QuestDB):
    for name in [
        'tcv_active_versions', 'tcv_active_fragment_atoms',
        'tcv_versions_seed', 'tcv_fragment_atoms_seed',
        'tcv_signal', 'tcv_ingest'
    ]:
        try:
            db.execute(f'DROP TABLE IF EXISTS {name}')
        except Exception:
            pass


def setup_native_tables(db: QuestDB):
    drop_tables(db)
    db.execute("""
        CREATE TABLE tcv_signal (
            ts TIMESTAMP,
            atom_id INT,
            value DOUBLE
        ) TIMESTAMP(ts) PARTITION BY DAY WAL
    """)
    # 16 monitored atoms x 43,200 minutes. x advances atom-fast, so ts is nondecreasing.
    n = 16 * T
    db.execute(f"""
        INSERT INTO tcv_signal
        SELECT
          dateadd('m', cast((x-1)/16 as int), '{EPOCH}') ts,
          cast((x-1)%16 as int) atom_id,
          sin(0.07*cast((x-1)%16 as int) + 0.0047*cast((x-1)/16 as int))
          + 0.18*cos(0.021*cast((x-1)/16 as int))
          + 0.04*sin(0.0009*cast((x-1)/16 as int)*(cast((x-1)%16 as int)+1)) value
        FROM long_sequence({n})
    """, timings=True)
    db.wait_wal('tcv_signal')

    db.execute("CREATE TABLE tcv_fragment_atoms_seed (layout SYMBOL, fragment_id INT, atom_id INT)")
    db.execute("""
        CREATE TABLE tcv_versions_seed (
            valid_from TIMESTAMP,
            valid_to TIMESTAMP,
            layout SYMBOL,
            fragment_id INT,
            payload_bytes INT
        ) TIMESTAMP(valid_from) PARTITION BY DAY WAL
    """)
    db.execute("""
        CREATE TABLE tcv_ingest (
            ts TIMESTAMP,
            sensor_id INT,
            value DOUBLE
        ) TIMESTAMP(ts) PARTITION BY DAY WAL
    """)

    fa_all, vr_all = [], []
    for layout in ['static', 'temporal']:
        fa, vr = build_version_rows(layout)
        fa_all.extend(fa)
        vr_all.extend(vr)
    fa_sql = [f"('{lay}',{fid},{aid})" for lay, fid, aid in fa_all]
    for part in chunks(fa_sql, 200):
        db.execute("INSERT INTO tcv_fragment_atoms_seed VALUES " + ",".join(part))

    # Sort by valid_from to keep designated timestamps naturally ordered.
    vr_all.sort(key=lambda x: (x[2], x[0], x[1], x[3]))
    vr_sql = [
        f"({qdb_ts(lo)},{qdb_ts(hi)},'{lay}',{fid},{pbytes})"
        for lay, fid, lo, hi, pbytes in vr_all
    ]
    for part in chunks(vr_sql, 40):
        db.execute("INSERT INTO tcv_versions_seed VALUES " + ",".join(part))
    db.wait_wal('tcv_versions_seed')


def install_active_static(db: QuestDB):
    for name in ['tcv_active_versions', 'tcv_active_fragment_atoms']:
        try: db.execute(f'DROP TABLE IF EXISTS {name}')
        except Exception: pass
    db.execute("CREATE TABLE tcv_active_fragment_atoms (layout SYMBOL, fragment_id INT, atom_id INT)")
    db.execute("""
        CREATE TABLE tcv_active_versions (
            valid_from TIMESTAMP,
            valid_to TIMESTAMP,
            layout SYMBOL,
            fragment_id INT,
            payload_bytes INT
        ) TIMESTAMP(valid_from) PARTITION BY DAY WAL
    """)
    db.execute("INSERT INTO tcv_active_fragment_atoms SELECT * FROM tcv_fragment_atoms_seed WHERE layout='static'")
    db.execute("INSERT INTO tcv_active_versions SELECT * FROM tcv_versions_seed WHERE layout='static' ORDER BY valid_from")
    db.wait_wal('tcv_active_versions')


def install_temporal(db: QuestDB):
    t0 = time.perf_counter_ns()
    _, server_ms_v, _, _ = db.execute(
        "INSERT INTO tcv_active_versions SELECT * FROM tcv_versions_seed WHERE layout='temporal' ORDER BY valid_from"
    )
    wal_wait_ms, wait_supported = db.wait_wal('tcv_active_versions')
    _, server_ms_fa, _, _ = db.execute(
        "INSERT INTO tcv_active_fragment_atoms SELECT * FROM tcv_fragment_atoms_seed WHERE layout='temporal'"
    )
    total_ms = (time.perf_counter_ns() - t0) / 1e6
    return {
        'migration_client_ms': total_ms,
        'migration_server_insert_ms': server_ms_v + server_ms_fa,
        'migration_wal_wait_ms': wal_wait_ms,
        'wait_wal_supported': wait_supported,
    }


def atom_list(atom_s: str):
    return [int(x) for x in str(atom_s).split(',') if str(x).strip()]


def resolver_sql(layout: str, atoms: list[int], lo: int, hi: int, active: bool = False):
    fa = 'tcv_active_fragment_atoms' if active else 'tcv_fragment_atoms_seed'
    vv = 'tcv_active_versions' if active else 'tcv_versions_seed'
    ins = ','.join(map(str, atoms))
    # QuestDB 10.0.1 treats IN (SELECT ...) as a cursor comparison.  Express
    # fragment resolution with a native equality join, then group away the
    # duplicate rows created when several queried atoms share one fragment.
    return f"""
      SELECT count() slices
      FROM (
        SELECT v.fragment_id, v.valid_from
        FROM {vv} v
        JOIN {fa} f
          ON v.layout=f.layout AND v.fragment_id=f.fragment_id
        WHERE v.layout='{layout}'
          AND f.atom_id IN ({ins})
          AND v.valid_from < {qdb_ts(hi)}
          AND v.valid_to > {qdb_ts(lo)}
        GROUP BY v.fragment_id, v.valid_from
      )
    """


def full_sql(layout: str, atoms: list[int], lo: int, hi: int, active: bool = False):
    fa = 'tcv_active_fragment_atoms' if active else 'tcv_fragment_atoms_seed'
    vv = 'tcv_active_versions' if active else 'tcv_versions_seed'
    ins = ','.join(map(str, atoms))
    # QuestDB does not support scalar subqueries in the SELECT list.  A
    # one-row CROSS JOIN keeps both native version resolution and the native
    # time-series interval aggregation inside one SQL statement.
    return f"""
      SELECT vr.slices, sg.signal_sum
      FROM (
        SELECT count() slices
        FROM (
          SELECT v.fragment_id, v.valid_from
          FROM {vv} v
          JOIN {fa} f
            ON v.layout=f.layout AND v.fragment_id=f.fragment_id
          WHERE v.layout='{layout}'
            AND f.atom_id IN ({ins})
            AND v.valid_from < {qdb_ts(hi)}
            AND v.valid_to > {qdb_ts(lo)}
          GROUP BY v.fragment_id, v.valid_from
        )
      ) vr
      CROSS JOIN (
        SELECT sum(value) signal_sum
        FROM tcv_signal
        WHERE atom_id IN ({ins})
          AND ts >= {qdb_ts(lo)} AND ts < {qdb_ts(hi)}
      ) sg
    """


def do_ingest(db: QuestDB, exec_t: int, nrows: int, seq0: int):
    sql = f"""
      INSERT INTO tcv_ingest
      SELECT {qdb_ts(exec_t)} ts,
             cast(({seq0}+x)%100 as int) sensor_id,
             sin(0.01*({seq0}+x)) + 0.01*cast(({seq0}+x)%100 as int) value
      FROM long_sequence({int(nrows)})
    """
    _, server_ms, compile_ms, client_ms = db.execute(sql)
    wal_ms, wait_supported = db.wait_wal('tcv_ingest')
    return seq0 + nrows, server_ms, compile_ms, client_ms, wal_ms, wait_supported


def replay(db: QuestDB, trace: pd.DataFrame, reps: int, warmup: int):
    rows = []
    seq = 1
    # Warm every distinct query under both layouts before timed repetitions.
    # A preliminary warm-up check using only the first 30 queries showed a strong first-in-pair
    # penalty, so query-specific cold compilation/page-open cost must not be
    # attributed to either physical layout.
    for _, q in trace.head(warmup).iterrows():
        ats = atom_list(q.atoms)
        for layout in ['static', 'temporal']:
            db.execute(full_sql(layout, ats, int(q.lo), int(q.hi)))

    for rep in range(reps):
        for _, q in trace.iterrows():
            qid = int(q.query_id)
            ats = atom_list(q.atoms)
            seq, ing_srv, ing_comp, ing_cli, wal_ms, wait_supported = do_ingest(
                db, int(q.exec_t), int(q.ingest_target_rows), seq
            )
            order = ['static', 'temporal'] if (rep + qid) % 2 == 0 else ['temporal', 'static']
            for layout in order:
                rj, r_srv, r_comp, r_cli = db.execute(resolver_sql(layout, ats, int(q.lo), int(q.hi)))
                fj, f_srv, f_comp, f_cli = db.execute(full_sql(layout, ats, int(q.lo), int(q.hi)))
                slices = rj.get('dataset', [[None]])[0][0] if rj.get('dataset') else None
                full_row = fj.get('dataset', [[None, None]])[0] if fj.get('dataset') else [None, None]
                full_slices = full_row[0] if len(full_row) > 0 else None
                signal_sum = full_row[1] if len(full_row) > 1 else None
                rows.append({
                    'rep': rep, 'query_id': qid, 'segment': q.segment,
                    'exec_t': int(q.exec_t), 'lo': int(q.lo), 'hi': int(q.hi),
                    'window': int(q.window), 'n_atoms': len(ats),
                    'ingest_target_rows': int(q.ingest_target_rows),
                    'layout': layout, 'pair_position': order.index(layout) + 1,
                    'version_slices': slices,
                    'full_version_slices': full_slices, 'signal_sum': signal_sum,
                    'resolver_server_ms': r_srv, 'resolver_compile_ms': r_comp,
                    'resolver_client_ms': r_cli,
                    'full_server_ms': f_srv, 'full_compile_ms': f_comp,
                    'full_client_ms': f_cli,
                    'ingest_server_ms': ing_srv, 'ingest_wal_wait_ms': wal_ms,
                    'wait_wal_supported': wait_supported,
                })
    return pd.DataFrame(rows)



def validate_native_results(raw: pd.DataFrame):
    """Validate semantic equivalence before any performance summary is trusted."""
    if raw.empty:
        raise RuntimeError('native replay produced no rows')
    key = ['rep', 'query_id']
    s = raw[raw.layout == 'static'].copy()
    t = raw[raw.layout == 'temporal'].copy()
    m = s.merge(t, on=key, suffixes=('_static', '_temporal'), validate='one_to_one')
    if len(m) * 2 != len(raw):
        raise RuntimeError('missing or duplicate static/temporal pairs in native replay')

    ss = pd.to_numeric(m.signal_sum_static, errors='coerce')
    ts = pd.to_numeric(m.signal_sum_temporal, errors='coerce')
    signal_abs_diff = (ss - ts).abs()
    signal_scale = pd.concat([ss.abs(), ts.abs()], axis=1).max(axis=1).clip(lower=1.0)
    signal_ok = signal_abs_diff <= (1e-10 * signal_scale)

    rs = pd.to_numeric(raw.version_slices, errors='coerce')
    fs = pd.to_numeric(raw.full_version_slices, errors='coerce')
    slice_ok = rs.eq(fs)

    report = {
        'paired_queries': int(len(m)),
        'signal_equivalent_pairs': int(signal_ok.sum()),
        'signal_max_abs_diff': float(signal_abs_diff.max()),
        'resolver_full_slice_matches': int(slice_ok.sum()),
        'resolver_full_slice_total': int(len(slice_ok)),
        'passed': bool(signal_ok.all() and slice_ok.all()),
    }
    if not report['passed']:
        raise RuntimeError('native replay correctness validation failed: ' + json.dumps(report))
    return report


def environment_report(db: QuestDB, ident: dict, host: str):
    trace_hash = hashlib.sha256(TRACE_FILE.read_bytes()).hexdigest()
    cpu_model = ''
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.lower().startswith('model name'):
                cpu_model = line.split(':', 1)[1].strip(); break
    except Exception:
        pass
    return {
        'questdb_build_response': ident,
        'host': host,
        'trace_file': TRACE_FILE.name,
        'trace_sha256': trace_hash,
        'platform': platform.platform(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'cpu_model': cpu_model,
        'cpu_count': os.cpu_count(),
        'python': sys.version,
        'timing_note': 'QuestDB REST timings are converted from nanoseconds to milliseconds; client_ms is local wall clock.',
    }

def summarize(raw: pd.DataFrame, migration_rows: list[dict]):
    """Summarize a position-balanced replay at the query level.

    Timed repeats are intentionally even (four in the released launcher), and
    pair order alternates by (rep + query_id).  We first average the paired
    timings within each query, so each layout contributes exactly two first-
    position and two second-position observations, then summarize across
    distinct queries.  This avoids pseudo-replication and first-in-pair bias.
    """
    out, qframes = [], []
    rng = np.random.default_rng(20260924)
    for seg in raw.segment.unique():
        s = raw[(raw.segment == seg) & (raw.layout == 'static')]
        t = raw[(raw.segment == seg) & (raw.layout == 'temporal')]
        m = s.merge(t, on=['rep', 'query_id'], suffixes=('_static', '_temporal'), validate='one_to_one')
        q = m.groupby('query_id').agg(
            static_server_ms=('full_server_ms_static', 'mean'),
            temporal_server_ms=('full_server_ms_temporal', 'mean'),
            static_client_ms=('full_client_ms_static', 'mean'),
            temporal_client_ms=('full_client_ms_temporal', 'mean'),
            static_resolver_ms=('resolver_server_ms_static', 'mean'),
            temporal_resolver_ms=('resolver_server_ms_temporal', 'mean'),
            static_slices=('version_slices_static', 'mean'),
            temporal_slices=('version_slices_temporal', 'mean'),
        ).reset_index()
        q['segment'] = seg
        q['server_saving_ms'] = q.static_server_ms - q.temporal_server_ms
        q['server_gain_pct'] = 100.0 * q.server_saving_ms / q.static_server_ms
        q['client_saving_ms'] = q.static_client_ms - q.temporal_client_ms
        q['client_gain_pct'] = 100.0 * q.client_saving_ms / q.static_client_ms
        q['resolver_saving_ms'] = q.static_resolver_ms - q.temporal_resolver_ms
        q['resolver_gain_pct'] = 100.0 * q.resolver_saving_ms / q.static_resolver_ms
        qframes.append(q)

        n = len(q)
        boot_gain, boot_delta = [], []
        for _ in range(5000):
            z = q.iloc[rng.integers(0, n, n)]
            boot_gain.append(float(z.server_gain_pct.median()))
            boot_delta.append(float(z.server_saving_ms.median()))
        out.append({
            'segment': seg,
            'queries': int(n),
            'static_slices_mean': float(q.static_slices.mean()),
            'temporal_slices_mean': float(q.temporal_slices.mean()),
            'slice_reduction_pct': float(100.0 * (1.0 - q.temporal_slices.mean() / q.static_slices.mean())),
            'resolver_gain_pct_median': float(q.resolver_gain_pct.median()),
            'server_gain_pct_median': float(q.server_gain_pct.median()),
            'server_gain_ci_lo': float(np.percentile(boot_gain, 2.5)),
            'server_gain_ci_hi': float(np.percentile(boot_gain, 97.5)),
            'server_saving_ms_median': float(q.server_saving_ms.median()),
            'server_saving_ci_lo': float(np.percentile(boot_delta, 2.5)),
            'server_saving_ci_hi': float(np.percentile(boot_delta, 97.5)),
            'client_gain_pct_median': float(q.client_gain_pct.median()),
            'positive_query_fraction': float((q.server_saving_ms > 0).mean()),
        })

    sdf = pd.DataFrame(out)
    qdf = pd.concat(qframes, ignore_index=True)
    mig_med = statistics.median([r['migration_client_ms'] for r in migration_rows]) if migration_rows else float('nan')
    cal = sdf[sdf.segment == 'calibration'].iloc[0]
    saving = float(cal.server_saving_ms_median)
    nbe = mig_med / saving if saving > 0 else float('inf')
    hold = qdf[qdf.segment != 'calibration'].sort_values('query_id')
    cum = 0.0
    realized = None
    for i, val in enumerate(hold.server_saving_ms, 1):
        cum += float(val)
        if realized is None and cum >= mig_med:
            realized = i
    sdf['migration_client_ms_median'] = mig_med
    sdf['calibration_predicted_break_even_queries'] = nbe
    sdf['holdout_realized_break_even_queries'] = realized if realized is not None else float('inf')
    return sdf, qdf

def concurrency_probe(db: QuestDB, trace: pd.DataFrame, readers: int):
    if readers <= 0:
        return pd.DataFrame(), {}
    install_active_static(db)
    qs = trace[trace.segment != 'calibration'].head(24).copy()
    stop = threading.Event()
    records = []
    lock = threading.Lock()
    mig_clock = {'start': None, 'end': None}

    def reader(worker: int):
        i = worker
        while not stop.is_set():
            q = qs.iloc[i % len(qs)]
            ats = atom_list(q.atoms)
            t0 = time.perf_counter()
            err = ''
            try:
                _, srv, _, cli = db.execute(full_sql('static', ats, int(q.lo), int(q.hi), active=True))
            except Exception as e:
                srv = float('nan'); cli = float('nan'); err = str(e)[:160]
            t1 = time.perf_counter()
            with lock:
                records.append({'worker': worker, 't0': t0, 't1': t1, 'server_ms': srv, 'client_ms': cli, 'error': err})
            i += readers

    with cf.ThreadPoolExecutor(max_workers=readers) as ex:
        futs = [ex.submit(reader, w) for w in range(readers)]
        time.sleep(0.5)
        mig_clock['start'] = time.perf_counter()
        mig = install_temporal(db)
        mig_clock['end'] = time.perf_counter()
        time.sleep(0.5)
        stop.set()
        for f in futs: f.result()

    rdf = pd.DataFrame(records)
    def phase(r):
        if r.t1 < mig_clock['start']: return 'pre'
        if r.t0 > mig_clock['end']: return 'post'
        return 'during'
    if not rdf.empty:
        rdf['phase'] = rdf.apply(phase, axis=1)
    summary = {'migration': mig, 'readers': readers}
    if not rdf.empty:
        for ph in ['pre', 'during', 'post']:
            x = rdf[rdf.phase == ph]
            summary[f'{ph}_n'] = int(len(x))
            summary[f'{ph}_errors'] = int((x.error != '').sum()) if len(x) else 0
            summary[f'{ph}_server_ms_median'] = float(x.server_ms.median()) if len(x) else float('nan')
            summary[f'{ph}_server_ms_p95'] = float(x.server_ms.quantile(.95)) if len(x) else float('nan')
    return rdf, summary


def dry_run(trace: pd.DataFrame):
    fa = sum(sum(len(f) for f in v) for v in LAYOUTS.values())
    vr = sum(len(build_version_rows(k)[1]) for k in LAYOUTS)
    print('DRY RUN OK')
    print('trace queries:', len(trace), 'segments:', trace.segment.value_counts().to_dict())
    print('signal rows:', 16*T, 'fragment memberships:', fa, 'version rows:', vr)
    q = trace.iloc[0]
    ats = atom_list(q.atoms)
    print('resolver SQL sample:\n', resolver_sql('static', ats, int(q.lo), int(q.hi))[:1000])
    print('full SQL sample:\n', full_sql('temporal', ats, int(q.lo), int(q.hi))[:1200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='http://127.0.0.1:9000')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--warmup', type=int, default=30)
    ap.add_argument('--migration-reps', type=int, default=15)
    ap.add_argument('--concurrent-readers', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    trace = pd.read_csv(TRACE_FILE)
    if args.dry_run:
        dry_run(trace); return

    db = QuestDB(args.host)
    # Health/identity check. QuestDB documents build() as a scalar meta function.
    ident, _, _, _ = db.execute("SELECT build() build")
    (DATA / 'RQ10_questdb_build.json').write_text(json.dumps(ident, indent=2), encoding='utf-8')
    (DATA / 'RQ10_questdb_environment.json').write_text(
        json.dumps(environment_report(db, ident, args.host), indent=2), encoding='utf-8'
    )
    setup_native_tables(db)
    raw = replay(db, trace, args.reps, args.warmup)
    raw.to_csv(DATA / 'RQ10_questdb_native_raw.csv', index=False)
    validation = validate_native_results(raw)
    (DATA / 'RQ10_questdb_validation.json').write_text(json.dumps(validation, indent=2), encoding='utf-8')

    migrations = []
    for rep in range(args.migration_reps):
        install_active_static(db)
        r = install_temporal(db); r['rep'] = rep; migrations.append(r)
    pd.DataFrame(migrations).to_csv(DATA / 'RQ10_questdb_migration.csv', index=False)

    sdf, qdf = summarize(raw, migrations)
    sdf.to_csv(DATA / 'RQ10_questdb_native_summary.csv', index=False)
    qdf.to_csv(DATA / 'RQ10_questdb_querylevel_raw.csv', index=False)

    cr, cs = concurrency_probe(db, trace, args.concurrent_readers)
    cr.to_csv(DATA / 'RQ10_questdb_concurrency_raw.csv', index=False)
    def _json_clean(x):
        if isinstance(x, dict): return {k: _json_clean(v) for k, v in x.items()}
        if isinstance(x, list): return [_json_clean(v) for v in x]
        if isinstance(x, float) and not math.isfinite(x): return None
        return x
    (DATA / 'RQ10_questdb_concurrency_summary.json').write_text(json.dumps(_json_clean(cs), indent=2, allow_nan=False), encoding='utf-8')

    print('\nCorrectness validation')
    print(json.dumps(validation, indent=2))
    print('\nQuestDB native summary')
    print(sdf.to_string(index=False))
    print('\nConcurrency summary')
    print(json.dumps(cs, indent=2))


if __name__ == '__main__':
    main()
