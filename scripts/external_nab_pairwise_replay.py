#!/usr/bin/env python3
"""RQ11 pairwise reproducer for the released NAB traces.

For each matched pair, fit quantile thresholds and select the temporal matching
on the control episode only, freeze the resulting design, and evaluate the
matched anomaly. The script also verifies the resulting slice counts in SQLite
and writes the pair-stratified pooled bootstrap used in the paper.
"""
from __future__ import annotations
import argparse, itertools, json, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

BASE=Path(__file__).resolve().parent.parent
DATA=BASE/'data'/'reported'
TRACE_CONFIG={
 'cpu': (DATA/'NAB_AWS_ec2_cpu_53ea38_four_windows.csv', 'RQ11_nab_pairwise'),
 'network': (DATA/'NAB_AWS_ec2_network_in_5abac7_four_windows.csv', 'RQ11_network_pairwise'),
}
SEED=20260924
BOOT=20000
PAIRS=[('pair1','normal1','anomaly1'),('pair2','normal2','anomaly2')]
TEMPLATES=[
 ((0,1),6,'short_level'),((2,3),12,'medium'),((4,5),24,'long'),
 ((6,7),12,'trend'),((0,2,4,6),24,'level_family'),
 ((1,3,5,7),24,'variation_family'),((0,1,6,7),36,'fast_trend'),
 ((2,3,4,5),36,'slow_context')]

def features(vals):
 s=pd.Series(vals,dtype=float)
 return pd.DataFrame({
  0:s.rolling(3,min_periods=1).mean(),1:s.rolling(3,min_periods=1).max(),
  2:s.rolling(6,min_periods=1).mean(),3:s.rolling(6,min_periods=2).std().fillna(0),
  4:s.rolling(12,min_periods=1).mean(),5:s.rolling(12,min_periods=1).max(),
  6:s.ewm(alpha=.3,adjust=False).mean(),7:(s-s.shift(6)).fillna(0)})

def pm(items):
 items=tuple(items)
 if not items:
  yield (); return
 a=items[0]
 for j in range(1,len(items)):
  b=items[j]; rest=items[1:j]+items[j+1:]
  for m in pm(rest): yield ((a,b),)+m
MATCHINGS=list(pm(range(8)))

def queries():
 out=[]; qid=0
 for hi in range(12,49,4):
  for atoms,w,name in TEMPLATES:
   out.append({'qid':qid,'lo':max(0,hi-w),'hi':hi,'atoms':tuple(atoms),
               'template':name,'window_samples':w}); qid+=1
 return out

def touch(m,qs):
 return sum(sum(bool(set(q['atoms']).intersection(p)) for p in m) for q in qs)

def hes(m,q,up):
 A=set(q['atoms']); total=0
 for p in m:
  if A.intersection(p):
   bs=sorted(set(up[p[0]])|set(up[p[1]]))
   total += 1 + sum(q['lo'] < b < q['hi'] for b in bs)
 return total

def fit_pair(df,cal,held,bins=4):
 F={}
 for seg in (cal,held):
  x=df[df.segment==seg].sort_values('sample_index')
  F[seg]=features(x.value.to_numpy())
 probs=np.linspace(0,1,bins+1)[1:-1]
 edges={c:np.unique(np.quantile(F[cal][c],probs)) for c in range(8)}
 up={}
 for seg in (cal,held):
  up[seg]={}
  for c in range(8):
   st=np.digitize(F[seg][c].to_numpy(),edges[c],right=False)
   up[seg][c]=[i for i in range(1,len(st)) if st[i]!=st[i-1]]
 qs=queries()
 static=min(MATCHINGS,key=lambda m:(touch(m,qs),m))
 temporal=min(MATCHINGS,key=lambda m:(sum(hes(m,q,up[cal]) for q in qs),touch(m,qs),m))
 return up,qs,static,temporal

def bootstrap_pair(g,rng):
 vals=[]; n=len(g)
 for _ in range(BOOT):
  z=g.iloc[rng.integers(0,n,n)]
  vals.append(100*(z.static_hes.sum()-z.temporal_hes.sum())/z.static_hes.sum())
 p=100*(g.static_hes.sum()-g.temporal_hes.sum())/g.static_hes.sum()
 return p,float(np.percentile(vals,2.5)),float(np.percentile(vals,97.5))

def pooled_bootstrap(groups,rng):
 vals=[]
 for _ in range(BOOT):
  ss=tt=0
  for g in groups:
   z=g.iloc[rng.integers(0,len(g),len(g))]
   ss+=z.static_hes.sum(); tt+=z.temporal_hes.sum()
  vals.append(100*(ss-tt)/ss)
 ss=sum(g.static_hes.sum() for g in groups); tt=sum(g.temporal_hes.sum() for g in groups)
 return {'pairs':len(groups),'queries':sum(len(g) for g in groups),
         'static_hes':int(ss),'temporal_hes':int(tt),
         'reduction_pct':100*(ss-tt)/ss,
         'ci_lo':float(np.percentile(vals,2.5)),
         'ci_hi':float(np.percentile(vals,97.5)),
         'bootstrap':'pair-stratified query bootstrap','replicates':BOOT,'seed':SEED}

def sqlite_check(df,held,up,qs,static,temporal):
 vals=df[df.segment==held].sort_values('sample_index').value.to_numpy()
 con=sqlite3.connect(':memory:')
 con.executescript('CREATE TABLE fa(layout TEXT,fid INT,atom INT); CREATE TABLE v(layout TEXT,fid INT,lo INT,hi INT); CREATE TABLE signal(i INT,value REAL);')
 con.executemany('INSERT INTO signal VALUES (?,?)',[(i,float(v)) for i,v in enumerate(vals)])
 for lname,m in [('static',static),('temporal',temporal)]:
  for fid,pair in enumerate(m):
   con.executemany('INSERT INTO fa VALUES (?,?,?)',[(lname,fid,int(a)) for a in pair])
   bs=sorted(set(up[held][pair[0]])|set(up[held][pair[1]])); cuts=[0]+bs+[len(vals)]
   con.executemany('INSERT INTO v VALUES (?,?,?,?)',[(lname,fid,int(a),int(b)) for a,b in zip(cuts[:-1],cuts[1:])])
 mism=0; slice_checks=0; signal_checks=0
 for q in qs:
  ph=','.join('?'*len(q['atoms'])); got={}; sums={}
  for lname,m in [('static',static),('temporal',temporal)]:
   got[lname]=con.execute(f'''SELECT COUNT(*) FROM v WHERE layout=? AND fid IN (SELECT DISTINCT fid FROM fa WHERE layout=? AND atom IN ({ph})) AND lo<? AND hi>?''',[lname,lname,*q['atoms'],q['hi'],q['lo']]).fetchone()[0]
   sums[lname]=con.execute('SELECT SUM(value) FROM signal WHERE i>=? AND i<?',[q['lo'],q['hi']]).fetchone()[0]
   slice_checks+=1
  if got['static']!=hes(static,q,up[held]) or got['temporal']!=hes(temporal,q,up[held]): mism+=1
  signal_checks+=1
  if abs(sums['static']-sums['temporal'])>1e-12: mism+=1
 con.close()
 return {'slice_checks':slice_checks,'signal_checks':signal_checks,'mismatches':mism,'passed':mism==0}

def parse_args():
 p=argparse.ArgumentParser(description='Replay the released RQ11 NAB pairwise experiment.')
 p.add_argument('--trace', choices=tuple(TRACE_CONFIG), default='cpu',
                help='released trace to replay (default: cpu)')
 return p.parse_args()

def main():
 args=parse_args()
 trace,prefix=TRACE_CONFIG[args.trace]
 df=pd.read_csv(trace); rng=np.random.default_rng(SEED)
 all_rows=[]; groups=[]; designs=[]; summaries=[]; sensitivity=[]; validations=[]
 for name,cal,held in PAIRS:
  up,qs,static,temporal=fit_pair(df,cal,held,4)
  designs.append({'pair':name,'calibration':cal,'heldout':held,
                  'static_layout':str(static),'temporal_layout':str(temporal)})
  rows=[]
  for q in qs:
   r={'pair':name,'calibration':cal,'heldout':held,**q,
      'atoms':','.join(map(str,q['atoms'])),
      'static_hes':hes(static,q,up[held]),'temporal_hes':hes(temporal,q,up[held])}
   rows.append(r); all_rows.append(r)
  g=pd.DataFrame(rows); groups.append(g)
  p,lo,hi=bootstrap_pair(g,rng)
  summaries.append({'pair':name,'queries':len(g),'static_hes':int(g.static_hes.sum()),
                    'temporal_hes':int(g.temporal_hes.sum()),'reduction_pct':p,
                    'ci_lo':lo,'ci_hi':hi})
  validations.append({'pair':name,**sqlite_check(df,held,up,qs,static,temporal)})
  for bins in (2,3,4,5,6):
   u2,q2,s2,t2=fit_pair(df,cal,held,bins)
   cs=sum(hes(s2,q,u2[held]) for q in q2); ct=sum(hes(t2,q,u2[held]) for q in q2)
   sensitivity.append({'pair':name,'bins':bins,'static_hes':cs,'temporal_hes':ct,
                       'reduction_pct':100*(cs-ct)/cs,
                       'static_layout':str(s2),'temporal_layout':str(t2)})
 pd.DataFrame(all_rows).to_csv(DATA/f'{prefix}_querylevel.csv',index=False)
 pd.DataFrame(summaries).to_csv(DATA/f'{prefix}_replication.csv',index=False)
 pd.DataFrame(sensitivity).to_csv(DATA/f'{prefix}_bin_sensitivity.csv',index=False)
 pd.DataFrame(validations).to_csv(DATA/f'{prefix}_sqlite_validation.csv',index=False)
 (DATA/f'{prefix}_designs.json').write_text(json.dumps(designs,indent=2)+'\n')
 pooled=pooled_bootstrap(groups,rng)
 (DATA/f'{prefix}_pooled.json').write_text(json.dumps(pooled,indent=2)+'\n')
 print(pd.DataFrame(summaries).to_string(index=False)); print(pd.DataFrame(validations).to_string(index=False)); print(json.dumps(pooled,indent=2))

if __name__=='__main__': main()
