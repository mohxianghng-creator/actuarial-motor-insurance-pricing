"""Offline artifacts: decision report, governance evidence and standalone dashboard."""
from pathlib import Path
import html
import json
import hashlib
import base64
import numpy as np
import pandas as pd


def clean_records(frame):
    return json.loads(frame.replace({np.nan:None}).to_json(orient="records",double_precision=12))


def markdown_table(frame):
    cols=list(frame.columns)
    lines=["| "+" | ".join(cols)+" |","| "+" | ".join(["---"]*len(cols))+" |"]
    for row in frame.itertuples(index=False,name=None):
        lines.append("| "+" | ".join(f"{x:.3f}" if isinstance(x,float) else str(x).replace("|","/") for x in row)+" |")
    return "\n".join(lines)


def render(root,out,cfg,q,s):
    root,out=Path(root),Path(out)
    read=lambda f:pd.read_csv(out/f)
    cv=read("cv_model_summary.csv");segments=read("segment_credibility.csv");capital=read("risk_budget_scenarios.csv")
    prices=read("quote_risk_audit.csv");robust=read("commercial_robust_comparison.csv");drift=read("population_drift.csv")
    commercial_decision=json.loads((out/'commercial_decision.json').read_text(encoding='utf-8'))
    commercial_conclusion='A listed action meets both joint constraints.' if commercial_decision['feasible'] else 'No listed action meets both the minimum expected policy volume and the worst-case expected-contribution floor. Revisit targets, risk selection, money rules or the scenario assumptions before recommending a commercial action.'
    robust_report=robust[['money_adjustment','worst_contribution','minimum_expected_policies','all_growth_targets_met','profit_floor_met','joint_constraints_met']].rename(columns={'money_adjustment':'Money action','worst_contribution':'Worst contribution','minimum_expected_policies':'Minimum expected policies','all_growth_targets_met':'Growth met','profit_floor_met':'Profit floor met','joint_constraints_met':'Joint feasible'})
    concentration=read("loss_concentration.csv");folds=read("cv_fold_metrics.csv")
    matched=read("matched_budget_margin_comparison.csv")
    prior_sensitivity=read("calibration_prior_sensitivity.csv")
    manifest=json.loads((out/"run_manifest.json").read_text(encoding="utf-8"))
    make_evidence_figure(out,s)
    legacy=json.loads((root/"provenance/legacy_v1_summary.json").read_text(encoding="utf-8"))
    def table(df):return df.to_html(index=False,border=0,float_format=lambda x:f"{x:,.3f}",escape=True)
    controls=[
        {"area":"Intended use","control":"Research-only quote exploration; prohibit production integration until approval","status":"IMPLEMENTED_USAGE_LABEL; APPROVAL_NOT_OBTAINED","evidence":"summary.json; quote_detail.csv"},
        {"area":"Data basis","control":"Confirm exposure, valuation, currency, coverage and ultimate loss basis","status":"OPEN_DATA_REQUIREMENT","evidence":"data_audit.json"},
        {"area":"Validation","control":"Use grouped development folds; quarantine historical test; independently review methodology","status":"CODE_CONTROLS_IMPLEMENTED; EXTERNAL_REVIEW_PENDING","evidence":"development_fold_manifest.csv; run_manifest.json"},
        {"area":"Fair treatment","control":"Compare monetary and relative burden, boundary jumps and segment calibration","status":"DIAGNOSTICS_IMPLEMENTED; NO_FAIRNESS_CERTIFICATION","evidence":"segment_credibility.csv; margin_policy_comparison.csv; margin_threshold_curves.csv"},
        {"area":"Rating factors","control":"Check local permission and proxy risks for age, territory, vehicle and fuel factors","status":"JURISDICTION_UNSPECIFIED","evidence":"FEATURE_REGISTER.md"},
        {"area":"Commercial pricing","control":"Keep risk-cost and demand assumptions distinct; review vulnerability and renewal practices","status":"SCENARIO_ONLY; NO_OBSERVED_DEMAND","evidence":"commercial_scenarios.csv"},
        {"area":"Model changes","control":"Version input, source, configuration, assumptions and test evidence","status":"IMPLEMENTED_HASH_MANIFEST","evidence":"run_manifest.json; verification.json"},
        {"area":"Referrals","control":"Flag missing, unseen, out-of-range and unusual vehicle ages before operational use","status":"FLAGS_IMPLEMENTED; NO_OPERATIONAL_WORKFLOW","evidence":"quote_detail.csv"},
        {"area":"Monitoring","control":"Monitor emerging ultimate A/E, credibility, mix, quote changes, bind and complaints","status":"DESIGNED; NO_LIVE_FEED","evidence":"MONITORING_AND_CHANGE_CONTROL.md"},
        {"area":"Independent approval","control":"Require actuarial, underwriting, finance, compliance and engineering sign-off","status":"NOT_OBTAINED","evidence":"DECISION_MEMO.md"},
    ]
    pd.DataFrame(controls).to_csv(out/"governance_register.csv",index=False)
    modelcard={"model_id":"CAS_MOTOR_V2_RESEARCH","candidate":s["decision"]["technical_candidate"],"intended_use":"Offline actuarial research, portfolio demonstration and hypothesis evaluation",
        "prohibited_claims":["Production-approved tariff","Fresh independent test improvement","Regulatory compliance certification","Estimated competitor behavior","Capital adequacy or black-swan prediction"],
        "required_input_columns":list(q.columns),"rating_features":[c for c in q.columns if c!="PolicyID"],"target":"Aggregate loss per assumed equal-exposure policy", "evidence":s["evidence_boundary"],
        "selection":s["decision"],"level_calibration":s["calibration"],"production_blockers":s["production_blockers"],"jurisdiction":cfg["jurisdiction"]}
    (out/"model_card.json").write_text(json.dumps(modelcard,indent=2),encoding="utf-8")
    selected=s["decision"]["technical_candidate"]
    best=s["decision"]["best_mean_candidate"]
    dev=s["development_oof_metrics"];base=s["development_baseline_metrics"]
    improvement=1-s["decision"]["selected_mean_deviance"]/s["decision"]["benchmark_mean_deviance"]
    low=int(segments.low_credibility.sum())
    basecapital=capital[(capital.frequency_systematic_cv==0)&(capital.severity_factor==1)&(capital.event_probability==0)&(capital["quantile"]==.99)].iloc[0]
    decisive=f'''# Motor Pricing Decision Memo — Version 2

**Decision:** retain research status. The technical research candidate is `{selected}`;
the lowest mean development-CV candidate is `{best}`. Their roles are distinguished.
Do not approve production or claim a measured market-profit uplift from this workbook.

## Evidence that informs the decision

- Repeated grouped development validation compares 9 model structures x 3 calibration strategies on 10 outer folds.
- Mean-fold selected deviance is {s['decision']['selected_mean_deviance']:.3f}; the independently calibrated constant comparator is {s['decision']['benchmark_mean_deviance']:.3f}.
- Relative development-CV change is {improvement:.2%}. This is model-development evidence after earlier exploration, not fresh external validation.
- Level calibration factor is {s['calibration']['factor']:.3f}; the raw level ratio is {s['calibration']['raw_ratio']:.3f}.
- {low} of {len(segments)} reported segment/decile cells carry a credibility flag.
- Mean illustrative quote is {s['mean_quote']:.2f}; mean configured money allowance is {s['mean_money_margin']:.2f} in unspecified dataset units.
- A conditional independent-policy empirical 99% loss buffer is {basecapital.buffer_money_per_policy:.2f} per policy. At assumed 12% capital cost, the indicative charge is {basecapital.CoC_money_margin_per_policy:.2f}; neither is an approved risk premium.

## Recommended actions

1. Confirm exposure, period, currency, coverage and loss valuation/development before calling the output an annual prospective tariff.
2. Review frequency and severity separately; determine which segment movements reflect ordinary claims versus scarce large losses.
3. Obtain new temporal/prospective experience for an evaluation independent of this development cycle.
4. Agree the financial purpose of monetary allowances: capital cost, model uncertainty, fixed profit or some explicit combination without double counting.
5. Prefer continuous transitions if bands are required, and review their relative burden and customer explanation.
6. Obtain actual expense, reinsurance and quote/bind information before validating capital or commercial decisions.
7. Complete jurisdiction-specific factor, fairness, privacy, filing and conduct review; obtain independent actuarial and business approval.

## Commercial conclusion

{commercial_conclusion}

The competitor, elasticity, adverse-selection and capacity outputs are conditional scenarios.
A scenario-level growth target may be infeasible. The engine reports infeasibility rather than manufacturing an optimum.
Expected-volume rationing is not a guarantee of a realized policy cap. No price-walking or individual willingness-to-pay model is deployed.

## Evidence boundary

{s['evidence_boundary']}
V1 test results are retained only as a dated development-history snapshot in `provenance/legacy_v1_summary.json`.
They are not recomputed or used to select V2. There is no unseen test result available for this iteration.
'''
    (root/"DECISION_MEMO.md").write_text(decisive,encoding="utf-8")
    narrative=[
        ("Decision and scope",f"The technical research candidate is {selected}; the lowest mean development-CV candidate is {best}. Mean-fold deviance is {s['decision']['selected_mean_deviance']:.3f}, versus {s['decision']['benchmark_mean_deviance']:.3f} for the constant comparator. The relative development-CV improvement is {improvement:.2%}. This result does not establish a fresh independent or commercial performance advantage. Deployment remains blocked by loss-basis, validation, capital, expense, demand and jurisdiction gaps."),
        ("Data contract and genuine dataset constraints",f"The workbook contains {s['audit']['source_rows']:,} labeled policy records and {s['audit']['quote_rows']:,} quote risks. Development uses {s['audit']['development_rows']:,} rows; level calibration uses {s['audit']['level_calibration_rows']:,}; the {s['audit']['historical_test_rows_excluded']:,} previously viewed historical test rows are excluded from all V2 fitting, selection and scoring. Exposure is absent, so each row is assumed to represent equal coverage duration and contract basis. ClaimAmount is assumed aggregate policy loss; paid/incurred/ultimate status, currency, coverage and limits are unverified. PolicyID restarts on the quote sheet and is never a rating feature. {s['audit']['vehicle_age_over_40_development']} development vehicle ages exceed 40; retained records and quote referrals preserve the evidence rather than silently correcting it."),
        ("Validation design and what it can prove","Two repeats of five StratifiedGroupKFold outer splits operate only within the original train+validation population. Identical seven-feature profiles are kept together as a conservative leakage sensitivity; this does not establish customer identity and can be stricter than required for genuinely independent customers. Each outer training partition is split by group into fitting and calibration samples. Preprocessing, frequency/severity estimation and body thresholds use fitting rows only; calibration uses the inner calibration sample; all compared candidates are scored on the same outer validation rows. None/full/credibility calibration are compared as complete pipelines. The final structure and calibration mode are frozen before fitting development and applying the original level-calibration sample. Earlier exploratory work means development-CV estimates are not a fresh prospective test."),
        ("Selection and uncertainty","A declared set of nine structures is compared under three calibration strategies. The lowest mean-fold deviance is reported, but a simpler nonconstant candidate within a heuristic one-standard-error threshold is selected as the technical research candidate. The constant is a mandatory comparator and may be the more defensible benchmark; excluding it from the risk-factor candidate shortlist is explicit, not evidence that the challenger is better. Repeated folds overlap: SD/sqrt(10) is a complexity-selection heuristic, not a valid independent-fold confidence interval. Conditional policy-bootstrap OOF intervals are post-selection exploratory diagnostics and omit refitting/dependence. Fold quote ranges describe training/calibration instability, not individual prediction intervals."),
        ("Frequency, zeros and average severity","Poisson frequency GLM/GAM, pooled mean severity, capped-body-plus-restored-excess models, GBM hurdle and direct Tweedie GAM candidates test distinct hypotheses. The direct Tweedie power is fixed at 1.5. Severity is aggregate policy loss / claim count, fitted with claim-count weights; this targets mean severity but cannot reconstruct individual-claim likelihoods or severities. Pooled-severity challengers test whether the sparse severity sample actually supports factor-specific estimation. Body models blend fitted capped severity and pooled capped severity using an explicitly assumed prior claim count of 5,000, then restore empirical excess per claim. These are credibility assumptions, not fitted Bühlmann parameters. All actual uncapped losses remain in evaluation. Many zeros follow naturally from low frequency; intercept Poisson/NB/ZIP diagnostics do not identify a causal structural-zero population. Hurdle positive-count means are constrained to at least one."),
        ("Calibrating price level without hiding instability",f"The chosen level calibration uses mode {s['calibration']['mode']}. Its raw actual/predicted ratio is {s['calibration']['raw_ratio']:.3f}, factor {s['calibration']['factor']:.3f}, and log-level reliability {s['calibration']['reliability']:.3f}. Credibility calibration uses a zero-centered Normal prior on the log level with configured SD 0.20 and a delta-method residual variance approximation. This is a transparent reliability heuristic, not a validated Bayesian posterior under heavy-tail dependence. Full, partial and absent calibration are all validated at outer-fold level. A portfolio A/E near one does not establish correct relativities. Segment diagnostics report claim count, effective loss count (sum loss squared / sum squared loss), largest-loss share and conditional bootstrap ranges. Numeric policy thresholds define review flags, not proof of statistical significance or fairness."),
        ("Expected loss, money allowance and quote identity","Expected prospective loss = modeled policy loss x trend x development. Technical premium = (expected prospective loss + fixed expense + monetary allowance) / (1 - variable-expense ratio - tax ratio). Quote = max(technical premium + commercial money adjustment, expected-loss-and-expense floor) under the default policy. Percentage expense factors are costs, not percentage risk markups. Trend/development default to 1.0 because dates and valuation are absent; expenses and 10/20/35 allowances are illustrative business assumptions. The floor covers expected costs only; it cannot guarantee a realized underwriting profit. Unrounded values are retained and submission CSV rounds to cents."),
        ("Fixed money, fairness and continuous shoulders","The dashboard compares a single flat allowance, strict piecewise-constant bands and plateau constants connected by cubic smoothstep shoulders. Strict bands use development-locked score thresholds. Shoulder zones are deliberately nonconstant: a continuous approximation to banded money, not a claim of strict constancy everywhere. Smoothstep adds no percentage markup and avoids discontinuous quote jumps; overlapping transition zones can remove a plateau and are visible in the chart. Equal money can impose larger relative loads on low-risk customers. Neither equal absolute contribution nor tiering alone proves fair treatment. Report affordability, cross-subsidy, capital contribution, boundary effects and permissible factors separately. Changing quote-batch filters never moves thresholds."),
        ("Risk budget and unseen events","A binomial-positive-policy/empirical-positive-loss simulation is an IID bootstrap of whole development-policy losses, globally mean-scaled to the quoted portfolio. It cannot recover individual-claim tail, missing coverage terms, unseen losses or loss development. Alternative cases add a Gamma common frequency multiplier (systematic CV 10%), severity inflation 25%, or an explicitly hypothetical additional common event (probability 2%, affected share 10%, loss 20,000 per affected policy). These quantities are assumptions, not estimated catastrophe parameters. Tables distinguish expected loss, loss quantile, capital buffer, tail mean and cost of holding capital at assumed 12%. Additional expected event loss is reported separately; capital cost does not replace its expected-loss charge. No reinsurance, reserve capital or regulatory solvency calculation is claimed. GPD policy-aggregate tail fits at several thresholds remain exploratory and do not automatically enter prices."),
        ("Commercial growth, capacity and competitors","Competitor reference quotes are fixed to the delivered technical reference rather than updated automatically when the user changes our pricing controls. A competitor risk tilt can favor different risks. Logistic relative-price demand, reference conversion and elasticities are assumed; a bound-loss multiplier explores unobserved adverse selection. Uniform random acceptance constrains expected volume and preserves the scenario demand mix, but does not guarantee a realized capacity cap. The finite grid contains 216 combinations of actions and scenarios. The robust comparison requires both the minimum expected volume and the configured worst-case expected-contribution floor. An empty feasible set is a valid result. "+commercial_conclusion+" No learned market demand, Nash equilibrium, causal price elasticity, customer vulnerability detection or actual profit uplift is claimed."),
        ("Regulatory and professional reasoning","Jurisdiction is unspecified. ASOP 56 is a US professional modeling reference, not a global legal rule. IAIS conduct/fair-treatment material informs governance thinking rather than certifying compliance. EIOPA differential-pricing guidance is an EU supervisory comparator; it is not automatically applicable to this portfolio. Separate technical risk differentiation from willingness-to-pay pricing, review age/territory/proxy factors locally, provide intelligible quote explanations and human referral, and obtain documented approval. Do not label a lack of detected group disparity as fairness certification: protected-class/vulnerability labels and legal criteria are absent. Commercial scenario controls are not deployed customer-level demand pricing."),
        ("Monitoring, change control and limits of offline assurance","Monitor emerging developed loss ratios/A/E by credible segment, frequency, severity, large-loss concentration, mix drift, referrals, quote movements, bind rates and complaints. Configured monitoring thresholds are illustrative review triggers, not legal tolerances or automated proof of deterioration. Freeze model, data basis, rating tables, calibration and commercial actions separately. An offline source/config/data manifest, complete installed dependency closure, numerical thread controls, trusted model artifact, automated financial/data-contract tests, browser interaction tests and fresh-process scoring parity support reproducibility. Cached fits are content-addressed against model sources, input hash, split, configuration and package versions. Exact reproduction across different numerical hardware is not guaranteed. No test count is presented as equivalent to independent actuarial validation."),
        ("Candidate contribution and interview positioning","The defensible achievement is building an auditable research-and-decision workflow, finding where evidence is weak and communicating when a tariff should not be approved. Demonstrate one quote, a monetary-margin tradeoff, a failed business scenario and the reason for a governance gate. Understand and explain assumptions personally and describe assistance accurately if asked. The bonus generic motor adapter supports exposure-aware GLM/GAM/GBM fitting but has synthetic contract tests only; it is not a universal production GI platform. R/Excel conversion is deferred until methodology review."),
    ]
    report_md="# CAS Motor Pricing — Actuarial Research and Decision Report\n\nVersion 2.0.0 · English deliverables · Offline execution · Research only\n\n"
    for title,text in narrative:report_md+=f"## {title}\n\n{text}\n\n"
    top=cv.head(12)[["candidate","mean_deviance","sd_deviance","mean_AE"]]
    report_md+="## Development CV comparison (first 12 by mean score)\n\n"+markdown_table(top)+"\n\n"
    report_md+="## Business scenario robustness\n\n"+markdown_table(robust_report)+"\n\n"
    report_md+="## Calibration prior sensitivity\n\n"+markdown_table(prior_sensitivity.drop(columns="interpretation"))+"\n\nThese are assumption sensitivities with the delivered thresholds locked, not further validation results.\n\n"
    report_md+="## Money rules at a matched allowance budget\n\n"+markdown_table(matched.drop(columns="scope"))+"\n\nMatching the default full-book budget is a diagnostic normalization, not a batch-dependent tariff.\n\n"
    report_md+="## Source references and scope\n\n"
    refs=[("ASOP 56 Modeling — US professional reference","https://www.actuarialstandardsboard.org/wp-content/uploads/2020/01/asop056_195.pdf"),
          ("IAIS fair treatment application paper announcement — international supervisory reference","https://www.iais.org/2025/07/iais-publishes-application-paper-on-fair-treatment-of-a-wide-range-of-consumers/"),
          ("EIOPA differential pricing statement — EU comparator","https://www.eiopa.europa.eu/publications/supervisory-statement-differential-pricing-practices-non-life-insurance-lines-business_en"),
          ("scikit-learn insurance frequency/severity/Tweedie example","https://scikit-learn.org/1.8/auto_examples/linear_model/plot_tweedie_regression_insurance_claims.html")]
    for title,url in refs:report_md+=f"- [{title}]({url})\n"
    report_md+="\nReferences inform methodological reasoning; they neither certify this project nor establish jurisdiction-specific compliance. No external connection is required to read the report or use the dashboard.\n"
    (out/"ACTUARIAL_REPORT.md").write_text(report_md,encoding="utf-8")
    style="""body{font:15px/1.65 'Segoe UI',Arial,sans-serif;color:#183247;max-width:1060px;margin:40px auto;padding:0 28px}h1{font-size:36px;line-height:1.2}h2{font-size:23px;margin-top:32px}p{max-width:1000px}table{border-collapse:collapse;width:100%;font-size:11px}td,th{padding:7px;border-bottom:1px solid #d9e3e8;text-align:left}th{background:#edf3f5}.tag{color:#8b571b;font-weight:700}.meta{color:#526b7b}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{background:#edf5f6;padding:16px;flex:1;border-radius:8px}.metric strong{display:block;font-size:25px}a{color:#096c76}@page{size:A4;margin:17mm 15mm 19mm} @media print{body{margin:0;padding:0;font-size:10px;line-height:1.5}h1{font-size:25px}h2{font-size:16px;break-after:avoid}table{font-size:7px}tr{break-inside:avoid}.metric{padding:9px}.metric strong{font-size:18px}a{color:inherit;text-decoration:none}}"""
    figure_uri='data:image/png;base64,'+base64.b64encode((out/'EVIDENCE_FIGURE.png').read_bytes()).decode()
    report_html=f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>CAS Motor Pricing — Actuarial Report</title><style>{style}</style></head><body><p class="meta">ACTUARIAL DECISION RESEARCH · VERSION 2.0.0</p><h1>CAS Motor Pricing</h1><p class="tag">Research only · No fresh independent validation · No regulatory approval</p><div class="metrics"><div class="metric"><strong>{html.escape(selected)}</strong>Technical candidate</div><div class="metric"><strong>{improvement:+.2%}</strong>Development-CV improvement vs comparator</div><div class="metric"><strong>{s["mean_quote"]:.2f}</strong>Mean illustrative quote</div></div><h2>Evidence at a glance</h2><img src="{figure_uri}" alt="Development-fold differences, segment uncertainty, money-rule behavior and hypothetical business robustness" style="width:100%;break-inside:avoid;display:block">'
    for title,text in narrative:report_html+=f"<h2>{html.escape(title)}</h2><p>{html.escape(text)}</p>"
    report_html+="<h2>Development CV comparison</h2>"+table(top)+"<h2>Loss concentration</h2>"+table(concentration)
    report_html+="<h2>Business scenario robustness</h2>"+table(robust_report)+"<p>"+html.escape(commercial_conclusion)+"</p><h2>Risk-budget diagnostics</h2>"+table(capital[capital["quantile"]==.99][["scenario","buffer_money_per_policy","CoC_money_margin_per_policy","additional_expected_event_loss_per_policy"]])
    report_html+="<h2>Calibration prior sensitivity</h2>"+table(prior_sensitivity.drop(columns="interpretation"))+"<p>Assumption sensitivities with delivered thresholds locked, not new validation results.</p><h2>Matched-budget monetary allowances</h2>"+table(matched.drop(columns="scope"))+"<p>Diagnostic normalization on the full default quote book; not a batch-dependent tariff or a fairness certification.</p>"
    report_html+="<h2>Source references and applicability</h2><ul>"+"".join(f'<li><a href="{html.escape(url)}">{html.escape(title)}</a></li>' for title,url in refs)+"</ul><p>These sources inform professional reasoning; no jurisdiction-specific compliance conclusion is made.</p><p class=meta>Input SHA256: "+manifest["input_sha256"]+"</p><p class=meta>Config SHA256: "+manifest["config_sha256"]+"</p></body></html>"
    (out/"ACTUARIAL_REPORT.html").write_text(report_html,encoding="utf-8")
    payload={"summary":s,"config":cfg,"manifest":manifest,"quotes":clean_records(prices),"cv":clean_records(cv),"folds":clean_records(folds),
        "segments":clean_records(segments),"capital":clean_records(capital),"robust":clean_records(robust),"drift":clean_records(drift),
        "governance":controls,"partial_dependence":clean_records(read("partial_dependence.csv")),"legacy_v1":legacy,
        "stability":clean_records(read("quote_training_stability.csv")),"concentration":clean_records(concentration),
        "matched_budget":clean_records(matched),"prior_sensitivity":clean_records(prior_sensitivity),"commercial_decision":commercial_decision}
    data_json=json.dumps(payload,separators=(",",":"),allow_nan=False).replace("</","<\\/")
    template=(root/"dashboard_template.html").read_text(encoding="utf-8")
    (out/"DASHBOARD.html").write_text(template.replace("__EMBEDDED_DATA__",data_json),encoding="utf-8")
    make_notebook(root,out)


def make_notebook(root,out):
    cells=[]
    def md(s):cells.append({"cell_type":"markdown","metadata":{},"source":s.splitlines(True)})
    def code(s):cells.append({"cell_type":"code","metadata":{},"execution_count":None,"outputs":[],"source":s.splitlines(True)})
    md("# CAS Motor Pricing V2 — Decision Walkthrough\n\nOffline evidence, explicit assumptions and business judgment. These are development results after prior exploration, not a fresh independent test.")
    code("from pathlib import Path\nimport json\nimport pandas as pd\nfrom IPython.display import display\nROOT=Path.cwd()\nRESULTS=ROOT/'results'\ns=json.loads((RESULTS/'summary.json').read_text())\ndisplay(s['decision'])\ndisplay(s['audit'])")
    md("## Compare complete fitting/calibration pipelines\nThe constant benchmark remains mandatory. One-SE is a simplicity heuristic because repeated folds overlap.")
    code("display(pd.read_csv(RESULTS/'cv_model_summary.csv'))\ndisplay(pd.read_csv(RESULTS/'cv_component_metrics.csv').groupby('model').mean(numeric_only=True))\ndisplay(s['uncertainty'])")
    md("## Credibility is about claims and loss concentration\nA segment with many policies can still be dominated by a few claims. These diagnostics do not prove prohibited discrimination or fair treatment.")
    code("segments=pd.read_csv(RESULTS/'segment_credibility.csv')\ndisplay(segments.sort_values('effective_loss_count').head(12))\ndisplay(pd.read_csv(RESULTS/'loss_concentration.csv'))")
    md("## Trace a quote and compare money rules\nSmooth shoulders connect constant plateaus; transition zones are not strictly constant. Thresholds are development-locked.")
    code("from pricing import quote_prices\nq=pd.read_csv(RESULTS/'quote_detail.csv')\ncfg=json.loads((ROOT/'config.json').read_text())\ndisplay(q.head().T)\nfor mode in ['flat_constant','tiered_constant','smooth_tiered_constant']:\n    p=cfg['pricing'].copy();p['margin_mode']=mode\n    alternative=quote_prices(q.pure_premium,p,s['thresholds'])\n    print(mode,alternative.quote.mean(),alternative.expected_contribution.sum())")
    md("## Separate capital buffer, capital cost and expected event loss\nThe scenarios are empirical conditional diagnostics, not a solvency model. Never substitute capital cost for an additional expected-loss charge.")
    code("display(pd.read_csv(RESULTS/'risk_budget_scenarios.csv').query('quantile == 0.99'))\ndisplay(pd.read_csv(RESULTS/'commercial_robust_comparison.csv'))\ndisplay(json.loads((RESULTS/'commercial_decision.json').read_text()))")
    md("## Explain the approval decision\nProfessional references inform a review framework. Jurisdiction, factor permission and regulatory approval remain unspecified.")
    code("display(pd.read_csv(RESULTS/'governance_register.csv'))\ndisplay(s['production_blockers'])\ndisplay(json.loads((RESULTS/'run_manifest.json').read_text()))")
    for i,cell in enumerate(cells):cell['id']=f'v2-cell-{i:02d}'
    notebook={"cells":cells,"nbformat":4,"nbformat_minor":5,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}}}
    (root/"walkthrough.ipynb").write_text(json.dumps(notebook,indent=2),encoding="utf-8")


def make_evidence_figure(out,s):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.style.use('seaborn-v0_8-whitegrid')
    folds=pd.read_csv(out/'cv_fold_metrics.csv')
    mode=s['calibration']['mode']
    f=folds[folds.calibration_mode==mode].copy()
    baseline=f[f.model=='Constant'][['repeat','fold','tweedie_deviance_p1.5']].rename(columns={'tweedie_deviance_p1.5':'constant_deviance'})
    f=f.merge(baseline,on=['repeat','fold'],validate='many_to_one')
    f['difference']=f['tweedie_deviance_p1.5']-f.constant_deviance
    d=f.groupby('model').difference.agg(['mean','std']).sort_values('mean',ascending=False)
    labels={'Constant':'Constant','Frequency_GLM_Pooled':'GLM frequency / pooled severity','Frequency_GAM_Pooled':'GAM frequency / pooled severity',
        'GLM_BodyTail':'GLM body + pooled tail','GAM_BodyTail':'GAM body + pooled tail','GBM_BodyTail':'GBM body + pooled tail',
        'GLM_FullSeverity':'GLM full severity','GBM_Hurdle':'GBM hurdle','Tweedie_GAM':'Tweedie GAM'}
    fig,axes=plt.subplots(2,2,figsize=(13,8.8),gridspec_kw={'width_ratios':[1.25,1]})
    ax=axes[0,0];y=np.arange(len(d))
    ax.errorbar(d['mean'],y,xerr=d['std'],fmt='o',color='#087f82',ecolor='#b4cdd0',capsize=3)
    ax.axvline(0,color='#a23e37',lw=1);ax.set_yticks(y,[labels[x] for x in d.index],fontsize=8)
    ax.set_title('A. Paired development-fold loss difference',loc='left',fontsize=12)
    ax.set_xlabel('Difference vs constant; whiskers = fold SD, not CI',fontsize=9)
    ax=axes[0,1];seg=pd.read_csv(out/'segment_credibility.csv');seg=seg[seg.feature=='DriverAgeBand']
    yy=np.arange(len(seg));a=seg.actual_over_expected.to_numpy();lo=seg.AE_bootstrap_lower.to_numpy();hi=seg.AE_bootstrap_upper.to_numpy()
    ax.errorbar(a,yy,xerr=np.array([a-lo,hi-a]),fmt='o',color='#b97322',ecolor='#dec7a8',capsize=3)
    ax.axvline(1,color='#087f82',lw=1);ax.set_yticks(yy,seg.level,fontsize=8)
    ax.set_title('B. Driver-age calibration uncertainty',loc='left',fontsize=12)
    ax.set_xlabel('A/E; conditional OOF bootstrap range',fontsize=9)
    ax=axes[1,0];curves=pd.read_csv(out/'margin_threshold_curves.csv')
    for name,color,label in [('flat_constant','#376baf','Flat'),('tiered_constant','#b97322','Strict tiers'),('smooth_tiered_constant','#087f82','Smooth shoulders')]:
        ax.plot(curves.pure_premium,curves[name],color=color,label=label,lw=2)
    for threshold in s['thresholds']:ax.axvline(threshold,color='#8ea3ad',lw=.8,ls='--')
    ax.set_title('C. Fixed-money boundary behavior',loc='left',fontsize=12)
    ax.set_xlabel('Pure expected loss',fontsize=9);ax.set_ylabel('Illustrative quote',fontsize=9);ax.legend(fontsize=8,frameon=False)
    ax=axes[1,1];robust=pd.read_csv(out/'commercial_robust_comparison.csv')
    values=robust.worst_contribution/1000
    ax.bar(robust.money_adjustment.astype(str),values,color=['#087f82' if v>=0 else '#b56c56' for v in values])
    ax.axhline(0,color='#6e8794',lw=.8);ax.set_title('D. Hypothetical market robustness',loc='left',fontsize=12)
    ax.set_xlabel('Fixed commercial money adjustment',fontsize=9);ax.set_ylabel('Worst contribution (thousands)',fontsize=9)
    for ax in axes.flat:ax.tick_params(labelsize=8);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('CAS Motor Pricing | Evidence and Decision Tradeoffs',fontsize=16,x=.04,ha='left',color='#183247')
    fig.text(.04,.012,'Development-only evidence after prior exploration. Overlapping folds are not independent. Commercial results are conditional scenarios. Monetary units unspecified.',fontsize=8,color='#617785')
    fig.tight_layout(rect=[0,.035,1,.95],h_pad=3,w_pad=2)
    fig.savefig(out/'EVIDENCE_FIGURE.png',dpi=180,bbox_inches='tight')
    fig.savefig(out/'EVIDENCE_FIGURE.pdf',bbox_inches='tight')
    plt.close(fig)
