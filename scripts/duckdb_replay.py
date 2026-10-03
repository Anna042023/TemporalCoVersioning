#!/usr/bin/env python3
import csv, json, math, os, random, shutil, subprocess, tempfile
from pathlib import Path
import numpy as np
import pandas as pd

CLI = Path(os.environ.get('DUCKDB_CLI', 'duckdb'))
BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'reported'
OUT.mkdir(parents=True, exist_ok=True)
TMP = BASE / '_duckdb_bench'
if TMP.exists(): shutil.rmtree(TMP)
TMP.mkdir(parents=True)

if not CLI.exists() and shutil.which(str(CLI)) is None:
    raise SystemExit('DuckDB CLI not found. Set DUCKDB_CLI=/path/to/duckdb (v1.5.5).')

SEED=20260923
N_ATOMS=64
HOT=list(range(16))
T=2048
QUERY_ATOMS=list(range(8))
WINDOW=768
PAYLOAD=1024
N_QUERIES=120
RUNS=3
REPS=4


def updates_for_atom(a):
    if a < 16:
        pair=a//2
        phase=(pair*11)%96+16
        return list(range(phase,T,128))
    phase=(a*29)%64+4
    period=64+(a%3)*16
    return list(range(phase,T,period))


def layouts():
    cold=list(range(16,64))
    static=[]
    for i in range(8): static.append([2*i,2*i+1]+cold[6*i:6*i+6])
    temporal=[[2*i,2*i+1] for i in range(8)]
    temporal += [cold[6*i:6*i+6] for i in range(8)]
    return {'static':static,'temporal':temporal}

LAYOUTS=layouts(); UPDATES={a:updates_for_atom(a) for a in range(N_ATOMS)}


def build_csvs(run_dir):
    fa=run_dir/'fragment_atoms.csv'; vr=run_dir/'versions.csv'
    with fa.open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['layout','fragment_id','atom_id'])
        for layout,groups in LAYOUTS.items():
            for fid,atoms in enumerate(groups):
                for a in atoms: w.writerow([layout,fid,a])
    with vr.open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['layout','fragment_id','valid_from','valid_to','payload_bytes'])
        for layout,groups in LAYOUTS.items():
            for fid,atoms in enumerate(groups):
                b=sorted({t for a in atoms for t in UPDATES[a] if 0<t<T})
                cuts=[0]+b+[T]
                for lo,hi in zip(cuts[:-1],cuts[1:]): w.writerow([layout,fid,lo,hi,PAYLOAD])
    return fa,vr


def setup_db(db, run_dir):
    fa,vr=build_csvs(run_dir)
    setup=f"""
    PRAGMA threads=1;
    CREATE TABLE fragment_atoms AS SELECT * FROM read_csv_auto('{fa.as_posix()}', header=true);
    CREATE TABLE versions AS SELECT layout, fragment_id, valid_from, valid_to, repeat('x', payload_bytes::INTEGER) AS payload FROM read_csv_auto('{vr.as_posix()}', header=true);
    CREATE TABLE signal AS SELECT ((i-1)%16)::INTEGER AS atom_id, floor((i-1)/16)::INTEGER AS t,
      sin(0.13*((i-1)%16) + 0.017*floor((i-1)/16)) + 0.1*cos(0.031*floor((i-1)/16)) AS value
      FROM range(1,{16*T+1}) tbl(i);
    CREATE INDEX idx_fa ON fragment_atoms(layout, atom_id, fragment_id);
    CREATE INDEX idx_v ON versions(layout, fragment_id, valid_from, valid_to);
    CREATE INDEX idx_sig ON signal(atom_id, t);
    CHECKPOINT;
    """
    r=subprocess.run([str(CLI),str(db),'-c',setup],capture_output=True,text=True)
    if r.returncode!=0:
        raise RuntimeError(r.stderr+r.stdout)


def parse_json_stream(s):
    dec=json.JSONDecoder(); out=[]; i=0; n=len(s)
    while i<n:
        while i<n and s[i].isspace(): i+=1
        if i>=n: break
        obj,j=dec.raw_decode(s,i); out.append(obj); i=j
    return out

ATOMS=','.join(str(x) for x in QUERY_ATOMS)

def resolver_sql(tag,layout,lo,hi):
    return f"""WITH fids AS (
      SELECT DISTINCT fragment_id FROM fragment_atoms WHERE layout='{layout}' AND atom_id IN ({ATOMS})
    ), ov AS (
      SELECT payload FROM versions v JOIN fids f USING(fragment_id)
      WHERE v.layout='{layout}' AND v.valid_from<{hi} AND v.valid_to>{lo}
    ) SELECT '{tag}' AS tag, count(*)::BIGINT AS slices,
      coalesce(sum(octet_length(encode(payload))),0)::BIGINT AS bytes_seen FROM ov;"""


def full_sql(tag,layout,lo,hi):
    return f"""WITH fids AS (
      SELECT DISTINCT fragment_id FROM fragment_atoms WHERE layout='{layout}' AND atom_id IN ({ATOMS})
    ), ov AS (
      SELECT payload FROM versions v JOIN fids f USING(fragment_id)
      WHERE v.layout='{layout}' AND v.valid_from<{hi} AND v.valid_to>{lo}
    ), rr AS (
      SELECT count(*)::BIGINT AS slices, coalesce(sum(octet_length(encode(payload))),0)::BIGINT AS bytes_seen FROM ov
    ), ss AS (
      SELECT sum(value)::DOUBLE AS signal_sum FROM signal WHERE atom_id IN ({ATOMS}) AND t>={lo} AND t<{hi}
    ) SELECT '{tag}' AS tag, rr.slices, rr.bytes_seen, ss.signal_sum FROM rr, ss;"""


def run_phase(db, starts, run_id, phase):
    stmts=["PRAGMA threads=1;", "PRAGMA enable_profiling='json';"]
    # Query-specific warmup, profiling disabled, both layouts.
    stmts.append("PRAGMA disable_profiling;")
    for qid,lo in enumerate(starts):
        hi=lo+WINDOW
        for layout in ['static','temporal']:
            if phase=='resolver': stmts.append(resolver_sql(f'W_{phase}_{qid}_{layout}',layout,lo,hi))
            else: stmts.append(full_sql(f'W_{phase}_{qid}_{layout}',layout,lo,hi))
    stmts.append("PRAGMA enable_profiling='json';")
    tags=[]
    for rep in range(REPS):
        for qid,lo in enumerate(starts):
            hi=lo+WINDOW
            order=['static','temporal'] if (rep+qid)%2==0 else ['temporal','static']
            for pos,layout in enumerate(order):
                tag=f'R{run_id}_{phase}_P{rep}_Q{qid}_{layout}_O{pos}'
                tags.append((tag,rep,qid,layout,pos,lo,hi))
                stmts.append(resolver_sql(tag,layout,lo,hi) if phase=='resolver' else full_sql(tag,layout,lo,hi))
    script='\n'.join(stmts)
    r=subprocess.run([str(CLI),str(db),'-csv','-noheader'],input=script,capture_output=True,text=True)
    if r.returncode!=0:
        raise RuntimeError(r.stderr[-5000:]+"\nOUT\n"+r.stdout[-2000:])
    profiles=[o for o in parse_json_stream(r.stderr) if str(o.get('query_name','')).lstrip().startswith(('WITH ', 'SELECT '))]
    # Only timed phase should be profiled; warmups were disabled. Map by embedded tag in query_name.
    pmap={}
    for o in profiles:
        q=o.get('query_name','')
        for tag, *_ in tags:
            if f"'{tag}'" in q:
                pmap[tag]=o; break
    # Results include warmup + timed queries, but every row begins with tag. Parse only timed tags.
    result_rows={}
    for line in r.stdout.splitlines():
        if not line: continue
        row=next(csv.reader([line]))
        if row and row[0].startswith(f'R{run_id}_{phase}_'):
            result_rows[row[0]]=row
    rows=[]
    missing=[]
    for meta in tags:
        tag,rep,qid,layout,pos,lo,hi=meta
        if tag not in pmap or tag not in result_rows:
            missing.append(tag); continue
        prof=pmap[tag]; rr=result_rows[tag]
        if phase=='resolver':
            slices=int(rr[1]); bytes_seen=int(rr[2]); signal_sum=float('nan')
        else:
            slices=int(rr[1]); bytes_seen=int(rr[2]); signal_sum=float(rr[3])
        rows.append({
          'run_id':run_id,'phase':phase,'rep':rep,'query_id':qid,'layout':layout,'position':pos,
          'lo':lo,'hi':hi,'latency_ms':float(prof['latency'])*1000.0,
          'cpu_ms':float(prof.get('cpu_time',float('nan')))*1000.0,
          'rows_scanned':int(prof.get('cumulative_rows_scanned',0)),
          'slices':slices,'bytes_seen':bytes_seen,'signal_sum':signal_sum
        })
    if missing: raise RuntimeError(f'missing {len(missing)} profiles/results, e.g. {missing[:5]}')
    return rows


def bootstrap_median_ci(x,seed,n=10000):
    x=np.asarray(x,float); rg=np.random.default_rng(seed)
    med=np.median(x[rg.integers(0,len(x),size=(n,len(x)))],axis=1)
    return tuple(float(v) for v in np.quantile(med,[.025,.975]))

rng=random.Random(SEED+PAYLOAD)
starts=[rng.randrange(0,T-WINDOW) for _ in range(N_QUERIES)]
allrows=[]
for run_id in range(RUNS):
    rd=TMP/f'run{run_id}'; rd.mkdir(); db=rd/'bench.duckdb'
    setup_db(db,rd)
    allrows.extend(run_phase(db,starts,run_id,'resolver'))
    allrows.extend(run_phase(db,starts,run_id,'full'))
    print('completed run',run_id,flush=True)

df=pd.DataFrame(allrows)
df.to_csv(OUT/'RQ7_duckdb_raw.csv',index=False)
# Average repetitions and independent runs for each query/layout/phase.
q=(df.groupby(['phase','query_id','layout'],as_index=False)
      .agg(latency_ms=('latency_ms','mean'),cpu_ms=('cpu_ms','mean'),slices=('slices','mean'),bytes_seen=('bytes_seen','mean'),signal_sum=('signal_sum','mean')))
q.to_csv(OUT/'RQ7_duckdb_querylevel.csv',index=False)

# Semantic and slice validation from full phase.
full=q[q.phase=='full']
piv=full.pivot_table(index='query_id',columns='layout',values=['signal_sum','slices'],aggfunc='first')
sig_diff=(piv[('signal_sum','static')]-piv[('signal_sum','temporal')]).abs()
# Python exact structural slice count.
def exact_slices(layout,lo,hi):
    cnt=0
    for atoms in LAYOUTS[layout]:
        if not set(atoms).intersection(QUERY_ATOMS): continue
        b=sorted({t for a in atoms for t in UPDATES[a] if lo<t<hi})
        cnt += len(b)+1
    return cnt
slice_mis=0
for qid,lo in enumerate(starts):
    for layout in ['static','temporal']:
        obs=float(full[(full.query_id==qid)&(full.layout==layout)].slices.iloc[0])
        exp=exact_slices(layout,lo,lo+WINDOW)
        if abs(obs-exp)>1e-9: slice_mis+=1
validation={
 'engine':'DuckDB 1.5.5 CLI','source_id':'d8cdaa33fd','threads':1,'payload_bytes':PAYLOAD,
 'queries':N_QUERIES,'independent_fresh_databases':RUNS,'timed_repetitions_per_database':REPS,
 'timings_per_query_layout':RUNS*REPS,'semantic_pairs':N_QUERIES,
 'semantic_mismatches_gt_1e-9':int((sig_diff>1e-9).sum()),'max_signal_abs_diff':float(sig_diff.max()),
 'slice_layout_query_mismatches':slice_mis,
 'position_balance':'For each fresh DB and query, each layout appears first exactly two of four timed repetitions.'
}
(OUT/'RQ7_duckdb_validation.json').write_text(json.dumps(validation,indent=2))
if validation['semantic_mismatches_gt_1e-9'] or slice_mis: raise RuntimeError(validation)

summary={'engine':'DuckDB 1.5.5','payload_bytes':PAYLOAD,'queries':N_QUERIES}
for phase in ['resolver','full']:
    d=q[q.phase==phase]
    s=d[d.layout=='static'].set_index('query_id').latency_ms
    t=d[d.layout=='temporal'].set_index('query_id').latency_ms
    gain=(s-t)/s*100
    delta=s-t
    lo,hi=bootstrap_median_ci(gain,SEED+(1 if phase=='resolver' else 2))
    dlo,dhi=bootstrap_median_ci(delta,SEED+(3 if phase=='resolver' else 4))
    summary.update({
      f'{phase}_static_latency_ms_median':float(s.median()),
      f'{phase}_temporal_latency_ms_median':float(t.median()),
      f'{phase}_gain_pct_median':float(gain.median()),
      f'{phase}_gain_ci_lo':lo,f'{phase}_gain_ci_hi':hi,
      f'{phase}_delta_ms_median':float(delta.median()),
      f'{phase}_delta_ci_lo':dlo,f'{phase}_delta_ci_hi':dhi,
      f'{phase}_positive_query_fraction':float((gain>0).mean())
    })
summary['static_slices_mean']=float(full[full.layout=='static'].slices.mean())
summary['temporal_slices_mean']=float(full[full.layout=='temporal'].slices.mean())
summary['slice_reduction_pct']=100*(1-summary['temporal_slices_mean']/summary['static_slices_mean'])
(OUT/'RQ7_duckdb_summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
print(json.dumps(validation,indent=2))
