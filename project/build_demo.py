"""Build a standalone demonstration using generated data only. No CAS input is read."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[key]='1'
from pathlib import Path
import hashlib,itertools,json
import numpy as np
import pandas as pd
from engine import metrics
from models import ResearchModel,calibrate,select_candidate,COMPLEXITY
from pricing import quote_prices,scenario,joint_commercial_decision
from research import make_partitions,segment_diagnostics,population_drift,risk_budget

ROOT=Path(__file__).resolve().parent

def generated(n,rng,start):
    d=pd.DataFrame({'PolicyID':np.arange(start,start+n),'Power':rng.choice(['Low','Medium','High'],n),'CarAge':rng.integers(0,28,n),'DriverAge':rng.integers(18,86,n),'Brand':rng.choice(['Group A','Group B','Group C'],n),'Gas':rng.choice(['Petrol','Diesel','Electric'],n),'Region':rng.choice(['North','South','East','West'],n),'Density':rng.integers(10,6000,n)})
    f=.07*np.exp(.55*(d.DriverAge<26)+.2*(d.Region=='North')+.18*(d.Gas=='Diesel')+.14*(d.Power=='High')+.000045*d.Density)
    d['ClaimNb']=rng.poisson(f)
    d['ClaimAmount']=[rng.gamma(shape=2*k,scale=500) if k else 0. for k in d.ClaimNb]
    return d

def records(d):return json.loads(d.to_json(orient='records',double_precision=12))

def build():
    cfg=json.loads((ROOT/'config.json').read_text());cfg['seed']=31827
    cfg['cv'].update(folds=3,repeats=1);cfg['risk_budget']['simulations']=500
    cfg['commercial'].update(expected_capacity=200,minimum_expected_policies=100)
    rng=np.random.default_rng(cfg['seed'])
    dev=generated(2400,rng,1);level=generated(600,rng,5001);q=generated(600,rng,10001).drop(columns=['ClaimNb','ClaimAmount'])
    names=['Constant','Frequency_GLM_Pooled','Frequency_GAM_Pooled','GBM_BodyTail'];rows=[];oof={}
    parts=make_partitions(dev,cfg)
    for part in parts:
        fit,cal,val=[dev.iloc[part[k]] for k in ['fit','cal','val']]
        for name in names:
            m=ResearchModel(name,cfg).fit(fit);pc=m.predict(cal);pv=m.predict(val)
            for mode in ['none','full','credibility']:
                c=calibrate(cal.ClaimAmount.to_numpy(),pc,mode,.2);key=name+'::'+mode
                oof.setdefault(key,np.zeros(len(dev)))[part['val']]=pv*c['factor']
                rows.append({'candidate':key,'model':name,'calibration_mode':mode,'complexity':COMPLEXITY[name],'fold':part['fold'],**metrics(val.ClaimAmount,pv*c['factor'])})
    cv=pd.DataFrame(rows).groupby(['candidate','model','calibration_mode','complexity'],as_index=False).agg(mean_deviance=('tweedie_deviance_p1.5','mean'),sd_deviance=('tweedie_deviance_p1.5','std'),mean_AE=('actual_over_expected','mean'))
    cv['heuristic_se']=cv.sd_deviance/np.sqrt(3);dec=select_candidate(cv)
    name,mode=dec['technical_candidate'].split('::');model=ResearchModel(name,cfg).fit(dev)
    c=calibrate(level.ClaimAmount.to_numpy(),model.predict(level),mode,.2)
    pure=model.predict(q)*c['factor'];thresholds=np.quantile(model.predict(dev)*c['factor'],[.5,.85]).tolist()
    prices=quote_prices(pure,cfg['pricing'],thresholds)
    book=pd.concat([q.reset_index(drop=True),prices],axis=1)
    comp=model.components(q);book['frequency_uncalibrated']=comp.get('frequency');book['severity_uncalibrated']=comp.get('severity');book['calibration_factor']=c['factor'];book['manual_review']=False;book['review_reasons']='Generated demonstration record'
    market={**cfg['commercial'],'net_premium_ratio':1-cfg['pricing']['variable_expense_ratio']-cfg['pricing']['tax_ratio']}
    grid=[]
    for cm,tilt,el,selection,action in itertools.product(market['competitor_multipliers'],market['competitor_risk_tilts'],market['elasticities'],market['bound_loss_multipliers'],market['quote_adjustments']):
        grid.append(scenario(prices,prices.technical_premium,pure,market,cm,tilt,el,selection,action))
    robust=pd.DataFrame(grid).groupby('money_adjustment',as_index=False).agg(worst_contribution=('expected_contribution','min'),minimum_expected_policies=('expected_policies','min'),all_growth_targets_met=('growth_target_met','all'))
    robust,decision=joint_commercial_decision(robust,0)
    pdp=[]
    for feature in ['DriverAge','CarAge','Density']:
        for value in np.linspace(dev[feature].min(),dev[feature].max(),25):
            changed=q.copy();changed[feature]=value;pdp.append({'feature':feature,'value':float(value),'mean_predicted_loss':float(model.predict(changed).mean()*c['factor'])})
    y=dev.ClaimAmount.to_numpy();concentration=[{'sample':'Generated development','claims':int(dev.ClaimNb.sum()),'effective_loss_count':float(y.sum()**2/np.dot(y,y)),'top_5_loss_share':float(np.sort(y)[-5:].sum()/y.sum())}]
    input_hash=hashlib.sha256(dev.to_csv(index=False).encode()).hexdigest()
    payload={'config':cfg,'summary':{'version':'synthetic-demo-1','thresholds':thresholds,'decision':dec,'calibration':c,'evidence_boundary':'Generated data only. These fitted demonstration results are separate from the CAS study. They do not establish performance on real insurance data.','production_blockers':['Synthetic demonstration; no actual customer or competitor observations.','Expected scenario contribution is conditional on assumptions.']},'quotes':records(book),'cv':records(cv),'segments':records(segment_diagnostics(dev,oof[dec['technical_candidate']],cfg)),'capital':records(risk_budget(dev,prices,cfg)),'partial_dependence':pdp,'concentration':concentration,'drift':records(population_drift(dev,q)),'robust':records(robust),'commercial_decision':decision,'matched_budget':[],'stability':[],'governance':[{'area':'Data','control':'All demonstration records are generated from a seeded distribution. No source workbook or fitted CAS contract is read.','status':'IMPLEMENTED_SYNTHETIC_DATA'},{'area':'Interpretation','control':'Region and fuel select a cohort. Changing them does not edit individual records.','status':'IMPLEMENTED_COHORT_FILTERS'}],'manifest':{'input_sha256':input_hash,'config_sha256':hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),'fit_cache_fingerprint':hashlib.sha256(json.dumps({'seed':31827,'candidate':dec['technical_candidate']},sort_keys=True).encode()).hexdigest(),'seed':31827}}
    text=(ROOT/'dashboard_template.html').read_text(encoding='utf-8')
    # Replace demo-specific evidence text; do not reuse the real study's counts or claims.
    text=text.replace('CAS program dataset <span','Generated demonstration data <span').replace('v2.0.0</div>','synthetic demo</div>')
    text=text.replace('OFFLINE DECISION LAB','SYNTHETIC DATA DEMO').replace('RESEARCH ONLY','GENERATED DATA')
    text=text.replace('<div class="main">','<div class="main">')
    text=text.replace('<main class="main">','<main class="main"><div class="notice"><strong>Synthetic demonstration.</strong> All 600 quote risks and all model evidence shown here were generated for this demo. They are separate from the CAS study. Region and Fuel filter the quote book; they do not edit an individual policy.</div>')
    text=text.replace('Development-only evidence after prior exploration. The historical test has been quarantined. No fresh prospective or out-of-time validation exists.','Generated-data comparison: one repeat of three grouped folds with inner calibration. Results demonstrate the interface and methods, not real-portfolio predictive performance.')
    text=text.replace('>2 × 5<','>1 × 3<').replace('>27<','>12<').replace('9 structures × 3 calibration strategies','4 structures × 3 calibration strategies').replace('Repeated-OOF prediction averages','Synthetic out-of-fold predictions')
    text=text.replace('Fold score range for this risk:','Fold score range (not generated for this demo):')
    text=text.replace('<h3>Matched allowance budgets</h3>','<h3>Additional diagnostics</h3>').replace('Saved default full-book diagnostic. Budget matching removes total-money differences; it does not prove fairness or redefine the live tariff.','Matched-budget diagnostics are not computed in this lightweight demo. The complete study contains those diagnostics.')
    start=text.index('<div class="filelinks">');end=text.index('</div>',start)+6
    text=text[:start]+'<div class="filelinks"><a href="../README.md">Project overview</a><a href="../docs/DEMO.md">Demo guide and interpretation</a><a href="../docs/METHOD.md">Pricing method</a></div>'+text[end:]
    encoded=json.dumps(payload,allow_nan=False,separators=(',',':')).replace('<','\\u003c')
    text=text.replace('<script id="embedded-data" type="application/json">__EMBEDDED_DATA__</script>','<script id="embedded-data" type="application/json">'+encoded+'</script>')
    target=ROOT.parent/'demo';target.mkdir(exist_ok=True)
    (target/'DASHBOARD.html').write_text(text,encoding='utf-8')
    print(json.dumps({'quote_risks':len(q),'fold_score_rows':len(rows),'pipelines':len(cv),'candidate':dec['technical_candidate'],'output':str(target/'DASHBOARD.html')}))
if __name__=='__main__':build()
