"""Shared fixed-money pricing and observable assumptions for browser parity."""
import numpy as np
import pandas as pd
from scipy.special import expit,logit


def margin_amount(pure,cfg,thresholds):
    pure=np.asarray(pure,dtype=float); mode=cfg["margin_mode"]
    values=np.asarray(cfg["tier_risk_margins"],dtype=float)
    thresholds=np.asarray(thresholds,dtype=float)
    if len(values)!=len(thresholds)+1 or not np.isfinite(values).all() or not np.isfinite(thresholds).all() or (values<0).any() or (np.diff(values)<0).any() or (np.diff(thresholds)<=0).any():raise ValueError("Invalid locked tiers")
    if mode=="flat_constant":
        if not np.isfinite(cfg["flat_risk_margin"]) or cfg["flat_risk_margin"]<0:raise ValueError("Invalid flat margin")
        return np.full(len(pure),cfg["flat_risk_margin"])
    if mode=="tiered_constant":return values[np.searchsorted(thresholds,pure,side="right")]
    if mode=="smooth_tiered_constant":
        width=float(cfg["transition_width"])
        if not np.isfinite(width) or width<=0:raise ValueError("Positive transition width required")
        # Plateau constants with cubic smoothstep shoulders; transition zones are
        # explicitly nonconstant. The price is continuous and monotone in pure cost.
        result=np.full(len(pure),values[0],dtype=float)
        for k,t in enumerate(thresholds):
            u=np.clip((pure-(t-width/2))/width,0,1)
            result+=(values[k+1]-values[k])*(3*u*u-2*u*u*u)
        return result
    raise ValueError("Unknown monetary margin rule")


def quote_prices(pure,cfg,thresholds):
    pure=np.asarray(pure,dtype=float)
    vals=[cfg[k] for k in ["trend_factor","development_factor","fixed_expense","variable_expense_ratio","tax_ratio","commercial_adjustment"]]
    if not np.isfinite(pure).all() or (pure<0).any() or not np.isfinite(vals).all():raise ValueError("Nonfinite or negative pricing input")
    v,t=cfg["variable_expense_ratio"],cfg["tax_ratio"]
    if v<0 or t<0 or v+t>=1 or cfg["fixed_expense"]<0 or cfg["trend_factor"]<=0 or cfg["development_factor"]<=0:raise ValueError("Invalid financial assumptions")
    loss=pure*cfg["trend_factor"]*cfg["development_factor"]
    money=margin_amount(pure,cfg,thresholds)
    floor=(loss+cfg["fixed_expense"])/(1-v-t)
    technical=(loss+cfg["fixed_expense"]+money)/(1-v-t)
    requested=technical+cfg["commercial_adjustment"]
    quote=np.maximum(requested,floor) if cfg["enforce_cost_floor"] else requested
    if (quote<0).any():raise ValueError("Negative quote")
    return pd.DataFrame({"pure_premium":pure,"expected_loss":loss,"money_margin":money,"fixed_expense":cfg["fixed_expense"],
        "cost_floor":floor,"technical_premium":technical,"quote":quote,"applied_adjustment":quote-technical,
        "floor_applied":requested<floor,"expected_contribution":quote*(1-v-t)-loss-cfg["fixed_expense"],
        "margin_to_pure_ratio":np.divide(money,pure,out=np.zeros_like(pure),where=pure>0)})


def scenario(prices,reference_price,reference_risk,cfg,competitor_multiplier=1.,risk_tilt=0.,elasticity=5.,bound_loss_multiplier=1.,adjustment=0.):
    if competitor_multiplier<=0 or elasticity<=0 or bound_loss_multiplier<=0 or not 0<cfg["reference_conversion"]<1 or cfg["expected_capacity"]<0:raise ValueError("Invalid scenario assumptions")
    ref=np.asarray(reference_price,dtype=float); risk=np.asarray(reference_risk,dtype=float)
    if (ref<=0).any() or (risk<=0).any():raise ValueError("Positive competitor reference basis required")
    competitor=ref*competitor_multiplier*np.exp(risk_tilt*np.log(risk/np.median(risk)))
    offered=np.maximum(prices.technical_premium.to_numpy()+adjustment,prices.cost_floor.to_numpy())
    demand=expit(logit(cfg["reference_conversion"])-elasticity*np.log(offered/competitor))
    unconstrained=demand.sum()
    # Uniform thinning represents random acceptance after demand, preserves the
    # scenario's risk mix, and constrains expected rather than realized volume.
    acceptance=min(1.,cfg["expected_capacity"]/max(unconstrained,1e-12))
    bound=demand*acceptance
    net_ratio=cfg["net_premium_ratio"]
    loss=prices.expected_loss.to_numpy()*bound_loss_multiplier
    contribution=offered*net_ratio-loss-prices.fixed_expense.to_numpy()
    total_premium=float(np.dot(offered,bound)); total_loss=float(np.dot(loss,bound))
    expenses=float(np.dot(offered*(1-net_ratio)+prices.fixed_expense.to_numpy(),bound))
    return {"expected_policies":float(bound.sum()),"unconstrained_expected_policies":float(unconstrained),"random_acceptance_ratio":acceptance,
        "expected_premium":total_premium,"expected_loss":total_loss,"expected_expenses":expenses,
        "expected_contribution":float(np.dot(contribution,bound)),"combined_ratio":(total_loss+expenses)/max(total_premium,1e-12),
        "growth_target_met":bool(bound.sum()>=cfg["minimum_expected_policies"]),
        "competitor_multiplier":competitor_multiplier,"risk_tilt":risk_tilt,"elasticity":elasticity,
        "bound_loss_multiplier":bound_loss_multiplier,"money_adjustment":adjustment}


class MotorPricingEngine:
    def __init__(self,model,calibration,cfg,thresholds,reference):
        self.model,self.calibration,self.cfg,self.thresholds,self.reference=model,calibration,cfg,thresholds,reference

    def quote(self,d):
        from engine import FEATURES,prepare
        if "PolicyID" not in d or d.PolicyID.isna().any() or d.PolicyID.duplicated().any():raise ValueError("Unique complete PolicyID required")
        prepare(d)
        p=self.model.predict(d)*self.calibration["factor"]
        out=quote_prices(p,self.cfg,self.thresholds);out.insert(0,"PolicyID",d.PolicyID.to_numpy())
        reasons=[]
        for row in d[FEATURES].to_dict("records"):
            flags=[]
            for col,ref in self.reference.items():
                value=row[col]
                if pd.isna(value):flags.append("Missing "+col)
                elif isinstance(ref,dict):
                    if not ref["min"]<=value<=ref["max"]:flags.append("Out-of-range "+col)
                elif str(value) not in ref:flags.append("Unseen "+col)
            if pd.notna(row["CarAge"]) and row["CarAge"]>40:flags.append("Vehicle age over 40")
            reasons.append("; ".join(flags))
        out["review_reasons"]=reasons;out["manual_review"]=out.review_reasons!=""
        out["usage_status"]="RESEARCH_ONLY_NOT_APPROVED_TARIFF"
        return out


def joint_commercial_decision(robust,minimum_contribution=0.):
    """Require both robust expected volume and robust expected contribution."""
    if not np.isfinite(minimum_contribution):raise ValueError("Finite contribution floor required")
    comparison=robust.copy()
    comparison["profit_floor_met"]=comparison.worst_contribution>=minimum_contribution
    comparison["joint_constraints_met"]=comparison.all_growth_targets_met&comparison.profit_floor_met
    feasible=comparison[comparison.joint_constraints_met]
    growth_only=comparison[comparison.all_growth_targets_met]
    decision={"scenario_only":True,"feasible":bool(len(feasible)),
        "selected_adjustment":float(feasible.sort_values("worst_contribution",ascending=False).iloc[0].money_adjustment) if len(feasible) else None,
        "growth_only_best_adjustment":float(growth_only.sort_values("worst_contribution",ascending=False).iloc[0].money_adjustment) if len(growth_only) else None,
        "minimum_worst_expected_contribution":float(minimum_contribution),
        "meaning":"Joint growth and worst-case expected-contribution constraints over the configured finite scenario grid. No feasible action is a legitimate result.",
        "warning":"Expected scenario contribution is not a guarantee of realized profit. No demand, equilibrium or actual growth is estimated."}
    return comparison,decision
