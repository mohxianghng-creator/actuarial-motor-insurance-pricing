"""Material contracts: leakage, money, credibility, model support and saved evidence."""
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import pytest
from engine import FEATURES,prepare,validate_training
from models import ResearchModel,calibrate,select_candidate
from pricing import quote_prices,margin_amount,scenario,MotorPricingEngine,joint_commercial_decision
from research import make_partitions,partitions_digest

ROOT=Path(__file__).parent

@pytest.fixture
def cfg():return json.loads((ROOT/'config.json').read_text())


def sample():
    rng=np.random.default_rng(7701);n=1800
    d=pd.DataFrame({'PolicyID':np.arange(n),'Power':rng.choice(['d','f'],n),'CarAge':rng.integers(0,20,n),
        'DriverAge':rng.integers(18,90,n),'Brand':'Example','Gas':rng.choice(['Diesel','Regular'],n),
        'Region':rng.choice(['A','B'],n),'Density':rng.integers(2,2000,n)})
    d['ClaimNb']=rng.poisson(.15+.003*(90-d.DriverAge),n)
    d['ClaimAmount']=d.ClaimNb*rng.gamma(2,500,n)
    return d


def test_grouped_repeated_partitions(cfg):
    d=sample();d.loc[1,FEATURES]=d.loc[0,FEATURES]
    parts=make_partitions(d,cfg)
    groups=pd.util.hash_pandas_object(d[FEATURES],index=False).to_numpy()
    assert len(parts)==10
    for repeat in range(2):
        validation=[]
        for part in [p for p in parts if p['repeat']==repeat]:
            a,b,c=[part[x] for x in ['fit','cal','val']]
            assert len(set(a)|set(b)|set(c))==len(d)
            assert not set(groups[a])&set(groups[b])
            assert not (set(groups[a])|set(groups[b]))&set(groups[c])
            validation.extend(c.tolist())
        assert sorted(validation)==list(range(len(d)))
    again=make_partitions(d,cfg)
    for a,b in zip(parts,again):np.testing.assert_array_equal(a['val'],b['val'])
    assert partitions_digest(parts)==partitions_digest(again)
    changed=[{**p,'fit':p['fit'].copy()} for p in parts];changed[0]['fit'][0]+=1
    assert partitions_digest(parts)!=partitions_digest(changed)


def test_features_exclude_identifiers_and_targets():
    x=prepare(sample())
    assert not set(['PolicyID','ClaimNb','ClaimAmount'])&set(x.columns)


@pytest.mark.parametrize('mode',['none','full','credibility'])
def test_calibration_contract(mode):
    y=np.array([0,0,100,300.]);p=np.full(4,50.)
    c=calibrate(y,p,mode,.2)
    assert c['raw_ratio']==2.
    assert 0<c['reliability']<1
    if mode=='none':assert c['factor']==1
    elif mode=='full':assert c['factor']==2
    else:assert 1<c['factor']<2
    np.testing.assert_allclose(c['factor'],calibrate(y,p,mode,.2)['factor'])


def test_more_concentrated_calibration_gets_less_weight():
    diffuse=calibrate(np.full(100,100.),np.full(100,80.),'credibility',.2)
    concentrated=calibrate(np.r_[np.zeros(99),10000],np.full(100,80.),'credibility',.2)
    assert concentrated['reliability']<diffuse['reliability']
    assert concentrated['factor']<diffuse['factor']


@pytest.mark.parametrize('y,p,prior',[(np.array([0.,0.]),np.ones(2),.2),(np.ones(2),np.zeros(2),.2),(np.array([np.nan,1]),np.ones(2),.2),(np.ones(2),np.ones(2),np.inf)])
def test_invalid_calibration_rejected(y,p,prior):
    with pytest.raises(ValueError):calibrate(y,p,'credibility',prior)


def test_simplicity_rule_preserves_constant_comparator():
    a=pd.DataFrame([
        {'candidate':'Constant::none','model':'Constant','mean_deviance':50,'heuristic_se':1,'complexity':0},
        {'candidate':'Frequency_GLM_Pooled::none','model':'Frequency_GLM_Pooled','mean_deviance':50.4,'heuristic_se':1,'complexity':1},
        {'candidate':'GBM_BodyTail::none','model':'GBM_BodyTail','mean_deviance':50.1,'heuristic_se':1,'complexity':6}])
    result=select_candidate(a)
    assert result['technical_candidate']=='Frequency_GLM_Pooled::none'
    assert result['constant_benchmark_candidate']=='Constant::none'
    assert result['benchmark_mean_deviance']<result['selected_mean_deviance']


@pytest.mark.parametrize('name',['Frequency_GLM_Pooled','Frequency_GAM_Pooled','GLM_BodyTail','GAM_BodyTail','GBM_BodyTail','GBM_Hurdle','Tweedie_GAM'])
def test_model_positive_mean_and_component_identity(name,cfg):
    d=sample();m=ResearchModel(name,cfg).fit(d.iloc[:1400]);parts=m.components(d.iloc[1400:])
    assert np.isfinite(parts['pure_premium']).all() and (parts['pure_premium']>0).all()
    if 'frequency' in parts:np.testing.assert_allclose(parts['pure_premium'],parts['frequency']*parts['severity'])
    if name.startswith('Frequency_'):assert np.ptp(parts['severity'])==0


def test_tail_is_restored_and_fit_only(cfg):
    d=sample();m=ResearchModel('GLM_BodyTail',cfg).fit(d.iloc[:1400])
    fit=d.iloc[:1400];positive=fit.ClaimNb>0;sev=fit.loc[positive,'ClaimAmount']/fit.loc[positive,'ClaimNb']
    assert m.threshold==pytest.approx(np.quantile(sev,.99))
    assert m.pooled_body+m.pooled_excess==pytest.approx(m.pool_severity)
    future=d.iloc[1400:].copy();future.loc[future.index[0],'ClaimAmount']=1e12
    before=m.threshold
    np.testing.assert_allclose(m.predict(future),m.predict(d.iloc[1400:]))
    assert m.threshold==before


def test_pricing_identity_and_commercial_floor(cfg):
    p=cfg['pricing'].copy();p.update(margin_mode='flat_constant',flat_risk_margin=20,fixed_expense=10,variable_expense_ratio=.2,tax_ratio=0)
    a=quote_prices([100,200],p,[150,250])
    np.testing.assert_allclose(a.quote,[162.5,287.5]);np.testing.assert_allclose(a.expected_contribution,20)
    p['commercial_adjustment']=-1000;b=quote_prices([100,200],p,[150,250])
    np.testing.assert_allclose(b.quote,b.cost_floor);np.testing.assert_allclose(b.expected_contribution,0,atol=1e-10)
    p['commercial_adjustment']=12.3;c=quote_prices([100,200],p,[150,250])
    np.testing.assert_allclose(c.quote,c.expected_loss+c.fixed_expense+c.money_margin+.2*c.quote+.8*c.applied_adjustment)


def test_smooth_shoulders_are_continuous_monotone_and_plateaued(cfg):
    p=cfg['pricing'].copy();p['margin_mode']='smooth_tiered_constant';p['transition_width']=20
    x=np.linspace(0,200,10000);a=quote_prices(x,p,[50,100])
    assert (np.diff(a.quote)>=0).all()
    for t in [40,50,60,90,100,110]:
        near=quote_prices([t-1e-7,t,t+1e-7],p,[50,100])
        assert np.ptp(near.quote)<1e-5
    np.testing.assert_allclose(margin_amount([0,20,70,80,150],p,[50,100]),[10,10,20,20,35])


def test_strict_threshold_equality_and_batch_invariance(cfg):
    p=cfg['pricing'].copy();p['margin_mode']='tiered_constant'
    a=quote_prices([49.99,50,99.99,100],p,[50,100])
    np.testing.assert_allclose(a.money_margin,[10,20,20,35])
    assert quote_prices([50],p,[50,100]).quote.iloc[0]==a.quote.iloc[1]


@pytest.mark.parametrize('field,value',[('variable_expense_ratio',1),('tax_ratio',-1),('fixed_expense',-1),('trend_factor',0),('development_factor',np.nan)])
def test_bad_pricing_inputs_rejected(field,value,cfg):
    p=cfg['pricing'].copy();p[field]=value
    with pytest.raises(ValueError):quote_prices([100],p,[50,100])


def test_invalid_tiers_and_pure_rejected(cfg):
    for thresholds in [[100,50],[50,np.nan]]:
        with pytest.raises(ValueError):quote_prices([100],cfg['pricing'],thresholds)
    with pytest.raises(ValueError):quote_prices([-1],cfg['pricing'],[50,100])


def test_capacity_and_adverse_selection(cfg):
    p=quote_prices(np.full(10000,100.),cfg['pricing'],[50,150])
    c={**cfg['commercial'],'net_premium_ratio':.85}
    a=scenario(p,p.technical_premium,p.pure_premium,c)
    assert a['unconstrained_expected_policies']==pytest.approx(3000)
    assert a['expected_policies']==pytest.approx(2500)
    assert a['random_acceptance_ratio']==pytest.approx(5/6)
    b=scenario(p,p.technical_premium,p.pure_premium,c,bound_loss_multiplier=1.15)
    assert a['expected_policies']==b['expected_policies']
    assert a['expected_contribution']-b['expected_contribution']==pytest.approx(2500*100*.15)


def test_competitor_reference_does_not_follow_our_money(cfg):
    p=quote_prices(np.linspace(50,200,1000),cfg['pricing'],[80,150]);ref=p.technical_premium.copy()
    c={**cfg['commercial'],'net_premium_ratio':.85}
    lower=scenario(p,ref,p.pure_premium,c,adjustment=-10)
    higher=scenario(p,ref,p.pure_premium,c,adjustment=10)
    assert lower['unconstrained_expected_policies']>higher['unconstrained_expected_policies']


def test_joint_growth_and_profit_constraints_report_infeasibility():
    grid=pd.DataFrame({'money_adjustment':[-10,0,10],'worst_contribution':[-100,100,150],
        'all_growth_targets_met':[True,False,False]})
    table,decision=joint_commercial_decision(grid,0)
    assert not decision['feasible'] and decision['selected_adjustment'] is None
    assert decision['growth_only_best_adjustment']==-10
    assert not table.joint_constraints_met.any()
    grid.loc[1,'all_growth_targets_met']=True
    table,decision=joint_commercial_decision(grid,0)
    assert decision['feasible'] and decision['selected_adjustment']==0


def test_persisted_research_engine_parity_and_referrals(tmp_path,cfg):
    d=sample();m=ResearchModel('Frequency_GLM_Pooled',cfg).fit(d)
    reference={c:sorted(d[c].astype(str).unique()) if c in ['Power','Brand','Gas','Region'] else {'min':d[c].min(),'max':d[c].max()} for c in FEATURES}
    e=MotorPricingEngine(m,{'factor':1.1},cfg['pricing'],[50,100],reference)
    q=d.iloc[:2].copy();q.loc[q.index[0],'Region']='UNKNOWN';q.loc[q.index[1],'CarAge']=100
    a=e.quote(q);assert a.manual_review.all()
    file=tmp_path/'engine.joblib';joblib.dump(e,file);np.testing.assert_allclose(joblib.load(file).quote(q).quote,a.quote)


@pytest.mark.parametrize('family',['glm','gam','gbm'])
def test_generic_exposure_contract(family,cfg):
    from generic_motor import MotorSchema,GenericMotorEngine
    d=sample().rename(columns={'PolicyID':'id','ClaimNb':'n','ClaimAmount':'y'});d['exposure']=np.where(d.CarAge%2,.5,1.)
    s=MotorSchema(['DriverAge','CarAge'],['Region'],policy_id='id',claim_count='n',aggregate_loss='y')
    m=GenericMotorEngine(s,family=family).fit(d.iloc[:1400]);q=d.iloc[1400:].copy()
    before=m.predict_loss(q);q['exposure']*=2;np.testing.assert_allclose(m.predict_loss(q),2*before)
    m.calibrate(d.iloc[1400:]);assert m.predict_loss(d.iloc[1400:]).sum()==pytest.approx(d.iloc[1400:].y.sum())
    quotes=m.quote(q,cfg['pricing'],[50,100]);assert (quotes.quote>0).all()


def test_saved_evidence_and_exact_frequency_explanation():
    r=ROOT/'results'
    if not (r/'summary.json').exists():pytest.skip('Build actual workbook first')
    q=pd.read_csv(r/'quote_detail.csv');s=json.loads((r/'summary.json').read_text());manifest=json.loads((r/'run_manifest.json').read_text())
    assert manifest['historical_test_scored_in_v2'] is False
    assert len(q)==10000 and q.PolicyID.is_unique
    assert (q.quote>=q.cost_floor-1e-10).all()
    assert s['decision']['technical_candidate']==select_candidate(pd.read_csv(r/'cv_model_summary.csv'))['technical_candidate']
    assert len(pd.read_csv(r/'cv_fold_metrics.csv'))==270
    if 'reference_frequency' not in q:pytest.skip('Selected model does not provide GLM frequency factors')
    effects=q[[f'log_frequency_effect_{c}' for c in FEATURES]].sum(axis=1)
    np.testing.assert_allclose(q.reference_frequency*np.exp(effects),q.frequency_uncalibrated,rtol=1e-11)
    np.testing.assert_allclose(q.pure_premium,q.frequency_uncalibrated*q.severity_uncalibrated*q.calibration_factor,rtol=1e-11)


def test_matched_money_budgets_and_prior_sensitivity_when_built():
    r=ROOT/'results'
    if not (r/'matched_budget_margin_comparison.csv').exists():pytest.skip('Build V2 budget diagnostics first')
    matched=pd.read_csv(r/'matched_budget_margin_comparison.csv')
    np.testing.assert_allclose(matched.mean_money_margin,matched.common_target_money_per_policy,rtol=1e-12)
    np.testing.assert_allclose(matched.total_contribution,matched.total_contribution.iloc[0],rtol=1e-12)
    prior=pd.read_csv(r/'calibration_prior_sensitivity.csv')
    assert (np.diff(prior.reliability)>0).all()
    assert (prior.calibration_factor>0).all()
