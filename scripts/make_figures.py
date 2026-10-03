#!/usr/bin/env python3
import os, math, time, random, bisect
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.colors import LinearSegmentedColormap

BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT=os.path.join(BASE,'figures')
os.makedirs(OUT,exist_ok=True)
DATA=os.path.join(BASE,'data','reported')

INK='#202020'; MID='#666666'; RULE='#b9b9b9'; PALE='#f1f1f1'; PALE2='#f7f7f7'
NAVY='#3f5366'; OCHRE='#8a6a3e'; FOREST='#5d725f'; WINE='#7c4e55'
BLUE='#1f77b4'; ORANGE='#ff7f0e'; GREEN='#2ca02c'; RED='#d62728'; PURPLE='#9467bd'; DARK='#25313c'; LIGHT='#f7f8fa'; PB='#eaf2f8'; PO='#fbe8d5'; PG='#e6f5e6'
ACADEMIC_MAP=LinearSegmentedColormap.from_list('academic_div',[('#dce7f0'),('#f7f7f5'),('#c98a62')])
plt.rcParams.update({'font.family':'serif','font.serif':['Linux Libertine O'],'mathtext.fontset':'stix','font.size':7.40,'pdf.fonttype':42,'ps.fonttype':42,'axes.linewidth':0.62,'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False})

def save(fig,name,pad=.004):
    for ext in ['pdf','svg']:
        fig.savefig(os.path.join(OUT,name+'.'+ext),bbox_inches='tight',pad_inches=pad)
    fig.savefig(os.path.join(OUT,name+'.png'),dpi=360,bbox_inches='tight',pad_inches=pad)
    plt.close(fig)

def clean(ax):
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)

# FIGURE 1: event-time boundary exposure with provenance projection.
fig,ax=plt.subplots(figsize=(3.34,1.98))
ax.set_xlim(0,1); ax.set_ylim(0,1); clean(ax)
x0,x1=.30,.96
A=[.37,.57,.77]; B=[.44,.65,.85]; ql,qr=.32,.92
# A light query-window band links source invalidations to the two physical layouts.
ax.add_patch(Rectangle((ql,.47),qr-ql,.43,facecolor='#fafbfc',edgecolor='none',zorder=-3))
ax.text((ql+qr)/2,.935,'query-visible interval',fontsize=4.45,ha='center',va='center',color=MID)
for label,y,color,cuts in [('Updates of $A$',.84,NAVY,A),('Updates of $B$',.70,MID,B)]:
    ax.text(.02,y,label,fontsize=5.75,ha='left',va='center')
    ax.plot([x0,x1],[y,y],lw=.42,color='#747474')
    for x in cuts:
        ax.plot([x,x],[y-.032,y+.032],lw=.92,color=color)
# Historical query interval.
ax.text(.02,.55,'Historical query $q$',fontsize=5.72,ha='left',va='center')
ax.plot([ql,qr],[.525,.525],lw=4.1,color='#e9ecef',solid_capstyle='butt')
ax.plot([ql,qr],[.525,.525],lw=.62,color=NAVY)
for x,t in [(ql,'$l_q$'),(qr,'$r_q$')]:
    ax.plot([x,x],[.496,.554],lw=.62,color=NAVY)
    ax.text(x,.484,t,fontsize=5.0,ha='center',va='top',color=MID)
for x in A+B:
    if ql < x < qr:
        src_y=.84 if x in A else .70
        ax.plot([x,x],[src_y-.035,.435],lw=.28,color=RULE,ls=':')
bar_l,bar_r=.37,.95
def bx(t): return bar_l + (t-ql)/(qr-ql)*(bar_r-bar_l)
ax.text(.02,.34,'Static co-versioning',fontsize=5.62,ha='left',va='center')
ax.plot([bar_l,bar_r],[.34,.34],lw=2.6,color='#efefef',solid_capstyle='butt')
ax.plot([bar_l,bar_r],[.34,.34],lw=.50,color='#737373')
for x in A:
    if ql<x<qr: ax.plot([bx(x),bx(x)],[.308,.372],lw=.78,color=NAVY)
for x in B:
    if ql<x<qr: ax.plot([bx(x),bx(x)],[.308,.372],lw=.78,color=MID)
ax.text(.95,.382,'6 boundaries / 7 slices',fontsize=4.95,ha='right',va='bottom',color=MID)
ax.text(.02,.17,'Temporal co-versioning',fontsize=5.62,ha='left',va='center')
ax.plot([bar_l,bar_r],[.17,.17],lw=2.8,color='#e7edf2',solid_capstyle='butt')
ax.plot([bar_l,bar_r],[.17,.17],lw=.55,color=NAVY)
for x in A:
    if ql<x<qr: ax.plot([bx(x),bx(x)],[.138,.202],lw=.82,color=NAVY)
ax.text(.95,.212,'3 boundaries / 4 slices',fontsize=4.95,ha='right',va='bottom',color=NAVY)
# Compact provenance key.
ax.plot([.35,.385],[.065,.065],lw=1.0,color=NAVY); ax.text(.390,.065,'A-origin',fontsize=4.62,ha='left',va='center',color=MID)
ax.plot([.54,.575],[.065,.065],lw=1.0,color=MID); ax.text(.580,.065,'imported B',fontsize=4.62,ha='left',va='center',color=MID)
ax.text(.95,.065,r'exposure $B_F\cap(l_q,r_q)$',fontsize=4.72,ha='right',va='center',color=MID)
save(fig,'fig1_problem')

# FIGURE 2: Time-Shuffle counterexample with explicit changed-vs-fixed structure.
fig,ax=plt.subplots(figsize=(7.08,1.62))
ax.set_xlim(0,1); ax.set_ylim(0,1); clean(ax)
ax.text(.025,.91,'Fixed workload information',fontsize=6.15,fontweight='bold',ha='left',va='center')
ax.text(.355,.91,'Only timestamp ownership changes',fontsize=6.15,fontweight='bold',ha='left',va='center')
ax.text(.755,.91,'Exact HES decision',fontsize=6.15,fontweight='bold',ha='left',va='center')
facts=[('Same query sets and weights',.72),('Same affected sets and update counts',.58),(r'Same timestamp multiset $\{\tau_1,\tau_2,\tau_3\}$',.44)]
for t,y in facts:
    ax.plot([.03,.043],[y,y],lw=.82,color=MID)
    ax.text(.051,y,t,fontsize=5.15,ha='left',va='center',color=INK)
ax.text(.035,.26,'Queries:',fontsize=5.1,fontweight='bold',ha='left',va='center')
ax.text(.087,.30,'$q_{AB}$',fontsize=4.95,ha='right',va='center')
ax.plot([.10,.275],[.30,.30],lw=2.0,color='#eceff1',solid_capstyle='butt'); ax.plot([.10,.275],[.30,.30],lw=.45,color=NAVY)
ax.text(.087,.20,'$q_A$',fontsize=4.95,ha='right',va='center')
ax.plot([.10,.18],[.20,.20],lw=2.0,color='#eceff1',solid_capstyle='butt'); ax.plot([.10,.18],[.20,.20],lw=.45,color=OCHRE)
cols=[.50,.59,.68]
for x,k in zip(cols,[1,2,3]): ax.text(x,.76,rf'$\tau_{{{k}}}$',fontsize=5.25,ha='center',va='center',color=MID)
for y,label in [(.59,'Assignment X'),(.31,'Assignment Y')]:
    ax.text(.355,y,label,fontsize=5.5,fontweight='bold',ha='left',va='center')
    ax.text(.455,y+.047,'A',fontsize=5.0,fontstyle='italic',ha='right',va='center')
    ax.text(.455,y-.047,'B',fontsize=5.0,fontstyle='italic',ha='right',va='center')
ax.plot([.465,.70],[.45,.45],lw=.30,color=RULE)
marks=[(cols[0],.637,OCHRE),(cols[2],.637,INK),(cols[1],.543,INK),(cols[2],.357,INK),(cols[0],.263,OCHRE),(cols[1],.263,INK)]
for x,y,col in marks: ax.plot(x,y,'o',ms=4.0,mfc='white',mec=col,mew=.92)
# The only changed item is made explicit instead of leaving the center panel visually empty.
ax.annotate('',xy=(cols[0],.292),xytext=(cols[0],.608),arrowprops=dict(arrowstyle='->',lw=.55,color=OCHRE,ls='--'))
ax.text(cols[0]+.014,.485,r'move $\tau_1$',fontsize=4.45,ha='left',va='center',color=OCHRE, bbox=dict(facecolor='white',edgecolor='none',pad=.08,alpha=.92))
ax.text(.765,.59,'X:',fontsize=5.35,fontweight='bold',ha='left',va='center')
ax.text(.795,.59,'merge 7 < split 8',fontsize=5.25,ha='left',va='center')
ax.text(.955,.59,'merge',fontsize=5.55,fontweight='bold',ha='right',va='center',color=OCHRE)
ax.text(.765,.31,'Y:',fontsize=5.35,fontweight='bold',ha='left',va='center')
ax.text(.795,.31,'split 6 < merge 7',fontsize=5.25,ha='left',va='center')
ax.text(.955,.31,'split',fontsize=5.55,fontweight='bold',ha='right',va='center',color=OCHRE)
ax.plot([.315,.315],[.10,.82],lw=.32,color=RULE)
ax.plot([.725,.725],[.10,.82],lw=.32,color=RULE)
ax.text(.50,.055,'Static signature fixed; one ownership change reverses the optimum.',fontsize=5.12,ha='center',va='center',color=MID)
save(fig,'fig2_roadmap_generated_reference')

# FIGURE 3: exact merge decomposition with boundary-set semantics.
fig,ax=plt.subplots(figsize=(3.34,2.46))
ax.set_xlim(0,1); ax.set_ylim(0,1); clean(ax)
xL,xR=.20,.94
# Histories and set labels.
for y,label in [(.88,'Fragment $F$'),(.74,'Fragment $G$'),(.56,r'Merged $F\cup G$')]:
    ax.text(.02,y,label,fontsize=5.62,ha='left',va='center')
    ax.plot([xL,xR],[y,y],lw=.48,color=INK)
for x in [.40,.61]:
    for y in [.88,.74,.56]: ax.plot([x,x],[y-.031,y+.031],lw=.92,color=WINE)
ax.plot([.82,.82],[.708,.772],lw=.92,color=FOREST)
ax.plot([.82,.82],[.528,.592],lw=.92,color=FOREST)
ax.text(.505,.952,r'$C_B=B_F\cap B_G$',fontsize=4.95,color=WINE,ha='center',va='center')
ax.text(.82,.665,r'$D_G=B_G\setminus B_F$',fontsize=4.75,color=FOREST,ha='center',va='center')
# Local score decomposition.
ax.text(.02,.43,'Exact local merge delta',fontsize=5.95,fontweight='bold',ha='left',va='center')
y=.255
ax.plot([.12,.90],[y,y],lw=.52,color=INK)
ax.plot([.50,.50],[y-.023,y+.023],lw=.52,color=INK)
ax.text(.50,.298,'0',fontsize=4.8,ha='center',va='bottom',color=MID)
# Three terms: savings, imported-boundary penalty, maintenance.
ax.annotate('',xy=(.22,y),xytext=(.50,y),arrowprops=dict(arrowstyle='-|>',lw=.95,color=WINE))
ax.text(.25,.193,r'shared-boundary saving $-3\sigma$',fontsize=4.82,ha='center',va='center',color=WINE)
ax.annotate('',xy=(.64,y),xytext=(.50,y),arrowprops=dict(arrowstyle='-|>',lw=.86,color=FOREST))
ax.text(.65,.193,r'imported-boundary penalty $+\sigma$',fontsize=4.65,ha='center',va='center',color=FOREST)
ax.annotate('',xy=(.82,y),xytext=(.64,y),arrowprops=dict(arrowstyle='-|>',lw=.86,color=MID))
ax.text(.81,.125,r'maintenance $+\lambda m(F)$',fontsize=4.7,ha='center',va='center',color=MID)
ax.text(.02,.058,r'Query subtotal $=-2\sigma$',fontsize=5.05,ha='left',va='center')
ax.text(.97,.058,r'Merge iff $\Delta C=-2\sigma+\lambda m(F)<0$',fontsize=5.0,fontweight='bold',ha='right',va='center')
save(fig,'fig3_cost_optimizer')

# EXPERIMENT FIGURES ------------------------------------------------------
# Fig4 calibration, two panels
mat=pd.read_csv(os.path.join(DATA,'materialization_microbenchmark.csv'))
med=mat.groupby('bytes',as_index=False)['ns_per_version'].median()
probe=pd.read_csv(os.path.join(DATA,'RQ5_final_probe_calibration_rep15.csv'))
fig,axs=plt.subplots(1,2,figsize=(7.08,1.40),gridspec_kw={'wspace':.30})
ax=axs[0]
ax.plot(med['bytes']/1024,med['ns_per_version']/1000,marker='o',ms=3.2,lw=1.0,color=GREEN)
ax.set_xscale('log',base=2); ax.set_yscale('log'); ax.set_xlabel('Derived-state payload (KiB)'); ax.set_ylabel('Materialization time (µs)'); ax.set_title('(a) Version-materialization calibration',fontsize=7.6,fontweight='bold')
ax.grid(alpha=.18,lw=.45)
ax=axs[1]
groups=[g['heldout_R2'].values for _,g in probe.groupby('workload')]
vp=ax.violinplot(groups,showmeans=False,showmedians=False,showextrema=False)
for b in vp['bodies']: b.set_facecolor(PG); b.set_edgecolor(GREEN); b.set_alpha(1)
ax.boxplot(groups,widths=.18,showfliers=False,medianprops=dict(color=DARK,lw=1),boxprops=dict(color=DARK),whiskerprops=dict(color=DARK),capprops=dict(color=DARK))
ax.set_xticks(range(1,len(groups)+1)); ax.set_xticklabels([f'W{i+1}' for i in range(len(groups))]); ax.set_ylim(.45,1.02); ax.set_ylabel('Held-out $R^2$'); ax.set_title('(b) HRC probe-calibration stability',fontsize=7.6,fontweight='bold'); ax.grid(axis='y',alpha=.18,lw=.45)
save(fig,'fig4_calibration')

# Fig5 RQ1 paired normalized costs
rq1=pd.read_csv(os.path.join(DATA,'RQ1_multistart_repeated.csv'))
aff=rq1['affinity_cost_ms'].to_numpy(); static=rq1['statichyper_cost_ms'].to_numpy(); temporal=rq1['temporal_cost_ms'].to_numpy()
vals=[aff/static, static/static, temporal/static]
fig,ax=plt.subplots(figsize=(3.25,1.58))
# The first two controls coincide in all released RQ1 seeds; draw them as narrow distributions.
vp=ax.violinplot(vals,showmeans=False,showmedians=False,showextrema=False)
for body,col,fill in zip(vp['bodies'],[GREEN,GREEN,ORANGE],[PG,PG,PO]):
    body.set_facecolor(fill); body.set_edgecolor(col); body.set_alpha(1)
ax.boxplot(vals,widths=.18,showfliers=False,medianprops=dict(color=DARK,lw=1),boxprops=dict(color=DARK),whiskerprops=dict(color=DARK),capprops=dict(color=DARK))
ax.set_xticks([1,2,3]); ax.set_xticklabels(['PairwiseAffinity','StaticHyper','Temporal']); ax.tick_params(axis='x',labelsize=6.2)
ax.set_ylabel('Normalized modeled cost'); ax.set_ylim(.965,1.012); ax.grid(axis='y',alpha=.18,lw=.45); ax.set_title('RQ1: calibrated default regime',fontsize=7.6,fontweight='bold')
ax.text(1.5,1.0055,'Identical in 50/50 seeds',ha='center',va='center',fontsize=5.4,color=DARK)
save(fig,'fig5_rq1_violin')

# Fig6 TimeShuffle controls
random_df=pd.read_csv(os.path.join(DATA,'RQ2_multirate_timeshuffle.csv'))
match=pd.read_csv(os.path.join(DATA,'RQ2_matched_timeshuffle_variable.csv'))
fig,axs=plt.subplots(1,2,figsize=(7.08,1.15),gridspec_kw={'wspace':.26})
ax=axs[0]
yvals=[random_df['opt_changed'].mean()*100,match['opt_changed'].mean()*100]
ax.vlines([0,1],[0,0],yvals,colors=[MID,GREEN],lw=1.0)
ax.scatter([0,1],yvals,s=18,facecolors=['white',GREEN],edgecolors=[MID,GREEN],linewidths=.8,zorder=3)
for x,v,n in zip([0,1],yvals,[len(random_df),len(match)]): ax.text(x,v+4,f'{int(round(v*n/100))}/{n} ({v:.0f}%)',ha='center',va='bottom',fontsize=5.2,color=DARK)
ax.set_xticks([0,1]); ax.set_xticklabels(['Random\nshuffle','Matched\nexposure']); ax.set_ylabel('Workloads with changed optimum (%)'); ax.set_ylim(0,108); ax.set_title('(a) Design sensitivity',fontsize=7.6,fontweight='bold'); ax.grid(axis='y',alpha=.18,lw=.45)
ax=axs[1]
vals=[match['B_on_A_penalty_pct'].values,match['A_on_B_penalty_pct'].values]
vp=ax.violinplot(vals,showmeans=False,showmedians=False,showextrema=False)
for body,col,fill in zip(vp['bodies'],[GREEN,ORANGE],[PG,PO]): body.set_facecolor(fill); body.set_edgecolor(col); body.set_alpha(1)
ax.boxplot(vals,widths=.18,showfliers=False,medianprops=dict(color=DARK,lw=1),boxprops=dict(color=DARK),whiskerprops=dict(color=DARK),capprops=dict(color=DARK))
ax.set_xticks([1,2]); ax.set_xticklabels(['B design\non A','A design\non B']); ax.set_ylabel('Cross-workload penalty (%)'); ax.set_title('(b) Matched Time-Shuffle penalty',fontsize=7.6,fontweight='bold'); ax.grid(axis='y',alpha=.18,lw=.45)
save(fig,'fig6_timeshuffle_controls')

# Fig7 optimizer oracle + stratified exactness
rq3=pd.read_csv(os.path.join(DATA,'RQ3_multistart_full_grid.csv'))
g1=(rq3['singleton_ratio']-1)*100; g2=(rq3['multistart_ratio']-1)*100
fig,axs=plt.subplots(1,3,figsize=(7.08,1.15),gridspec_kw={'width_ratios':[1.0,1.0,.86],'wspace':.34})
ax=axs[0]
vals=[g1.values,g2.values]; vp=ax.violinplot(vals,showmeans=False,showmedians=False,showextrema=False)
for body,col,fill in zip(vp['bodies'],[BLUE,ORANGE],[PB,PO]): body.set_facecolor(fill); body.set_edgecolor(col); body.set_alpha(1)
ax.boxplot(vals,widths=.17,showfliers=False,medianprops=dict(color=DARK,lw=1),boxprops=dict(color=DARK),whiskerprops=dict(color=DARK),capprops=dict(color=DARK))
ax.set_xticks([1,2]); ax.set_xticklabels(['Singleton','Multi-start']); ax.set_ylabel('Optimality gap (%)'); ax.set_title('(a) Gap distribution',fontsize=7.3,fontweight='bold'); ax.grid(axis='y',alpha=.18,lw=.45)
ax=axs[1]
for g,label,col in [(g1,'Singleton',BLUE),(g2,'Multi-start',ORANGE)]:
    x=np.sort(g); y=np.arange(1,len(x)+1)/len(x); ax.plot(x,y,lw=1.0,label=label,color=col)
ax.set_xlabel('Optimality gap (%)'); ax.set_ylabel('Empirical CDF'); ax.set_xlim(-.02,max(1.5,np.percentile(g1,99.5))); ax.set_title('(b) Exact-oracle CDF',fontsize=7.3,fontweight='bold'); ax.grid(alpha=.18,lw=.45); ax.legend(frameon=False,fontsize=6.2)
ax=axs[2]
by=rq3.groupby('state_bytes')[['singleton_exact','multistart_exact']].mean().mul(100)
xs=np.arange(len(by)); w=.34
ax.bar(xs-w/2,by['singleton_exact'].values,width=w,color=ORANGE,edgecolor='white',linewidth=.30,label='Singleton')
ax.bar(xs+w/2,by['multistart_exact'].values,width=w,color=BLUE,edgecolor='white',linewidth=.30,label='Multi-start')
for x,v in zip(xs+w/2,by['multistart_exact'].values): ax.text(x,v+1.0,f'{v:.1f}',ha='center',va='bottom',fontsize=5.35,color=DARK)
ax.set_xticks(xs); ax.set_xticklabels(['256','512','1024','2048']); ax.set_ylim(80,103.5); ax.set_ylabel('Exact cases (%)'); ax.set_xlabel('State bytes'); ax.set_title('(c) Search ablation',fontsize=7.3,fontweight='bold'); ax.grid(axis='y',alpha=.18,lw=.45)
save(fig,'fig7_optimizer_oracle')

# Fig8 temporal phase map plus mean curve
phase=pd.read_csv(os.path.join(DATA,'RQ4B_multistart_stats.csv'))
piv=phase.pivot(index='psi',columns='window_fraction',values='mean_gain_pct')
fig,axs=plt.subplots(1,2,figsize=(7.08,1.10),gridspec_kw={'width_ratios':[1.5,1],'wspace':.24})
ax=axs[0]
im=ax.imshow(piv.values,origin='lower',aspect='auto',cmap='Blues',vmin=0,vmax=max(8,piv.values.max()))
ax.set_xticks(range(len(piv.columns))); ax.set_xticklabels([f'{v:.2f}' for v in piv.columns]); ax.set_yticks(range(len(piv.index))); ax.set_yticklabels([f'{v:.1f}' for v in piv.index]); ax.set_xlabel('Historical-window fraction $L/T$'); ax.set_ylabel(r'Temporal alignment $\psi$'); ax.set_title('(a) Static-to-temporal phase map',fontsize=7.6,fontweight='bold')
for i in range(piv.shape[0]):
    for j in range(piv.shape[1]): ax.text(j,i,f'{piv.values[i,j]:.1f}',ha='center',va='center',fontsize=6.15,color=DARK)
cbar=fig.colorbar(im,ax=ax,fraction=.046,pad=.025); cbar.set_label('Gain over StaticHyper (%)',fontsize=6.8); cbar.ax.tick_params(labelsize=6)
ax=axs[1]
mean=phase.groupby('window_fraction')['mean_gain_pct'].mean(); ax.plot(mean.index,mean.values,marker='o',ms=3,lw=1.1,color=ORANGE); ax.set_xlabel('Historical-window fraction $L/T$'); ax.set_ylabel('Mean gain (%)'); ax.set_title('(b) Window-length trend',fontsize=7.6,fontweight='bold'); ax.grid(alpha=.18,lw=.45); ax.set_ylim(-.2,8)
save(fig,'fig8_phase_temporal')

# Fig9 physical phase map -- compact horizontal layout with direct line labels.
phaseA=pd.read_csv(os.path.join(DATA,'RQ4A_multistart_stats.csv'))
piv=phaseA.pivot(index='query_update_ratio',columns='state_bytes',values='mean_gain_pct')
fig,axs=plt.subplots(1,2,figsize=(3.10,1.23),gridspec_kw={'width_ratios':[1.18,1.0],'wspace':.31})
ax=axs[0]
im=ax.imshow(piv.values,origin='lower',aspect='auto',cmap='Blues',vmin=min(0,piv.values.min()),vmax=max(6.5,piv.values.max()))
ax.set_xticks(range(len(piv.columns))); ax.set_xticklabels([str(int(v)) for v in piv.columns],rotation=28,fontsize=5.2)
ax.set_yticks(range(len(piv.index))); ax.set_yticklabels([str(int(v)) for v in piv.index],fontsize=5.3)
ax.set_xlabel('State bytes',labelpad=1); ax.set_ylabel('Queries/update',labelpad=1)
ax.set_title('(a) Physical regime map',fontsize=6.95,fontweight='bold',pad=2)
for i in range(piv.shape[0]):
    for j in range(piv.shape[1]): ax.text(j,i,f'{piv.values[i,j]:.1f}',ha='center',va='center',fontsize=5.00,color=DARK)
ax=axs[1]
label_specs={5:(-8,5,'bottom'),10:(7,-7,'top'),20:(6,3,'bottom'),40:(6,2,'bottom')}
for ratio,col in zip([5,10,20,40],[GREEN,BLUE,ORANGE,DARK]):
    d=phaseA[phaseA['query_update_ratio']==ratio].sort_values('state_bytes')
    ax.plot(d['state_bytes'],d['mean_gain_pct'],marker='o',ms=2.0,lw=.75,color=col)
    peak=d.loc[d['mean_gain_pct'].idxmax()]
    dx,dy,va=label_specs[ratio]
    ax.annotate(f'{ratio}:1',xy=(peak['state_bytes'],peak['mean_gain_pct']),xytext=(dx,dy),textcoords='offset points',
                fontsize=5.00,color=col,ha='center',va=va,
                bbox=dict(boxstyle='square,pad=.08',facecolor='white',edgecolor='none',alpha=.88))
ax.set_xscale('log',base=2); ax.set_xlabel('State bytes',labelpad=1); ax.set_ylabel('Gain (%)',labelpad=1)
ax.set_ylim(-.15,6.55)
ax.set_title('(b) Read-write slices',fontsize=6.95,fontweight='bold',pad=2); ax.grid(alpha=.14,lw=.35)
save(fig,'fig9_phase_physical')

# Fig10 HRC validation -- compact horizontal layout to reduce page-11 visual density.
pred=pd.read_csv(os.path.join(DATA,'RQ5_cpp_independent_predictions.csv'))
pr=pd.read_csv(os.path.join(DATA,'RQ5_final_probe_calibration_rep15.csv'))
fig,axs=plt.subplots(1,2,figsize=(3.10,1.13),gridspec_kw={'wspace':.34})
ax=axs[0]
for w,g in pred.groupby('workload'):
    ax.scatter(g['HRC_pred_ms'],g['runtime_ms'],s=4.3,alpha=.48,color=BLUE)
ax.set_xlabel('HRC score',labelpad=1); ax.set_ylabel('Resolver time (ms)',labelpad=1)
ax.set_title('(a) Candidate ordering',fontsize=6.7,fontweight='bold',pad=2); ax.grid(alpha=.14,lw=.35)
ax=axs[1]
workloads=sorted(pr['workload'].unique()); r2=[pr[pr.workload==w]['heldout_R2'].values for w in workloads]; rho=[pr[pr.workload==w]['heldout_Spearman'].values for w in workloads]
pos=np.arange(1,len(workloads)+1); off=.16
bp1=ax.boxplot(r2,positions=pos-off,widths=.25,showfliers=False,patch_artist=True,medianprops=dict(color=DARK,lw=.75),boxprops=dict(color=GREEN),whiskerprops=dict(color=GREEN),capprops=dict(color=GREEN))
bp2=ax.boxplot(rho,positions=pos+off,widths=.25,showfliers=False,patch_artist=True,medianprops=dict(color=DARK,lw=.75),boxprops=dict(color=GREEN),whiskerprops=dict(color=GREEN),capprops=dict(color=GREEN))
for b in bp1['boxes']: b.set_facecolor('white')
for b in bp2['boxes']: b.set_facecolor(PG)
ax.set_xticks(pos); ax.set_xticklabels([f'W{i+1}' for i in range(len(workloads))],fontsize=5.5); ax.set_ylim(.45,1.02); ax.set_ylabel('Held-out score',labelpad=1)
ax.set_title('(b) Ten-probe stability',fontsize=6.7,fontweight='bold',pad=2); ax.grid(axis='y',alpha=.14,lw=.35)
from matplotlib.patches import Patch
ax.legend(handles=[Patch(facecolor='white',edgecolor=GREEN,label='$R^2$'),Patch(facecolor=PG,edgecolor=GREEN,label='Spearman')],frameon=False,fontsize=4.9,loc='lower left',ncol=2,columnspacing=.6,handlelength=.8)
save(fig,'fig10_hrc_validation')

# Fig11 end-to-end summary plus measured optimizer scalability -- compact horizontal layout.
summary_path=os.path.join(DATA,'canonical_compact_10x_summary.csv')
e2e=pd.read_csv(summary_path)
scale_path=os.path.join(DATA,'scalability.csv')
scale=pd.read_csv(scale_path)
fig,axs=plt.subplots(1,2,figsize=(3.25,1.08),gridspec_kw={'wspace':.33})
ax=axs[0]
m=e2e['mean'].to_numpy(float); lo=e2e['ci95_low'].to_numpy(float); hi=e2e['ci95_high'].to_numpy(float)
colors=[GREEN,ORANGE,BLUE]
ax.bar(range(len(m)),m,color=colors,edgecolor='white',width=.58)
ax.errorbar(range(len(m)),m,yerr=[m-lo,hi-m],fmt='none',ecolor=DARK,capsize=2.0,lw=.68)
ax.axhline(0,color=DARK,lw=.48); ax.set_xticks(range(len(m))); ax.set_xticklabels(['Total','Resolve','Signal'])
ax.set_ylabel('Temporal gain (%)',labelpad=1); ax.set_title('(a) CPU decomposition',fontsize=6.7,fontweight='bold',pad=2); ax.grid(axis='y',alpha=.14,lw=.35)
ax=axs[1]
sc=scale.copy(); ax.plot(sc['n'],sc['median_ms'],marker='o',ms=2.5,lw=.9,color=RED)
ax.fill_between(sc['n'],sc['min_ms'],sc['max_ms'],alpha=.12,color=RED)
ax.set_xlabel('Atomic states $n$',labelpad=1); ax.set_ylabel('Time (ms)',labelpad=1)
ax.set_title('(b) Optimizer scaling',fontsize=6.7,fontweight='bold',pad=2); ax.grid(alpha=.14,lw=.35)
save(fig,'fig11_system_scaling')


# Fig12 transfer robustness: QuestDB root replication + CloudWatch specification sensitivity.
root=pd.read_csv(os.path.join(DATA,'RQ10_questdb_root_comparison.csv'))
bins=pd.read_csv(os.path.join(DATA,'RQ11_pooled_bin_sensitivity.csv'))
fig,axs=plt.subplots(1,2,figsize=(3.25,1.08),gridspec_kw={'wspace':.38})
ax=axs[0]
segments=['calibration','regular_holdout','maintenance_burst','window_drift']; labels=['Calib.','Holdout','Burst','Drift']; x=np.arange(4)
for off,rname,col,mark in [(-.11,'root1',BLUE,'o'),(.11,'root2',ORANGE,'s')]:
    d=root[root['root']==rname].set_index('segment').loc[segments]
    y=d['server_gain_pct_median'].to_numpy(float); lo=d['server_gain_ci_lo'].to_numpy(float); hi=d['server_gain_ci_hi'].to_numpy(float)
    ax.errorbar(x+off,y,yerr=[y-lo,hi-y],fmt=mark,ms=2.7,lw=.7,capsize=1.6,color=col,label=rname.replace('root','Root '))
ax.axhline(0,color=DARK,lw=.5); ax.set_xticks(x); ax.set_xticklabels(labels,fontsize=5.0); ax.set_ylabel('Server gain (%)',labelpad=1)
ax.set_title('(a) QuestDB two-root replication',fontsize=6.55,fontweight='bold',pad=2); ax.grid(axis='y',alpha=.14,lw=.35); ax.legend(frameon=False,fontsize=4.9,ncol=2,loc='upper right',columnspacing=.6,handletextpad=.3)
ax=axs[1]
for metric,col,mark in [('CPU',BLUE,'o'),('NetworkIn',ORANGE,'s')]:
    d=bins[bins['metric']==metric].sort_values('bins'); ax.plot(d['bins'],d['reduction_pct'],marker=mark,ms=2.8,lw=.85,color=col,label=metric)
ax.axhline(0,color=DARK,lw=.5); ax.set_xticks(sorted(bins['bins'].unique())); ax.set_xlabel('Histogram bins',labelpad=1); ax.set_ylabel('HES reduction (%)',labelpad=1)
ax.set_title('(b) CloudWatch sensitivity',fontsize=6.55,fontweight='bold',pad=2); ax.grid(alpha=.14,lw=.35); ax.legend(frameon=False,fontsize=5.2,loc='upper right')
save(fig,'fig12_transfer_robustness')

print('generated figures in',OUT)
