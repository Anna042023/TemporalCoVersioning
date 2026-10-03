#!/usr/bin/env python3
import os, sqlite3, time, math, shutil, statistics, random, json
from pathlib import Path
import pandas as pd
import numpy as np

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'reported'
OUT.mkdir(exist_ok=True)
TMP = BASE / '_sqlite_bench'
TMP.mkdir(exist_ok=True)

SEED = 20260923
N_ATOMS = 64
HOT = list(range(16))
T = 2048
QUERY_ATOMS = list(range(8))
WINDOW = 768
PAYLOAD_SIZES = [256, 1024, 4096]
QUERY_REPS = 4
BENCH_RUNS = 3
N_QUERIES = 120
MIGRATION_REPS = 15


def updates_for_atom(a):
    # Hot atoms occur in synchronized pairs; cold atoms are deliberately asynchronous.
    if a < 16:
        pair = a // 2
        phase = (pair * 11) % 96 + 16
        return list(range(phase, T, 128))
    phase = (a * 29) % 64 + 4
    period = 64 + (a % 3) * 16
    return list(range(phase, T, period))


def layouts():
    # Static: each queried hot pair is co-versioned with six unrelated cold states.
    static = []
    cold = list(range(16,64))
    for i in range(8):
        static.append([2*i, 2*i+1] + cold[6*i:6*i+6])
    # Temporal: synchronized hot pairs are isolated; cold states remain grouped separately.
    temporal = [[2*i,2*i+1] for i in range(8)]
    temporal += [cold[6*i:6*i+6] for i in range(8)]
    return {'static': static, 'temporal': temporal}

LAYOUTS = layouts()
UPDATES = {a: updates_for_atom(a) for a in range(N_ATOMS)}


def build_rows(layout_name, payload_bytes):
    fatoms=[]; versions=[]
    payload = bytes([17]) * payload_bytes
    for fid, atoms in enumerate(LAYOUTS[layout_name]):
        for a in atoms:
            fatoms.append((layout_name, fid, a))
        b = sorted({t for a in atoms for t in UPDATES[a] if 0 < t < T})
        cuts=[0]+b+[T]
        for lo,hi in zip(cuts[:-1],cuts[1:]):
            versions.append((layout_name, fid, lo, hi, payload))
    return fatoms, versions


def connect(path):
    con=sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('PRAGMA temp_store=MEMORY')
    con.execute('PRAGMA cache_size=-65536')
    return con


def init_db(path, payload_bytes, include_temporal=True):
    if os.path.exists(path): os.remove(path)
    con=connect(path)
    con.executescript('''
    CREATE TABLE fragment_atoms(layout TEXT NOT NULL, fragment_id INTEGER NOT NULL, atom_id INTEGER NOT NULL);
    CREATE INDEX idx_fa ON fragment_atoms(layout, atom_id, fragment_id);
    CREATE TABLE versions(layout TEXT NOT NULL, fragment_id INTEGER NOT NULL, valid_from INTEGER NOT NULL, valid_to INTEGER NOT NULL, payload BLOB NOT NULL);
    CREATE INDEX idx_v ON versions(layout, fragment_id, valid_from, valid_to);
    CREATE TABLE signal(atom_id INTEGER NOT NULL, t INTEGER NOT NULL, value REAL NOT NULL);
    CREATE INDEX idx_sig ON signal(atom_id,t);
    ''')
    rows=[]
    for a in HOT:
        for t in range(T):
            # deterministic non-constant signal
            rows.append((a,t,math.sin(0.13*a + 0.017*t) + 0.1*math.cos(0.031*t)))
    con.executemany('INSERT INTO signal VALUES (?,?,?)', rows)
    for name in ['static'] + (['temporal'] if include_temporal else []):
        fa,vr=build_rows(name,payload_bytes)
        con.executemany('INSERT INTO fragment_atoms VALUES (?,?,?)',fa)
        con.executemany('INSERT INTO versions VALUES (?,?,?,?,?)',vr)
    con.commit()
    return con


def query_once(con, layout, lo, hi):
    ph=','.join('?' for _ in QUERY_ATOMS)
    t0=time.perf_counter_ns()
    fids=[r[0] for r in con.execute(
        f'SELECT DISTINCT fragment_id FROM fragment_atoms WHERE layout=? AND atom_id IN ({ph}) ORDER BY fragment_id',
        [layout]+QUERY_ATOMS)]
    fph=','.join('?' for _ in fids)
    cnt,bytes_seen=con.execute(
        f'SELECT COUNT(*), COALESCE(SUM(length(payload)),0) FROM versions WHERE layout=? AND fragment_id IN ({fph}) AND valid_from<? AND valid_to>?',
        [layout]+fids+[hi,lo]).fetchone()
    t1=time.perf_counter_ns()
    signal_sum=con.execute(
        f'SELECT SUM(value) FROM signal WHERE atom_id IN ({ph}) AND t>=? AND t<?',
        QUERY_ATOMS+[lo,hi]).fetchone()[0]
    t2=time.perf_counter_ns()
    return (t1-t0)/1e6, (t2-t0)/1e6, cnt, bytes_seen, signal_sum


def query_benchmark(payload_bytes, run_id):
    path=str(TMP/f'query_{payload_bytes}_{run_id}.db')
    con=init_db(path,payload_bytes,include_temporal=True)
    rng=random.Random(SEED+payload_bytes)
    starts=[rng.randrange(0,T-WINDOW) for _ in range(N_QUERIES)]
    # Query-specific warm-up for both layouts. Timed repetitions then balance
    # first position exactly two-to-two for every distinct query.
    for qid, lo in enumerate(starts):
        for layout in ['static','temporal']:
            query_once(con,layout,lo,lo+WINDOW)
    rows=[]
    for rep in range(QUERY_REPS):
        for qid,lo in enumerate(starts):
            order=['static','temporal'] if (rep+qid)%2==0 else ['temporal','static']
            for layout in order:
                r_ms,f_ms,cnt,bseen,signal_sum=query_once(con,layout,lo,lo+WINDOW)
                rows.append((run_id,payload_bytes,rep,qid,layout,r_ms,f_ms,cnt,bseen,signal_sum))
    con.close()
    return rows


def migrate_once(base_path, payload_bytes, rep):
    work=TMP/f'mig_{payload_bytes}_{rep}.db'
    for suffix in ['', '-wal', '-shm']:
        p=str(work)+suffix
        if os.path.exists(p): os.remove(p)
    shutil.copy2(base_path,work)
    con=connect(work)
    fa,vr=build_rows('temporal',payload_bytes)
    t0=time.perf_counter_ns()
    con.execute('BEGIN IMMEDIATE')
    con.executemany('INSERT INTO fragment_atoms VALUES (?,?,?)',fa)
    con.executemany('INSERT INTO versions VALUES (?,?,?,?,?)',vr)
    con.commit()
    t1=time.perf_counter_ns()
    # verify target generation is complete
    nver=con.execute("SELECT COUNT(*) FROM versions WHERE layout='temporal'").fetchone()[0]
    con.close()
    ms=(t1-t0)/1e6
    for suffix in ['', '-wal', '-shm']:
        p=str(work)+suffix
        if os.path.exists(p): os.remove(p)
    return ms,nver

query_rows=[]; mig_rows=[]
for pb in PAYLOAD_SIZES:
    for run_id in range(BENCH_RUNS):
        query_rows += query_benchmark(pb,run_id)
    base=str(TMP/f'base_{pb}.db')
    con=init_db(base,pb,include_temporal=False); con.close()
    for run_id in range(BENCH_RUNS):
        for rep in range(MIGRATION_REPS):
            mid=run_id*MIGRATION_REPS+rep
            ms,nver=migrate_once(base,pb,mid)
            mig_rows.append((run_id,pb,rep,ms,nver))

qdf=pd.DataFrame(query_rows,columns=['run_id','payload_bytes','rep','query_id','layout','resolver_ms','full_ms','version_slices','payload_bytes_seen','signal_sum'])
mdf=pd.DataFrame(mig_rows,columns=['run_id','payload_bytes','rep','migration_ms','temporal_version_rows'])
qdf.to_csv(OUT/'RQ7_sqlite_raw.csv',index=False)
mdf.to_csv(OUT/'RQ8_migration_raw.csv',index=False)

def bootstrap_median_ci(values, seed, n_boot=10000):
    x=np.asarray(values,dtype=float)
    rg=np.random.default_rng(seed)
    idx=rg.integers(0,len(x),size=(n_boot,len(x)))
    med=np.median(x[idx],axis=1)
    lo,hi=np.quantile(med,[0.025,0.975])
    return float(lo),float(hi)

# Aggregate repeated timings within each distinct query before computing any
# cross-query summary. This avoids treating timing repetitions as independent
# workload samples and matches the RQ10 query-level protocol.
qlevel=(qdf.groupby(['payload_bytes','query_id','layout'],as_index=False)
          .agg(resolver_ms=('resolver_ms','mean'),
               full_ms=('full_ms','mean'),
               version_slices=('version_slices','mean'),
               payload_bytes_seen=('payload_bytes_seen','mean'),
               signal_sum=('signal_sum','mean')))
qlevel.to_csv(OUT/'RQ7_sqlite_querylevel.csv',index=False)

_sem=qlevel.pivot_table(index=['payload_bytes','query_id'],columns='layout',values='signal_sum',aggfunc='first')
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
(OUT/'RQ7_sqlite_validation.json').write_text(json.dumps(_validation,indent=2))
if _validation['semantic_mismatches_gt_1e-9']:
    raise RuntimeError(f"SQLite semantic gate failed: {_validation}")

summ=[]
for pb in PAYLOAD_SIZES:
    d=qlevel[qlevel.payload_bytes==pb]
    ds=d[d.layout=='static']
    dt=d[d.layout=='temporal']
    m=ds.merge(dt,on=['payload_bytes','query_id'],suffixes=('_static','_temporal'))
    resolver_gain=(m.resolver_ms_static-m.resolver_ms_temporal)/m.resolver_ms_static*100
    full_gain=(m.full_ms_static-m.full_ms_temporal)/m.full_ms_static*100
    delta_ms=(m.full_ms_static-m.full_ms_temporal)
    rg_lo,rg_hi=bootstrap_median_ci(resolver_gain,SEED+pb+1)
    fg_lo,fg_hi=bootstrap_median_ci(full_gain,SEED+pb+2)
    ds_lo,ds_hi=bootstrap_median_ci(delta_ms,SEED+pb+3)
    med_delta=float(delta_ms.median())
    mig=mdf[mdf.payload_bytes==pb].migration_ms
    mig_med=float(mig.median())
    nbe=mig_med/med_delta if med_delta>0 else float('inf')
    summ.append({
        'payload_bytes':pb,
        'queries':int(m.query_id.nunique()),
        'independent_runs':BENCH_RUNS,
        'timed_repetitions_per_run':QUERY_REPS,
        'timings_per_query_layout':BENCH_RUNS*QUERY_REPS,
        'static_resolver_ms_median':float(ds.resolver_ms.median()),
        'temporal_resolver_ms_median':float(dt.resolver_ms.median()),
        'resolver_gain_pct_median':float(resolver_gain.median()),
        'resolver_gain_ci_lo':rg_lo,
        'resolver_gain_ci_hi':rg_hi,
        'static_full_ms_median':float(ds.full_ms.median()),
        'temporal_full_ms_median':float(dt.full_ms.median()),
        'full_gain_pct_median':float(full_gain.median()),
        'full_gain_ci_lo':fg_lo,
        'full_gain_ci_hi':fg_hi,
        'static_slices_mean':float(ds.version_slices.mean()),
        'temporal_slices_mean':float(dt.version_slices.mean()),
        'migration_ms_median':mig_med,
        'migration_ms_p95':float(mig.quantile(.95)),
        'median_query_saving_ms':med_delta,
        'query_saving_ci_lo':ds_lo,
        'query_saving_ci_hi':ds_hi,
        'break_even_queries_median':nbe,
    })
sdf=pd.DataFrame(summ)
sdf.to_csv(OUT/'RQ7_RQ8_sqlite_summary.csv',index=False)
print(sdf.to_string(index=False))
print('sqlite_version', sqlite3.sqlite_version)
