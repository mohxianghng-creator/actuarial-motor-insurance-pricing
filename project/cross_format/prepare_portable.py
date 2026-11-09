"""Export a frozen selected-model contract without changing the Python V2 model."""
import hashlib,json,sys,shutil
from pathlib import Path
import joblib,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def prepare():
    r=ROOT/'r_version';x=ROOT/'excel_version';p=r/'portable_model';data=r/'data'
    for d in [r,x,p,data,r/'results']:d.mkdir(parents=True,exist_ok=True)
    e=joblib.load(ROOT/'results/pricing_engine.joblib');cfg=json.loads((ROOT/'config.json').read_text())
    model=e.model
    assert model.name=='Frequency_GLM_Pooled'
    sc=model.pre.named_transformers_['num'].named_steps['scale']
    im=model.pre.named_transformers_['num'].named_steps['impute']
    names=['CarAge','DriverAge','Density'];trans=['identity','identity','log1p']
    rows=[]
    for i,name in enumerate(names):rows.append({'feature':name,'encoding':trans[i],'level':'','coefficient':float(model.freq.coef_[i]),'center':float(sc.mean_[i]),'scale':float(sc.scale_[i]),'imputation_transformed':float(im.statistics_[i])})
    k=3;encoder=model.pre.named_transformers_['cat']
    for feature,levels in zip(['Power','Brand','Gas','Region'],encoder.categories_):
        for level in levels:
            rows.append({'feature':feature,'encoding':'onehot','level':str(level),'coefficient':float(model.freq.coef_[k]),'center':0.,'scale':1.,'imputation_transformed':0.});k+=1
    pd.DataFrame(rows).to_csv(p/'terms.csv',index=False)
    params={'intercept':float(model.freq.intercept_),'pooled_severity':model.pool_severity,'calibration_factor':e.calibration['factor'],'threshold_1':e.thresholds[0],'threshold_2':e.thresholds[1]}
    for key,v in cfg['pricing'].items():
        if isinstance(v,(int,float,bool)):params[key]=int(v) if isinstance(v,bool) else v
        elif isinstance(v,str):params[key]=v
    for i,v in enumerate(cfg['pricing']['tier_risk_margins'],1):params[f'tier_money_{i}']=v
    params.update({'input_sha256':json.loads((ROOT/'results/run_manifest.json').read_text())['input_sha256'],'source_candidate':'Frequency_GLM_Pooled::credibility','version':'2.0.0'})
    pd.DataFrame({'parameter':list(params),'value':[str(v) for v in params.values()]}).to_csv(p/'parameters.csv',index=False)
    refs=[]
    for feature,value in e.reference.items():
        if isinstance(value,dict):refs.append({'feature':feature,'kind':'numeric','level':'','minimum':value['min'],'maximum':value['max']})
        else:
            refs.extend({'feature':feature,'kind':'categorical','level':level,'minimum':'','maximum':''} for level in value)
    pd.DataFrame(refs).to_csv(p/'reference_ranges.csv',index=False)
    src=Path(cfg['data_path'])
    if hashlib.sha256(src.read_bytes()).hexdigest()!=params['input_sha256']:raise ValueError('Workbook differs from the frozen V2 input; retrain/version the Python model before exporting another training dataset.')
    book=pd.read_excel(src,sheet_name=['Dataset for Student','Pricing Data'])
    d=book['Dataset for Student'];q=book['Pricing Data'];manifest=pd.read_csv(ROOT/'provenance/frozen_split_manifest.csv')
    d=d.merge(manifest,on='PolicyID',validate='one_to_one');d=d[d.split!='test']
    d.to_csv(data/'training_and_calibration.csv',index=False);q.to_csv(data/'quotes.csv',index=False)
    shutil.copy2(ROOT/'results/development_fold_manifest.csv',data/'development_fold_manifest.csv')
    for name in ['quote_detail.csv','commercial_scenarios.csv','commercial_robust_comparison.csv','cv_model_summary.csv','risk_budget_scenarios.csv','segment_credibility.csv','development_oof_predictions.csv']:
        shutil.copy2(ROOT/'results'/name,p/('python_'+name))
    # Portable R configuration is code/data, not a JSON-library dependency.
    def r_value(v):
        if isinstance(v,bool):return 'TRUE' if v else 'FALSE'
        if isinstance(v,(int,float)):return repr(v)
        if isinstance(v,str):return json.dumps(v)
        if isinstance(v,list):return 'c('+','.join(r_value(x) for x in v)+')'
        if isinstance(v,dict):return 'list('+','.join(k+'='+r_value(x) for k,x in v.items())+')'
        raise TypeError(v)
    (r/'config.R').write_text('config <- '+r_value(cfg)+'\n',encoding='utf-8')
    files=[*p.glob('*.csv'),*data.glob('*.csv')]
    provenance={'python_v2_input_sha256':params['input_sha256'],'original_workbook_md5':hashlib.md5(src.read_bytes()).hexdigest(),
        'historical_test_rows_exported':0,'files_sha256':{str(f.relative_to(r)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        'scope':'Frozen selected-model scoring is exact. Native R retraining is separately labeled; native R GBM uses rpart Newton boosting rather than sklearn histogram boosting.'}
    (r/'PORTABILITY_MANIFEST.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    # Base R can check MD5 of every exported input without an extra package.
    pd.DataFrame([{'file':str(f.relative_to(r)).replace('\\','/'),'md5':hashlib.md5(f.read_bytes()).hexdigest()} for f in files]).to_csv(r/'input_checksums.csv',index=False)
    return cfg

if __name__=='__main__':prepare();print('Portable model and R inputs exported; historical test outcomes excluded.')
