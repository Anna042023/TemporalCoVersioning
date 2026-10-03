#!/usr/bin/env python3
import os, sqlite3, time, math, shutil, statistics, random, json
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'reported'
OUT.mkdir(exist_ok=True)
TMP = BASE / '_monitoring_trace'
TMP.mkdir(exist_ok=True)

SEED = 20260923
N_ATOMS = 64
HOT = list(range(16))
# 30 days at one-minute resolution. This is intentionally a monitoring-style
# replay envelope, not a claim to reproduce TSM-Bench's private/complete trace.
T = 30 * 24 * 60
PAYLOAD_BYTES = 1024
QUERY_REPS = 4
BENCH_RUNS = 3
SEGMENTS = [
    ('calibration', 0, 10 * 24 * 60, 180),
    ('regular_holdout', 10 * 24 * 60, 20 * 24 * 60, 180),
    ('maintenance_burst', 20 * 24 * 60, 25 * 24 * 60, 120),
    ('window_drift', 25 * 24 * 60, T, 120),
]
MIGRATION_REPS = 15


def layouts():
    cold = list(range(16, 64))
    static = []
    for i in range(8):
        static.append([2*i, 2*i+1] + cold[6*i:6*i+6])
    temporal = [[2*i, 2*i+1] for i in range(8)]
    temporal += [cold[6*i:6*i+6] for i in range(8)]
    return {'static': static, 'temporal': temporal}

LAYOUTS = layouts()


def updates_for_atom(a):
    """Correlated monitoring-style invalidations with maintenance bursts.

    Hot pairs usually move together, while cold state is asynchronous. A small
    amount of hot-pair desynchronization and two maintenance intervals avoid a
    trivially perfect synthetic pattern.
    """
    rg = random.Random(SEED + 17 * a)
    out = []
    if a < 16:
        pair = a // 2
        phase = 90 + 19 * pair
        # Routine configuration/topology refresh, roughly every 12 hours.
        t = phase
        while t < T:
            jitter = rg.randint(-25, 25)
            out.append(max(1, min(T-1, t + jitter)))
            t += 12 * 60
        # Rare atom-local invalidations break perfect pair synchrony.
        for _ in range(6):
            out.append(rg.randrange(1, T))
    else:
        # Cold states change more asynchronously, with heterogeneous periods.
        period = [180, 240, 360, 480][a % 4]
        phase = rg.randrange(15, period)
        t = phase
        while t < T:
            out.append(max(1, min(T-1, t + rg.randint(-18, 18))))
            t += period
    # Maintenance bursts on days 21 and 23 affect many states.
    for day in [20, 22]:
        center = day * 24 * 60 + 2 * 60 + (a % 9) * 3
        if a >= 16 or a % 4 == 0:
            for off in [0, 12, 37]:
                tt = center + off
                if 0 < tt < T:
                    out.append(tt)
    return sorted(set(out))

UPDATES = {a: updates_for_atom(a) for a in range(N_ATOMS)}


def build_rows(layout_name, payload_bytes):
    fatoms, versions = [], []
    payload = bytes([17]) * payload_bytes
    for fid, atoms in enumerate(LAYOUTS[layout_name]):
        for a in atoms:
            fatoms.append((layout_name, fid, a))
        b = sorted({t for a in atoms for t in UPDATES[a] if 0 < t < T})
        cuts = [0] + b + [T]
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            versions.append((layout_name, fid, lo, hi, payload))
    return fatoms, versions


def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('PRAGMA temp_store=MEMORY')
    con.execute('PRAGMA cache_size=-65536')
    return con


def init_db(path, payload_bytes, include_temporal=True):
    if os.path.exists(path):
        os.remove(path)
    con = connect(path)
    con.executescript('''
    CREATE TABLE fragment_atoms(layout TEXT NOT NULL, fragment_id INTEGER NOT NULL, atom_id INTEGER NOT NULL);
    CREATE INDEX idx_fa ON fragment_atoms(layout, atom_id, fragment_id);
    CREATE TABLE versions(layout TEXT NOT NULL, fragment_id INTEGER NOT NULL, valid_from INTEGER NOT NULL, valid_to INTEGER NOT NULL, payload BLOB NOT NULL);
    CREATE INDEX idx_v ON versions(layout, fragment_id, valid_from, valid_to);
    CREATE TABLE signal(atom_id INTEGER NOT NULL, t INTEGER NOT NULL, value REAL NOT NULL);
    CREATE INDEX idx_sig ON signal(atom_id,t);
    CREATE TABLE ingest(seq INTEGER PRIMARY KEY, event_t INTEGER NOT NULL, sensor_id INTEGER NOT NULL, value REAL NOT NULL);
    ''')
    # 16 hot monitoring signals over 30 days. Chunk insert to control memory.
    chunk = []
    for a in HOT:
        for t in range(T):
            value = (math.sin(0.07*a + 0.0047*t)
                     + 0.18*math.cos(0.021*t)
                     + 0.04*math.sin(0.0009*t*(a+1)))
            chunk.append((a, t, value))
            if len(chunk) >= 20000:
                con.executemany('INSERT INTO signal VALUES (?,?,?)', chunk)
                chunk.clear()
    if chunk:
        con.executemany('INSERT INTO signal VALUES (?,?,?)', chunk)
    for name in ['static'] + (['temporal'] if include_temporal else []):
        fa, vr = build_rows(name, payload_bytes)
        con.executemany('INSERT INTO fragment_atoms VALUES (?,?,?)', fa)
        con.executemany('INSERT INTO versions VALUES (?,?,?,?,?)', vr)
    con.commit()
    return con


def weighted_choice(rg, items):
    xs, ws = zip(*items)
    r = rg.random() * sum(ws)
    acc = 0.0
    for x, w in items:
        acc += w
        if r <= acc:
            return x
    return xs[-1]


def trace_queries():
    """Joint workload, preserving correlations instead of independent sweeps.

    Range variability and online ingest/query interleaving are informed by the
    TSM-Bench monitoring benchmark interface. The exact event trace here is a
    deterministic quasi-real replay generated for this paper.
    """
    rg = random.Random(SEED + 991)
    rows = []
    qid = 0
    for name, seg_lo, seg_hi, n in SEGMENTS:
        for _ in range(n):
            exec_t = rg.randrange(seg_lo + 24*60, seg_hi)
            hour = (exec_t // 60) % 24
            # Daytime favors short operational views; night/reporting periods
            # favor longer windows. Final segment deliberately drifts longer.
            if name == 'window_drift':
                window = weighted_choice(rg, [(360, .20), (720, .25), (1440, .35), (2880, .20)])
            elif 7 <= hour <= 19:
                window = weighted_choice(rg, [(60, .25), (180, .35), (360, .25), (1440, .15)])
            else:
                window = weighted_choice(rg, [(180, .20), (360, .30), (720, .25), (1440, .25)])
            hi = max(window, exec_t - rg.randrange(0, 6*60 + 1))
            lo = max(0, hi - window)
            # Hot-pair locality with occasional wider cross-pair queries.
            npairs = weighted_choice(rg, [(1, .30), (2, .40), (3, .20), (4, .10)])
            pair0 = weighted_choice(rg, [(0,.24),(1,.18),(2,.15),(3,.12),(4,.10),(5,.08),(6,.07),(7,.06)])
            pairs = [(pair0 + j) % 8 for j in range(npairs)]
            atoms = sorted({2*p + b for p in pairs for b in [0,1]})
            # Interleaved write load; maintenance segment is much write heavier.
            if name == 'maintenance_burst':
                ingest = weighted_choice(rg, [(1000,.25),(5000,.45),(10000,.30)])
            elif name == 'window_drift':
                ingest = weighted_choice(rg, [(200,.30),(1000,.50),(5000,.20)])
            else:
                ingest = weighted_choice(rg, [(200,.45),(1000,.40),(5000,.15)])
            rows.append((qid, name, exec_t, lo, hi, window, ','.join(map(str, atoms)), ingest))
            qid += 1
    return rows

TRACE = trace_queries()


def do_ingest(con, seq0, exec_t, nrows, rg):
    # Batch semantics imitate mixed monitoring ingestion without claiming
    # concurrency equivalence to a production TSDB.
    rows = []
    for i in range(nrows):
        sid = rg.randrange(0, 100)
        val = math.sin(0.01*(seq0+i)) + 0.01*sid
        rows.append((seq0+i, exec_t, sid, val))
    con.executemany('INSERT INTO ingest VALUES (?,?,?,?)', rows)
    con.commit()
    return seq0 + nrows


def query_once(con, layout, atoms, lo, hi):
    ph = ','.join('?' for _ in atoms)
    t0 = time.perf_counter_ns()
    fids = [r[0] for r in con.execute(
        f'SELECT DISTINCT fragment_id FROM fragment_atoms WHERE layout=? AND atom_id IN ({ph}) ORDER BY fragment_id',
        [layout] + atoms)]
    fph = ','.join('?' for _ in fids)
    cnt, bytes_seen = con.execute(
        f'SELECT COUNT(*), COALESCE(SUM(length(payload)),0) FROM versions '
        f'WHERE layout=? AND fragment_id IN ({fph}) AND valid_from<? AND valid_to>?',
        [layout] + fids + [hi, lo]).fetchone()
    t1 = time.perf_counter_ns()
    signal_sum = con.execute(
        f'SELECT SUM(value) FROM signal WHERE atom_id IN ({ph}) AND t>=? AND t<?',
        atoms + [lo, hi]).fetchone()[0]
    t2 = time.perf_counter_ns()
    return (t1-t0)/1e6, (t2-t0)/1e6, cnt, bytes_seen, signal_sum


def replay(run_id):
    path = str(TMP / f'monitoring_replay_{run_id}.db')
    con = init_db(path, PAYLOAD_BYTES, include_temporal=True)
    # Query-specific warm-up for both layouts, without writes. The timed
    # four-repeat schedule below then balances first position two-to-two for
    # every distinct query.
    for q in TRACE:
        atoms = list(map(int, q[6].split(',')))
        for layout in ['static','temporal']:
            query_once(con, layout, atoms, q[3], q[4])
    rows = []
    seq = 1
    rng = random.Random(SEED + 12345 + 1000*run_id)
    # Cap inserted rows per event to keep the local replay practical while
    # preserving the workload's write-intensity ordering. Store scale separately.
    ingest_scale = 1
    for rep in range(QUERY_REPS):
        for q in TRACE:
            qid, seg, exec_t, lo, hi, window, atom_s, ingest_target = q
            atoms = list(map(int, atom_s.split(',')))
            nphys = max(1, ingest_target // ingest_scale)
            seq = do_ingest(con, seq, exec_t, nphys, rng)
            order = ['static','temporal'] if (rep + qid) % 2 == 0 else ['temporal','static']
            for layout in order:
                r_ms, f_ms, cnt, bseen, signal_sum = query_once(con, layout, atoms, lo, hi)
                rows.append((run_id,rep,qid,seg,exec_t,lo,hi,window,len(atoms),ingest_target,ingest_scale,
                             layout,r_ms,f_ms,cnt,bseen,signal_sum))
    con.close()
    return rows


def migrate_once(base_path, rep):
    work = TMP / f'mig_{rep}.db'
    for suffix in ['', '-wal', '-shm']:
        p = str(work) + suffix
        if os.path.exists(p): os.remove(p)
    shutil.copy2(base_path, work)
    con = connect(work)
    fa, vr = build_rows('temporal', PAYLOAD_BYTES)
    t0 = time.perf_counter_ns()
    con.execute('BEGIN IMMEDIATE')
    con.executemany('INSERT INTO fragment_atoms VALUES (?,?,?)', fa)
    con.executemany('INSERT INTO versions VALUES (?,?,?,?,?)', vr)
    con.commit()
    t1 = time.perf_counter_ns()
    con.close()
    for suffix in ['', '-wal', '-shm']:
        p = str(work) + suffix
        if os.path.exists(p): os.remove(p)
    return (t1-t0)/1e6


rows = []
for run_id in range(BENCH_RUNS):
    rows.extend(replay(run_id))
cols = ['run_id','rep','query_id','segment','exec_t','lo','hi','window','n_atoms','ingest_target_rows','ingest_scale',
        'layout','resolver_ms','full_ms','version_slices','payload_bytes_seen','signal_sum']
df = pd.DataFrame(rows, columns=cols)
df.to_csv(OUT / 'RQ9_monitoring_trace_raw.csv', index=False)

# Migration measurement on a static-only copy.
base = str(TMP / 'migration_base.db')
con = init_db(base, PAYLOAD_BYTES, include_temporal=False)
con.close()
mig = [migrate_once(base, run_id*MIGRATION_REPS + i) for run_id in range(BENCH_RUNS) for i in range(MIGRATION_REPS)]

def bootstrap_median_ci(values, seed, n_boot=10000):
    x=np.asarray(values,dtype=float)
    rg=np.random.default_rng(seed)
    idx=rg.integers(0,len(x),size=(n_boot,len(x)))
    med=np.median(x[idx],axis=1)
    lo,hi=np.quantile(med,[0.025,0.975])
    return float(lo),float(hi)

# First aggregate the four timing repetitions within each distinct query and
# layout. Segment medians and bootstrap intervals are then computed across
# distinct queries, not across repeated timing observations.
qlevel=(df.groupby(['query_id','segment','exec_t','lo','hi','window',
                    'n_atoms','ingest_target_rows','ingest_scale','layout'],as_index=False)
          .agg(resolver_ms=('resolver_ms','mean'),
               full_ms=('full_ms','mean'),
               version_slices=('version_slices','mean'),
               payload_bytes_seen=('payload_bytes_seen','mean'),
               signal_sum=('signal_sum','mean')))
qlevel.to_csv(OUT/'RQ9_monitoring_trace_querylevel.csv',index=False)

_sem=qlevel.pivot_table(index=['query_id'],columns='layout',values='signal_sum',aggfunc='first')
_abs=(_sem['static']-_sem['temporal']).abs()
_validation={
    'semantic_pairs': int(len(_abs)),
    'semantic_mismatches_gt_1e-9': int((_abs>1e-9).sum()),
    'max_signal_abs_diff': float(_abs.max()),
    'independent_runs': BENCH_RUNS,
    'timed_repetitions_per_run': QUERY_REPS,
    'timings_per_query_layout': BENCH_RUNS*QUERY_REPS,
    'position_balance': 'Within each run, each query/layout is first exactly two of four timed repetitions.'
}
(OUT/'RQ9_monitoring_trace_validation.json').write_text(json.dumps(_validation,indent=2))
if _validation['semantic_mismatches_gt_1e-9']:
    raise RuntimeError(f"RQ9 semantic gate failed: {_validation}")

summary = []
query_savings_by_segment={}
for seg, *_ in SEGMENTS:
    s0 = qlevel[(qlevel.segment == seg) & (qlevel.layout == 'static')]
    t0 = qlevel[(qlevel.segment == seg) & (qlevel.layout == 'temporal')]
    m = s0.merge(t0, on=['query_id'], suffixes=('_static','_temporal'))
    delta = m.full_ms_static - m.full_ms_temporal
    gain = delta / m.full_ms_static * 100
    rgain = (m.resolver_ms_static - m.resolver_ms_temporal) / m.resolver_ms_static * 100
    g_lo,g_hi=bootstrap_median_ci(gain,SEED+int(m.query_id.min())+1)
    d_lo,d_hi=bootstrap_median_ci(delta,SEED+int(m.query_id.min())+2)
    query_savings_by_segment[seg]=m[['query_id']].assign(saving_ms=delta.values)
    summary.append({
        'segment': seg,
        'queries': int(m.query_id.nunique()),
        'independent_runs':BENCH_RUNS,
        'timed_repetitions_per_run':QUERY_REPS,
        'timings_per_query_layout':BENCH_RUNS*QUERY_REPS,
        'window_min_median': float(m.window_static.median()),
        'ingest_target_rows_median': float(m.ingest_target_rows_static.median()),
        'static_slices_mean': float(m.version_slices_static.mean()),
        'temporal_slices_mean': float(m.version_slices_temporal.mean()),
        'resolver_gain_pct_median': float(rgain.median()),
        'full_gain_pct_median': float(gain.median()),
        'full_gain_ci_lo': g_lo,
        'full_gain_ci_hi': g_hi,
        'query_saving_ms_median': float(delta.median()),
        'query_saving_ci_lo': d_lo,
        'query_saving_ci_hi': d_hi,
        'positive_query_fraction': float((delta > 0).mean()),
    })

sdf = pd.DataFrame(summary)
cal = sdf[sdf.segment == 'calibration'].iloc[0]
mig_med = float(statistics.median(mig))
cal_nbe = mig_med / cal.query_saving_ms_median if cal.query_saving_ms_median > 0 else float('inf')
# Chronological holdout payback uses one aggregated saving per distinct query.
hold_q=pd.concat([query_savings_by_segment[s] for s in ['regular_holdout','maintenance_burst','window_drift']])
hold_q=hold_q.sort_values('query_id')
cum = 0.0
realized = None
for i, d in enumerate(hold_q.saving_ms, 1):
    cum += float(d)
    if realized is None and cum >= mig_med:
        realized = i
sdf['migration_ms_median'] = mig_med
sdf['calibration_predicted_break_even_queries'] = cal_nbe
sdf['holdout_realized_break_even_queries'] = realized if realized is not None else float('inf')
sdf.to_csv(OUT / 'RQ9_monitoring_trace_summary.csv', index=False)

trace_df = pd.DataFrame(TRACE, columns=['query_id','segment','exec_t','lo','hi','window','atoms','ingest_target_rows'])
trace_df.to_csv(OUT / 'RQ9_monitoring_trace_events.csv', index=False)

print(sdf.to_string(index=False))
print('migration median ms', mig_med)
print('calibration predicted break-even', cal_nbe)
print('holdout realized break-even', realized)
print('sqlite_version', sqlite3.sqlite_version)
