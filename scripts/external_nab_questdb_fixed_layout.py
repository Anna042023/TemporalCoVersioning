#!/usr/bin/env python3
import json, time
from pathlib import Path
import numpy as np, pandas as pd, requests

BASE=Path(__file__).resolve().parent.parent
DATA=BASE/"data"/"reported"
TRACE=pd.read_csv(DATA/"NAB_AWS_ec2_cpu_53ea38_four_windows.csv")
Q=pd.read_csv(DATA/"RQ11_questdb_fixed_layout_expected.csv")
DESIGN=json.loads((DATA/"RQ11_questdb_fixed_layout_design.json").read_text())
REPS=4
BOOT=10000
SEED=20260924

class QDB:
    def __init__(self, host="http://127.0.0.1:9000"):
        self.host=host.rstrip("/")
        self.s=requests.Session()
    def exec(self,sql,timings=True):
        t0=time.perf_counter_ns()
        r=self.s.get(self.host+"/api/v1/sql/execute",
                     params={"query":sql,"timings":"true" if timings else "false"},
                     timeout=120)
        client=(time.perf_counter_ns()-t0)/1e6
        o=r.json()
        if not r.ok or "error" in o:
            raise RuntimeError(f"{o}\nSQL={sql}")
        ti=o.get("timings") or {}
        return o,float(ti.get("execute",0))/1e6,float(ti.get("compiler",0))/1e6,client
    def wait(self,table):
        self.exec(f"SELECT wait_wal_table('{table}')")

def q(s): return "'" + str(s).replace("'","''") + "'"

def build_features():
    # Recompute the exact four-bin state changes for the fixed-layout semantic check.
    def features(vals):
        s=pd.Series(vals,dtype=float)
        return pd.DataFrame({
            0:s.rolling(3,min_periods=1).mean(),1:s.rolling(3,min_periods=1).max(),
            2:s.rolling(6,min_periods=1).mean(),3:s.rolling(6,min_periods=2).std().fillna(0),
            4:s.rolling(12,min_periods=1).mean(),5:s.rolling(12,min_periods=1).max(),
            6:s.ewm(alpha=.3,adjust=False).mean(),7:(s-s.shift(6)).fillna(0),
        })
    f={}
    for seg,g in TRACE.groupby("segment",sort=False):
        f[seg]=features(g.sort_values("sample_index").value.to_numpy())
    edges={c:np.unique(np.quantile(f["normal1"][c],[.25,.5,.75])) for c in range(8)}
    updates={}
    for seg in f:
        updates[seg]={}
        for c in range(8):
            st=np.digitize(f[seg][c].to_numpy(),edges[c],right=False)
            updates[seg][c]=[i for i in range(1,len(st)) if st[i]!=st[i-1]]
    return updates

def setup(db):
    for t in ["nab_signal","nab_versions","nab_fragment_atoms"]:
        try: db.exec(f"DROP TABLE IF EXISTS {t}")
        except Exception: pass
    db.exec("CREATE TABLE nab_signal(ts TIMESTAMP, segment SYMBOL, value DOUBLE) TIMESTAMP(ts) PARTITION BY DAY WAL")
    db.exec("CREATE TABLE nab_fragment_atoms(layout SYMBOL, fragment_id INT, atom_id INT)")
    db.exec("CREATE TABLE nab_versions(valid_from TIMESTAMP, valid_to TIMESTAMP, segment SYMBOL, layout SYMBOL, fragment_id INT, payload_bytes INT) TIMESTAMP(valid_from) PARTITION BY DAY WAL")
    # signals
    vals=[]
    for r in TRACE.itertuples():
        vals.append(f"({q(r.timestamp)},{q(r.segment)},{float(r.value)})")
    for i in range(0,len(vals),80):
        db.exec("INSERT INTO nab_signal VALUES "+",".join(vals[i:i+80]))
    db.wait("nab_signal")
    # layouts
    static=[tuple(x) for x in DESIGN["static_layout"]]
    temporal=[tuple(x) for x in DESIGN["temporal_layout"]]
    fa=[]
    for lname,matching in [("static",static),("temporal",temporal)]:
        for fid,p in enumerate(matching):
            for a in p: fa.append(f"({q(lname)},{fid},{a})")
    db.exec("INSERT INTO nab_fragment_atoms VALUES "+",".join(fa))
    updates=build_features()
    starts={seg:pd.Timestamp(g.sort_values("sample_index").timestamp.iloc[0])
            for seg,g in TRACE.groupby("segment",sort=False)}
    vr=[]
    for lname,matching in [("static",static),("temporal",temporal)]:
        for fid,p in enumerate(matching):
            for seg,start in starts.items():
                bs=sorted(set(updates[seg][p[0]])|set(updates[seg][p[1]]))
                cuts=[0]+bs+[49]
                for lo,hi in zip(cuts[:-1],cuts[1:]):
                    a=start+pd.Timedelta(minutes=5*lo)
                    b=start+pd.Timedelta(minutes=5*hi)
                    vr.append((a, f"({q(str(a))},{q(str(b))},{q(seg)},{q(lname)},{fid},1024)"))
    vr.sort(key=lambda x:x[0])
    vals=[x[1] for x in vr]
    for i in range(0,len(vals),50):
        db.exec("INSERT INTO nab_versions VALUES "+",".join(vals[i:i+50]))
    db.wait("nab_versions")
    return starts

def sql(layout,seg,atoms,lo,hi):
    ins=",".join(map(str,atoms))
    resolver=f"""
      SELECT count() slices FROM (
       SELECT v.fragment_id,v.valid_from
       FROM nab_versions v
       JOIN nab_fragment_atoms f ON v.layout=f.layout AND v.fragment_id=f.fragment_id
       WHERE v.segment={q(seg)} AND v.layout={q(layout)}
         AND f.atom_id IN ({ins})
         AND v.valid_from < {q(str(hi))}
         AND v.valid_to > {q(str(lo))}
       GROUP BY v.fragment_id,v.valid_from
      )
    """
    full=f"""
      SELECT vr.slices,sg.signal_sum
      FROM ({resolver}) vr
      CROSS JOIN (
        SELECT sum(value) signal_sum FROM nab_signal
        WHERE segment={q(seg)} AND ts>={q(str(lo))} AND ts<{q(str(hi))}
      ) sg
    """
    return resolver,full

def main():
    db=QDB()
    starts=setup(db)
    # Query-specific warm-up.
    for r in Q.itertuples():
        atoms=[int(x) for x in str(r.atoms).split(",")]
        lo=starts[r.segment]+pd.Timedelta(minutes=5*int(r.lo))
        hi=starts[r.segment]+pd.Timedelta(minutes=5*int(r.hi))
        for layout in ["static","temporal"]:
            db.exec(sql(layout,r.segment,atoms,lo,hi)[1])
    raw=[]; mism=0; maxdiff=0.0
    for rep in range(REPS):
        for r in Q.itertuples():
            atoms=[int(x) for x in str(r.atoms).split(",")]
            lo=starts[r.segment]+pd.Timedelta(minutes=5*int(r.lo))
            hi=starts[r.segment]+pd.Timedelta(minutes=5*int(r.hi))
            order=["static","temporal"] if (rep+int(r.qid))%2==0 else ["temporal","static"]
            got={}
            for layout in order:
                o,ems,cms,clms=db.exec(sql(layout,r.segment,atoms,lo,hi)[1])
                vals=o["dataset"][0]
                slices=int(vals[0]); sig=float(vals[1])
                expected=int(r.static_hes if layout=="static" else r.temporal_hes)
                if slices!=expected: mism+=1
                got[layout]=sig
                raw.append({"segment":r.segment,"qid":r.qid,"rep":rep,"layout":layout,
                            "server_ms":ems,"compile_ms":cms,"client_ms":clms,
                            "slices":slices,"signal_sum":sig})
            maxdiff=max(maxdiff,abs(got["static"]-got["temporal"]))
            if abs(got["static"]-got["temporal"])>1e-9: mism+=1
    raw=pd.DataFrame(raw)
    raw.to_csv(DATA/"RQ11_questdb_fixed_layout_raw.csv",index=False)
    ql=(raw.groupby(["segment","qid","layout"],as_index=False)
        .agg(server_ms=("server_ms","median"),client_ms=("client_ms","median"),
             slices=("slices","first"),signal_sum=("signal_sum","first")))
    piv=ql.pivot(index=["segment","qid"],columns="layout",values="server_ms").reset_index()
    piv["gain_pct"]=100*(piv["static"]-piv["temporal"])/piv["static"].replace(0,np.nan)
    piv.to_csv(DATA/"RQ11_questdb_fixed_layout_querylevel.csv",index=False)
    summ=[]
    rng=np.random.default_rng(SEED)
    for seg,g in piv.groupby("segment",sort=False):
        vals=g.gain_pct.replace([np.inf,-np.inf],np.nan).dropna().to_numpy()
        bs=[]
        if len(vals):
            for _ in range(BOOT):
                z=vals[rng.integers(0,len(vals),len(vals))]
                bs.append(float(np.median(z)))
        summ.append({"segment":seg,"queries":len(g),
                     "median_server_gain_pct":float(np.nanmedian(vals)) if len(vals) else None,
                     "ci_lo":float(np.percentile(bs,2.5)) if bs else None,
                     "ci_hi":float(np.percentile(bs,97.5)) if bs else None})
    pd.DataFrame(summ).to_csv(DATA/"RQ11_questdb_fixed_layout_timing_summary.csv",index=False)
    val={"query_pairs":len(Q)*REPS,"slice_checks":len(Q)*REPS*2,
         "mismatches":mism,"max_signal_abs_diff":maxdiff,"passed":mism==0}
    (DATA/"RQ11_questdb_fixed_layout_validation.json").write_text(json.dumps(val,indent=2))
    print(pd.DataFrame(summ).to_string(index=False))
    print(val)
if __name__=="__main__":
    main()
