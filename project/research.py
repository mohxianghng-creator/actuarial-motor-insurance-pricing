"""Development-only repeated grouped validation and auditable research build."""
from __future__ import annotations
import hashlib
import csv
import itertools
import importlib.metadata
import json
import platform
import sys
import warnings
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import mean_poisson_deviance,mean_gamma_deviance,brier_score_loss,roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold,GroupShuffleSplit
from engine import FEATURES,CATEGORICAL,load_data,metrics,dump_json,tail_analysis,zero_diagnostics,bootstrap_comparison
from models import ResearchModel,calibrate,select_candidate,COMPLEXITY
from pricing import MotorPricingEngine,quote_prices,scenario,joint_commercial_decision


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def full_environment(root):
    from packaging.requirements import Requirement
    requested=["numpy","pandas","scipy","scikit-learn","openpyxl","matplotlib","joblib","pytest"]
    resolved={}; pending=list(requested)
    while pending:
        name=pending.pop()
        if name.lower() in resolved:continue
        dist=importlib.metadata.distribution(name)
        resolved[name.lower()]=dist.version
        for raw in dist.requires or []:
            req=Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra":""}):pending.append(req.name)
    (root/"requirements.lock.txt").write_text("\n".join(f"{k}=={v}" for k,v in sorted(resolved.items()))+"\n",encoding="utf-8")
    return {"python":sys.version,"platform":platform.platform(),"packages":resolved,
        "numerical_threads":{k:__import__('os').environ.get(k) for k in ["OMP_NUM_THREADS","MKL_NUM_THREADS","OPENBLAS_NUM_THREADS"]}}


def make_partitions(dev,cfg):
    groups=pd.util.hash_pandas_object(dev[FEATURES],index=False).to_numpy()
    strata=(dev.ClaimNb>0).astype(int).to_numpy()
    parts=[]
    for repeat in range(cfg["cv"]["repeats"]):
        splitter=StratifiedGroupKFold(n_splits=cfg["cv"]["folds"],shuffle=True,random_state=cfg["seed"]+repeat)
        for fold,(outer,val) in enumerate(splitter.split(dev,strata,groups)):
            inner=GroupShuffleSplit(n_splits=1,test_size=cfg["cv"]["inner_calibration_fraction"],random_state=cfg["seed"]+100*repeat+fold)
            aa,bb=next(inner.split(outer,groups=groups[outer]))
            fit,cal=outer[aa],outer[bb]
            assert not set(groups[fit])&set(groups[cal])
            assert not set(groups[outer])&set(groups[val])
            assert not set(fit)&set(cal) and not set(outer)&set(val)
            parts.append({"repeat":repeat,"fold":fold,"fit":fit,"cal":cal,"val":val})
    return parts


def partitions_digest(parts):
    digest=hashlib.sha256()
    for part in parts:
        digest.update(f"{part['repeat']}:{part['fold']}".encode())
        for role in ["fit","cal","val"]:
            array=np.asarray(part[role],dtype='<i8')
            digest.update(f"{role}:{len(array)}:".encode());digest.update(array.tobytes())
    return digest.hexdigest()


def segment_diagnostics(dev,pred,cfg):
    frame=dev.copy();frame["prediction"]=pred
    frame["DriverAgeBand"]=pd.cut(frame.DriverAge,[0,25,35,50,65,120],right=False).astype(str)
    frame["RiskDecile"]=pd.qcut(pd.Series(pred).rank(method="first"),10,labels=False).to_numpy()+1
    rng=np.random.default_rng(cfg["seed"])
    rows=[];mon=cfg["monitoring"]
    for feature in ["DriverAgeBand","Region","Gas","Brand","RiskDecile"]:
        for level,g in frame.groupby(feature,observed=True,dropna=False):
            y=g.ClaimAmount.to_numpy(); p=g.prediction.to_numpy(); total=y.sum()
            effective=total**2/max(np.dot(y,y),1e-12)
            intervals=[]
            for _ in range(400):
                ix=rng.integers(0,len(g),len(g));intervals.append(y[ix].sum()/p[ix].sum())
            top=float(y.max()/max(total,1e-12));claims=int(g.ClaimNb.sum())
            reasons=[]
            if claims<mon["minimum_segment_claims"]:reasons.append("Few claims")
            if effective<mon["minimum_effective_loss_count"]:reasons.append("Low effective loss count")
            if top>mon["maximum_top_loss_share"]:reasons.append("Largest-loss concentration")
            rows.append({"feature":feature,"level":str(level),"policies":len(g),"claims":claims,"positive_policies":int((y>0).sum()),
                "actual_loss":float(total),"expected_loss":float(p.sum()),"actual_over_expected":float(total/p.sum()),
                "AE_bootstrap_lower":float(np.quantile(intervals,.025)),"AE_bootstrap_upper":float(np.quantile(intervals,.975)),
                "effective_loss_count":float(effective),"largest_loss_share":top,"low_credibility":bool(reasons),"review_reasons":"; ".join(reasons)})
    return pd.DataFrame(rows)


def risk_budget(dev,prices,cfg):
    rc=cfg["risk_budget"];rng=np.random.default_rng(cfg["seed"]+333)
    y=dev.ClaimAmount.to_numpy();pos=y[y>0];n=len(prices);p0=len(pos)/len(y)
    target=float(prices.expected_loss.sum());scale=target/(n*y.mean())
    rows=[]
    cases=[(cv,sev,False) for cv in rc["frequency_systematic_cvs"] for sev in rc["severity_factors"]]
    cases.append((0.,1.,True))
    for cv,sev,event in cases:
        sims=rc["simulations"]
        frailty=rng.gamma(1/cv**2,cv**2,sims) if cv else np.ones(sims)
        counts=rng.binomial(n,np.clip(p0*frailty,0,1))
        losses=np.array([rng.choice(pos,size=k,replace=True).sum()*scale*sev for k in counts])
        event_loss=n*rc["common_event_affected_share"]*rc["common_event_loss_per_affected_policy"]
        event_probability=rc["common_event_probability"] if event else 0.
        if event:losses+=rng.binomial(1,event_probability,sims)*event_loss
        expectation=target*sev+event_probability*event_loss
        for q in rc["tail_quantiles"]:
            var=float(np.quantile(losses,q));tail=float(losses[losses>=var].mean())
            capital=max(0,var-expectation)
            rows.append({"scenario":f"Frequency CV {cv:.0%}; severity {sev:.2f}" if not event else "Hypothetical additional common event",
                "frequency_systematic_cv":cv,"severity_factor":sev,"event_probability":event_probability,"quantile":q,
                "expected_loss":expectation,"simulated_mean":float(losses.mean()),"aggregate_loss_quantile":var,"tail_mean":tail,
                "buffer_money_per_policy":capital/n,"assumed_cost_of_capital":rc["cost_of_capital"],
                "CoC_money_margin_per_policy":capital/n*rc["cost_of_capital"],
                "additional_expected_event_loss_per_policy":event_probability*event_loss/n,
                "margin_budget_per_policy":float(prices.money_margin.mean()),
                "status":"CONDITIONAL_DIAGNOSTIC_NOT_SOLVENCY_CAPITAL"})
    return pd.DataFrame(rows)


def stress_grid(prices,cfg):
    rows=[];pcfg=cfg["pricing"];n=len(prices)
    premium=prices.quote.sum();expenses=premium*(pcfg["variable_expense_ratio"]+pcfg["tax_ratio"])+n*pcfg["fixed_expense"]
    for f in [.8,1.,1.1,1.2,1.5]:
        for s in [.8,1.,1.1,1.25,1.5]:
            loss=prices.expected_loss.sum()*f*s
            rows.append({"frequency_factor":f,"severity_factor":s,"combined_ratio":(loss+expenses)/premium,
                "contribution":premium-loss-expenses,"extra_money_margin_for_break_even":max(0,(loss+expenses-premium)/n)})
    return pd.DataFrame(rows)


def population_drift(dev,q):
    rows=[]
    for c in FEATURES:
        if c in CATEGORICAL:
            levels=dev[c].astype(str).unique();a=dev[c].astype(str).value_counts(normalize=True);b=q[c].astype(str).value_counts(normalize=True)
            idx=a.index.union(b.index)
            av=a.reindex(idx,fill_value=0).to_numpy();bv=b.reindex(idx,fill_value=0).to_numpy()
            value=.5*np.abs(av-bv).sum();kind="Total variation"
        else:
            cuts=np.unique(np.quantile(dev[c].dropna(),np.linspace(0,1,11)))
            cuts[0]=-np.inf;cuts[-1]=np.inf
            av=np.histogram(dev[c].dropna(),cuts)[0].astype(float);bv=np.histogram(q[c].dropna(),cuts)[0].astype(float)
            av/=av.sum();bv/=bv.sum();value=stats.ks_2samp(dev[c],q[c]).statistic;kind="KS statistic"
        aa=np.maximum(av,1e-6);bb=np.maximum(bv,1e-6)
        psi=float(np.sum((bb-aa)*np.log(bb/aa)))
        rows.append({"feature":c,"metric":kind,"distance":float(value),"PSI":psi,"interpretation":"Monitoring diagnostic; thresholds are policy assumptions, not hypothesis tests"})
    return pd.DataFrame(rows)


def build(config_path,data_override,output,cache,force=False):
    root=Path(__file__).parent;out=Path(output);out.mkdir(parents=True,exist_ok=True)
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    cfg=json.loads(Path(config_path).read_text(encoding="utf-8"));data=Path(data_override or cfg["data_path"])
    if not cfg["equal_exposure_assumption"]:raise ValueError("Explicit equal-exposure assumption required")
    environment=full_environment(root)
    print("Loading and reconciling original workbook",flush=True)
    d,q,template=load_data(data)
    frozen=pd.read_csv(root/"provenance/frozen_split_manifest.csv")
    if not d.PolicyID.equals(frozen.PolicyID):raise ValueError("Input row IDs differ from frozen source split")
    dev_index=np.flatnonzero(frozen.split.isin(["train","validation"]))
    calibration_index=np.flatnonzero(frozen.split=="calibration")
    excluded_index=np.flatnonzero(frozen.split=="test")
    assert len(set(dev_index)&set(calibration_index))==len(set(dev_index)&set(excluded_index))==0
    dev=d.iloc[dev_index].reset_index(drop=True);level=d.iloc[calibration_index].reset_index(drop=True)
    # Historical test targets are never scored or passed to a v2 fitting/selection function.
    parts=make_partitions(dev,cfg)
    # Stream the audit trail instead of retaining ~780,000 Python dictionaries.
    # This reduces peak memory without altering a split or fitting objective.
    policy_ids=dev.PolicyID.to_numpy()
    with (out/"development_fold_manifest.csv").open("w",newline="",encoding="utf-8") as audit_file:
        writer=csv.writer(audit_file);writer.writerow(["PolicyID","repeat","fold","role"])
        for part in parts:
            for role in ["fit","cal","val"]:
                ix=part[role]
                writer.writerows(zip(policy_ids[ix],itertools.repeat(part["repeat"],len(ix)),itertools.repeat(part["fold"],len(ix)),itertools.repeat(role,len(ix))))
    partition_hash=partitions_digest(parts)
    fingerprint=hashlib.sha256(json.dumps({"data":sha(data),"models":sha(root/"models.py"),"core":sha(root/"engine.py"),"orchestrator":sha(__file__),"partitions":partition_hash,
        "cfg":cfg,"environment":environment["packages"],"frozen_split":sha(root/"provenance/frozen_split_manifest.csv")},sort_keys=True).encode()).hexdigest()
    modes=cfg["calibration_modes"];keys=[f"{name}::{mode}" for name in cfg["candidates"] for mode in modes]
    oof={key:np.zeros((cfg["cv"]["repeats"],len(dev))) for key in keys}
    quote_folds={key:[] for key in keys};rows=[];notices=[];component_rows=[]
    for part in parts:
        rep,fold=part["repeat"],part["fold"]
        fit,cal,val=[dev.iloc[part[k]] for k in ["fit","cal","val"]]
        print(f"CV repeat {rep+1}/{cfg['cv']['repeats']}, fold {fold+1}/{cfg['cv']['folds']}: {len(fit)} fit, {len(cal)} calibration, {len(val)} outer validation",flush=True)
        for name in cfg["candidates"]:
            cachefile=cache/f"{fingerprint[:16]}_{rep}_{fold}_{name}.joblib"
            if cachefile.exists() and not force:
                payload=joblib.load(cachefile)
                if payload["fingerprint"]!=fingerprint:raise ValueError("Stale fold cache")
            else:
                with warnings.catch_warnings(record=True) as captured:
                    warnings.simplefilter("always")
                    model=ResearchModel(name,cfg).fit(fit)
                payload={"fingerprint":fingerprint,"model":model,"cal_raw":model.predict(cal),"val_parts":model.components(val),
                    "quote_raw":model.predict(q),"warnings":[str(w.message) for w in captured]}
                joblib.dump(payload,cachefile,compress=3)
            notices.extend({"repeat":rep,"fold":fold,"model":name,"message":message} for message in payload["warnings"])
            comps=payload["val_parts"];raw=comps["pure_premium"]
            for mode in modes:
                key=f"{name}::{mode}";calibration=calibrate(cal.ClaimAmount,payload["cal_raw"],mode,cfg["cv"]["log_calibration_prior_sd"])
                prediction=raw*calibration["factor"]
                oof[key][rep,part["val"]]=prediction
                quote_folds[key].append(payload["quote_raw"]*calibration["factor"])
                rows.append({"candidate":key,"model":name,"calibration_mode":mode,"complexity":COMPLEXITY[name],"repeat":rep,"fold":fold,
                    "fit_rows":len(fit),"calibration_rows":len(cal),"validation_rows":len(val),"calibration_factor":calibration["factor"],
                    "calibration_reliability":calibration["reliability"],**metrics(val.ClaimAmount,prediction)})
            if "frequency" in comps:
                positive=val.ClaimNb.to_numpy()>0
                freq=comps["frequency"];sev=comps["severity"]
                occurrence=1-np.exp(-freq)
                component_rows.append({"model":name,"repeat":rep,"fold":fold,
                    "frequency_poisson_deviance":float(mean_poisson_deviance(val.ClaimNb,freq)),
                    "frequency_actual_over_expected":float(val.ClaimNb.sum()/freq.sum()),
                    "severity_gamma_deviance_aggregate_mean":float(mean_gamma_deviance(val.ClaimAmount.to_numpy()[positive]/val.ClaimNb.to_numpy()[positive],sev[positive],sample_weight=val.ClaimNb.to_numpy()[positive])),
                    "occurrence_brier_poisson_approximation":float(brier_score_loss(positive,occurrence)),
                    "occurrence_auc_poisson_approximation":float(roc_auc_score(positive,occurrence))})
    fold_scores=pd.DataFrame(rows);fold_scores.to_csv(out/"cv_fold_metrics.csv",index=False)
    summary=fold_scores.groupby(["candidate","model","calibration_mode","complexity"],as_index=False).agg(
        mean_deviance=("tweedie_deviance_p1.5","mean"),sd_deviance=("tweedie_deviance_p1.5","std"),folds=("fold","size"),
        mean_AE=("actual_over_expected","mean"),mean_gini=("concentration_gini","mean"),mean_calibration_factor=("calibration_factor","mean"))
    summary["heuristic_se"]=summary.sd_deviance/np.sqrt(summary.folds)
    summary=summary.sort_values(["mean_deviance","complexity","candidate"]);summary.to_csv(out/"cv_model_summary.csv",index=False)
    components=pd.DataFrame(component_rows);components.to_csv(out/"cv_component_metrics.csv",index=False)
    decision=select_candidate(summary);key=decision["technical_candidate"];name,mode=key.split("::")
    benchmark=decision["constant_benchmark_candidate"]
    print("Locked development selection:",key,flush=True)
    chosen_prediction=oof[key].mean(axis=0);baseline_prediction=oof[benchmark].mean(axis=0)
    uncertainty=bootstrap_comparison(dev.ClaimAmount,chosen_prediction,baseline_prediction,cfg["cv"]["bootstrap_repetitions"],cfg["seed"])
    uncertainty["scope"]="Exploratory paired development-OOF policy bootstrap after candidate selection; excludes refitting and dependence between overlapping training folds. Not a fresh test or post-selection-valid confidence interval."
    dump_json(out/"oof_uncertainty.json",uncertainty)
    oof_output=pd.DataFrame({"PolicyID":dev.PolicyID,"ClaimNb":dev.ClaimNb,"ClaimAmount":dev.ClaimAmount,"candidate_prediction":chosen_prediction,"constant_prediction":baseline_prediction})
    for r in range(cfg["cv"]["repeats"]):oof_output[f"repeat_{r+1}_prediction"]=oof[key][r]
    oof_output.to_csv(out/"development_oof_predictions.csv",index=False)
    segments=segment_diagnostics(dev,chosen_prediction,cfg);segments.to_csv(out/"segment_credibility.csv",index=False)
    model=ResearchModel(name,cfg).fit(dev)
    calibration=calibrate(level.ClaimAmount,model.predict(level),mode,cfg["cv"]["log_calibration_prior_sd"])
    development_scores=model.predict(dev)*calibration["factor"]
    thresholds=np.quantile(development_scores,cfg["pricing"]["tier_quantiles"]).tolist()
    if np.any(np.diff(thresholds)<=0):raise ValueError("No distinct risk tiers; use a flat-margin policy")
    reference={c:sorted(dev[c].astype(str).unique().tolist()) if c in CATEGORICAL else {"min":float(dev[c].min()),"max":float(dev[c].max())} for c in FEATURES}
    engine=MotorPricingEngine(model,calibration,cfg["pricing"],thresholds,reference)
    prior_rows=[]
    for prior in [.10,.20,.40,.80]:
        alternative_cal=calibrate(level.ClaimAmount,model.predict(level),"credibility",prior)
        alternative_prices=quote_prices(model.predict(q)*alternative_cal["factor"],cfg["pricing"],thresholds)
        prior_rows.append({"prior_log_sd":prior,"calibration_factor":alternative_cal["factor"],"reliability":alternative_cal["reliability"],
            "mean_quote_fixed_thresholds":alternative_prices.quote.mean(),"interpretation":"Assumption sensitivity with delivered thresholds locked; not additional performance validation"})
    pd.DataFrame(prior_rows).to_csv(out/"calibration_prior_sensitivity.csv",index=False)
    prices=engine.quote(q)
    quote_components=model.components(q)
    for component in ["frequency","severity"]:
        if component in quote_components:prices[component+"_uncalibrated"]=quote_components[component]
    prices["calibration_factor"]=calibration["factor"]
    if hasattr(model,"freq") and hasattr(model.freq,"coef_"):
        from engine import prepare
        z=model.pre.transform(prepare(q))
        profile={c:dev[c].mode().iloc[0] if c in CATEGORICAL else float(dev[c].median()) for c in FEATURES}
        zref=model.pre.transform(prepare(pd.DataFrame([profile])))
        effects=(z-zref)*model.freq.coef_
        encoded=model.pre.get_feature_names_out()
        reference_frequency=float(np.exp(model.freq.intercept_+zref[0]@model.freq.coef_))
        prices["reference_frequency"]=reference_frequency
        for feature in FEATURES:
            label="LogDensity" if feature=="Density" else feature
            columns=[i for i,term in enumerate(encoded) if term==f"num__{label}" or term.startswith(f"cat__{feature}_")]
            prices["log_frequency_effect_"+feature]=effects[:,columns].sum(axis=1)
        pd.DataFrame({"encoded_term":encoded,"log_frequency_coefficient":model.freq.coef_,"multiplier_per_encoded_unit":np.exp(model.freq.coef_)}).to_csv(out/"frequency_coefficients.csv",index=False)
        dump_json(out/"frequency_reference_profile.json",{"profile":profile,"reference_frequency":reference_frequency,
            "interpretation":"Exact additive log-frequency differences from the declared median/modal reference. They are model explanations, not causal or prohibited-discrimination conclusions. Severity and loss-level calibration are separate."})
    prices.to_csv(out/"quote_detail.csv",index=False)
    prices_with_features=q.merge(prices,on="PolicyID",validate="one_to_one")
    prices_with_features.to_csv(out/"quote_risk_audit.csv",index=False)
    submission=template[["PolicyID"]].merge(prices[["PolicyID","quote"]],on="PolicyID",how="left",validate="one_to_one").rename(columns={"quote":"Premium"})
    submission.to_csv(out/"pricing_submission.csv",index=False,float_format="%.2f")
    joblib.dump(engine,out/"pricing_engine.joblib",compress=3)
    selected_folds=np.asarray(quote_folds[key]);means=selected_folds.mean(axis=1)
    stability=pd.DataFrame({"PolicyID":q.PolicyID,"minimum_fold_quote_loss":selected_folds.min(axis=0),"q10_fold_loss":np.quantile(selected_folds,.1,axis=0),
        "median_fold_loss":np.median(selected_folds,axis=0),"q90_fold_loss":np.quantile(selected_folds,.9,axis=0),"maximum_fold_loss":selected_folds.max(axis=0)})
    stability.to_csv(out/"quote_training_stability.csv",index=False)
    np.savez_compressed(out/"selected_fold_quote_predictions.npz",predictions=selected_folds)
    margins=[]
    for margin_mode in ["flat_constant","tiered_constant","smooth_tiered_constant"]:
        pcfg=cfg["pricing"].copy();pcfg["margin_mode"]=margin_mode
        alt=quote_prices(prices.pure_premium,pcfg,thresholds)
        margins.append({"mode":margin_mode,"mean_quote":alt.quote.mean(),"total_contribution":alt.expected_contribution.sum(),
            "mean_money_margin":alt.money_margin.mean(),"mean_margin_to_pure_ratio":alt.margin_to_pure_ratio.mean()})
    pd.DataFrame(margins).to_csv(out/"margin_policy_comparison.csv",index=False)
    target_budget=float(prices.money_margin.mean())
    matched=[]
    for margin_mode in ["flat_constant","tiered_constant","smooth_tiered_constant"]:
        pcfg=cfg["pricing"].copy();pcfg["margin_mode"]=margin_mode
        if margin_mode=="flat_constant":pcfg["flat_risk_margin"]=target_budget
        else:
            before=quote_prices(prices.pure_premium,pcfg,thresholds)
            pcfg["tier_risk_margins"]=[v*target_budget/before.money_margin.mean() for v in pcfg["tier_risk_margins"]]
        alt=quote_prices(prices.pure_premium,pcfg,thresholds)
        ratio=alt.money_margin/np.maximum(alt.pure_premium,1e-9)
        matched.append({"mode":margin_mode,"common_target_money_per_policy":target_budget,"mean_money_margin":alt.money_margin.mean(),
            "total_contribution":alt.expected_contribution.sum(),"p10_relative_burden":float(ratio.quantile(.1)),"median_relative_burden":float(ratio.median()),
            "p90_relative_burden":float(ratio.quantile(.9)),"scope":"Diagnostic matching of full-quote-population allowance budgets; not a new batch-dependent tariff"})
    pd.DataFrame(matched).to_csv(out/"matched_budget_margin_comparison.csv",index=False)
    grid=np.linspace(max(0,thresholds[0]-30),thresholds[-1]+30,500)
    curves=pd.DataFrame({"pure_premium":grid})
    for mm in ["flat_constant","tiered_constant","smooth_tiered_constant"]:
        pcfg=cfg["pricing"].copy();pcfg["margin_mode"]=mm
        curves[mm]=quote_prices(grid,pcfg,thresholds).quote
    curves.to_csv(out/"margin_threshold_curves.csv",index=False)
    rc=risk_budget(dev,prices,cfg);rc.to_csv(out/"risk_budget_scenarios.csv",index=False)
    stress_grid(prices,cfg).to_csv(out/"frequency_severity_stress.csv",index=False)
    commercial_cfg={**cfg["commercial"],"net_premium_ratio":1-cfg["pricing"]["variable_expense_ratio"]-cfg["pricing"]["tax_ratio"]}
    commercial_rows=[]
    for comp in commercial_cfg["competitor_multipliers"]:
        for tilt in commercial_cfg["competitor_risk_tilts"]:
            for beta in commercial_cfg["elasticities"]:
                for bound_loss in commercial_cfg["bound_loss_multipliers"]:
                    for adjustment in commercial_cfg["quote_adjustments"]:
                        commercial_rows.append(scenario(prices,prices.technical_premium,prices.pure_premium,commercial_cfg,comp,tilt,beta,bound_loss,adjustment))
    commercial=pd.DataFrame(commercial_rows);commercial.to_csv(out/"commercial_scenarios.csv",index=False)
    robust=commercial.groupby("money_adjustment",as_index=False).agg(worst_contribution=("expected_contribution","min"),best_contribution=("expected_contribution","max"),
        minimum_expected_policies=("expected_policies","min"),all_growth_targets_met=("growth_target_met","all"))
    robust,commercial_decision=joint_commercial_decision(robust,commercial_cfg["minimum_worst_expected_contribution"])
    robust.to_csv(out/"commercial_robust_comparison.csv",index=False)
    dump_json(out/"commercial_decision.json",commercial_decision)
    drift=population_drift(dev,q);drift.to_csv(out/"population_drift.csv",index=False)
    tail=tail_analysis(dev,out);dump_json(out/"tail_diagnostics.json",tail)
    glm=ResearchModel("Frequency_GLM_Pooled",cfg).fit(dev)
    counts=glm.components(level)["frequency"]
    zero=zero_diagnostics(dev.ClaimNb,level.ClaimNb,counts)
    zero={k.replace("_test","_level_calibration").replace("heldout_","level_calibration_"):v for k,v in zero.items()}
    zero["evaluation_sample"]="Original level-calibration partition; historical test remains excluded"
    dump_json(out/"zero_count_diagnostics.json",zero)
    # Explanatory PDPs average over observed development profiles; correlations can
    # create unrealistic combinations. These are not causal effects or local attributions.
    sample=dev.sample(n=min(1500,len(dev)),random_state=cfg["seed"])
    effects=[]
    for feature in ["DriverAge","CarAge","Density"]:
        values=np.unique(np.quantile(dev[feature],np.linspace(.01,.99,25)))
        for value in values:
            changed=sample.copy();changed[feature]=value
            effects.append({"feature":feature,"value":float(value),"mean_predicted_loss":float((model.predict(changed)*calibration["factor"]).mean())})
    pd.DataFrame(effects).to_csv(out/"partial_dependence.csv",index=False)
    audit={"source_rows":len(d),"development_rows":len(dev),"level_calibration_rows":len(level),"historical_test_rows_excluded":len(excluded_index),
        "quote_rows":len(q),"development_positive_policies":int((dev.ClaimNb>0).sum()),"development_claims":int(dev.ClaimNb.sum()),
        "development_zero_fraction":float((dev.ClaimNb==0).mean()),"development_mean_loss":float(dev.ClaimAmount.mean()),
        "missing_features":d[FEATURES].isna().sum().to_dict(),"duplicate_development_profiles":int(dev.duplicated(FEATURES).sum()),
        "vehicle_age_over_40_development":int((dev.CarAge>40).sum()),"quote_referrals":int(prices.manual_review.sum()),
        "missing_business_fields":["exposure","dates and valuation","currency","coverage and contract terms","expenses","reinsurance","offered and competitor prices","bind/reject outcomes"],
        "loss_basis":"Assumed aggregate policy loss with equal exposure; paid/incurred/ultimate and currency unverified"}
    dump_json(out/"data_audit.json",audit)
    training_splits=[]
    for label,frame in [("Development",dev),("Level calibration",level)]:
        y=frame.ClaimAmount
        training_splits.append({"sample":label,"policies":len(frame),"mean_loss":float(y.mean()),"claims":int(frame.ClaimNb.sum()),
            "largest_loss":float(y.max()),"top_5_loss_share":float(y.nlargest(5).sum()/y.sum()),"effective_loss_count":float(y.sum()**2/(y*y).sum())})
    pd.DataFrame(training_splits).to_csv(out/"loss_concentration.csv",index=False)
    simple_better=decision["selected_mean_deviance"]<decision["benchmark_mean_deviance"]
    result={"version":cfg["version"],"status":"RESEARCH_ONLY_NOT_APPROVED","decision":decision,"calibration":calibration,
        "development_oof_metrics":metrics(dev.ClaimAmount,chosen_prediction),"development_baseline_metrics":metrics(dev.ClaimAmount,baseline_prediction),
        "selected_mean_fold_deviance_better_than_baseline":bool(simple_better),"uncertainty":uncertainty,"thresholds":thresholds,
        "audit":audit,"mean_quote":float(prices.quote.mean()),"total_quote":float(prices.quote.sum()),
        "mean_money_margin":float(prices.money_margin.mean()),"fold_portfolio_mean_loss_min":float(means.min()),"fold_portfolio_mean_loss_max":float(means.max()),
        "evidence_boundary":"Development-only repeated grouped CV after earlier exploration. Historical test is quarantined; no fresh out-of-time or prospective result exists.",
        "production_blockers":["Loss/exposure/coverage/currency basis unconfirmed","No fresh temporal or prospective validation","Assumed expenses and capital cost","Tail/event dependence and reinsurance not established","No observed conversion or competitor data","Jurisdiction and rating-factor permissibility unspecified"]}
    dump_json(out/"summary.json",result);dump_json(out/"training_warnings.json",notices);dump_json(out/"environment.json",environment)
    manifest={"version":cfg["version"],"seed":cfg["seed"],"input_sha256":sha(data),"config_sha256":sha(config_path),"data_path":str(data.resolve()),
        "source_sha256":{f.name:sha(f) for f in sorted([*root.glob("*.py"),*root.glob("*.html"),*root.glob("*.cjs"),*root.glob("*.ps1"),root/"package.json"])},"frozen_split_sha256":sha(root/"provenance/frozen_split_manifest.csv"),
        "fit_cache_fingerprint":fingerprint,"partitions_sha256":partition_hash,"historical_test_scored_in_v2":False,"config":cfg}
    dump_json(out/"run_manifest.json",manifest)
    from presentation import render
    render(root,out,cfg,q,result)
    print(json.dumps({"selected":key,"mean_quote":result["mean_quote"],"status":result["status"],"output":str(out.resolve())},indent=2),flush=True)
