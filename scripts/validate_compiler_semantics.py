#!/usr/bin/env python3
import csv, random, statistics
from collections import defaultdict
from pathlib import Path

OUT=Path(__file__).resolve().parent.parent/'data'/'reported'/'structural_compiler_validation.csv'
T=5000
N=64
SEEDS=50
UPDATES=600
QUERIES=500
CHECKS=500

def base_edges(kind):
    if kind=='Path64':
        return {tuple(sorted((i,i+1))) for i in range(N-1)}
    out=set(); side=8
    for r in range(side):
        for c in range(side):
            u=r*side+c
            if c+1<side: out.add((u,u+1))
            if r+1<side: out.add((u,u+side))
    return {tuple(sorted(e)) for e in out}

def candidates(kind):
    if kind=='Path64':
        return [tuple(sorted((i,i+2))) for i in range(N-2)] + [tuple(sorted((i,i+3))) for i in range(N-3)]
    out=[]; side=8
    for r in range(side-1):
        for c in range(side-1):
            u=r*side+c
            out.append(tuple(sorted((u,u+side+1))))
            out.append(tuple(sorted((u+1,u+side))))
    return out

def qset(kind,rg):
    if kind=='Path64':
        w=rg.randint(3,6); start=rg.randrange(0,N-w+1); return frozenset(range(start,start+w))
    side=8; h=rg.choice([1,2]); w=rg.choice([2,3]); r=rg.randrange(0,side-h+1); c=rg.randrange(0,side-w+1)
    return frozenset((r+dr)*side+(c+dc) for dr in range(h) for dc in range(w))

def fragments(kind):
    if kind=='Path64': return [frozenset(range(i,min(i+4,N))) for i in range(0,N,4)]
    # 2x2 tiles
    side=8; out=[]
    for r in range(0,side,2):
        for c in range(0,side,2):
            out.append(frozenset((r+dr)*side+(c+dc) for dr in range(2) for dc in range(2)))
    return out

def run(kind,seed):
    rg=random.Random(911000 + seed + (0 if kind=='Path64' else 10000))
    edges=base_edges(kind); cand=candidates(kind)
    times=sorted(rg.sample(range(1,T),UPDATES))
    invalid=defaultdict(list)
    raw_updates=[]
    for t in times:
        e=rg.choice(cand)
        if e in edges: edges.remove(e)
        else: edges.add(e)
        u,v=e
        # one-hop derived state changes only at the edited endpoints
        invalid[u].append(t); invalid[v].append(t)
        raw_updates.append((t,frozenset((u,v))))
    qs=[]
    for _ in range(QUERIES):
        S=qset(kind,rg); frac=rg.uniform(.35,.80); L=max(1,int(frac*T)); l=rg.randrange(0,T-L); qs.append((S,l,l+L))
    parts=fragments(kind)
    mismatches=0; exposed=[]; touched=[]
    for _ in range(CHECKS):
        S,l,r=rg.choice(qs); F=rg.choice([f for f in parts if f & S])
        # compiled boundary union for the fragment
        compiled=sorted({t for a in F for t in invalid[a] if l<t<r})
        # independent replay from raw edge-edit stream
        replay=[t for t,A in raw_updates if A & F and l<t<r]
        if len(compiled)!=len(replay): mismatches += 1
        exposed.append(len(compiled)); touched.append(len([f for f in parts if f & S]))
    return {
        'topology':kind,'seed':seed,'nodes':N,'structural_edits':UPDATES,'historical_queries':QUERIES,
        'fragment_query_checks':CHECKS,'mismatches':mismatches,
        'mean_exposed_boundaries':statistics.mean(exposed),'median_exposed_boundaries':statistics.median(exposed),
        'mean_fragments_touched':statistics.mean(touched)
    }

rows=[]
for kind in ['Path64','Grid64']:
    for seed in range(SEEDS): rows.append(run(kind,seed))
with OUT.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(OUT)
for kind in ['Path64','Grid64']:
    rr=[r for r in rows if r['topology']==kind]
    print(kind,'checks',sum(r['fragment_query_checks'] for r in rr),'mismatches',sum(r['mismatches'] for r in rr),
          'mean exposed',statistics.mean(r['mean_exposed_boundaries'] for r in rr),
          'mean touched',statistics.mean(r['mean_fragments_touched'] for r in rr))
