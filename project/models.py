"""Declared candidate set, train-only severity pooling and independent calibration."""
import numpy as np
from sklearn.linear_model import PoissonRegressor, GammaRegressor
from engine import LossModel, preprocessor

COMPLEXITY={"Constant":0,"Frequency_GLM_Pooled":1,"Frequency_GAM_Pooled":2,"GLM_BodyTail":3,
            "GAM_BodyTail":4,"GLM_FullSeverity":4,"Tweedie_GAM":5,"GBM_BodyTail":6,"GBM_Hurdle":7}


class ResearchModel:
    def __init__(self,name,cfg):
        if name not in COMPLEXITY: raise ValueError("Unknown declared candidate")
        self.name,self.cfg=name,cfg

    def fit(self,d):
        from engine import prepare
        x=prepare(d); n=d.ClaimNb.to_numpy(); y=d.ClaimAmount.to_numpy(); pos=n>0
        self.pool_frequency=float(n.mean())
        self.pool_severity=float(y.sum()/n.sum())
        self.pool_loss=float(y.mean())
        if self.name=="Constant":return self
        if self.name in ["GLM_FullSeverity","GBM_Hurdle","Tweedie_GAM"]:
            if self.name=="GLM_FullSeverity":self.base=LossModel(self.name,alpha=.01,seed=self.cfg["seed"])
            elif self.name=="GBM_Hurdle":self.base=LossModel(self.name,kind="tree",leaves=7,hurdle=True,seed=self.cfg["seed"])
            else:self.base=LossModel(self.name,kind="spline",alpha=.1,direct=True,seed=self.cfg["seed"])
            self.base.fit(x,d)
            return self
        kind="tree" if "GBM" in self.name else "spline" if "GAM" in self.name else "linear"
        self.pre=preprocessor(kind); z=self.pre.fit_transform(x)
        self.freq=LossModel("helper",kind="tree",leaves=7,seed=self.cfg["seed"])._tree("poisson") if kind=="tree" else PoissonRegressor(alpha=.01,max_iter=600,tol=1e-7)
        self.freq.fit(z,n)
        if self.name.startswith("Frequency_"):return self
        sev=y[pos]/n[pos]
        self.threshold=float(np.quantile(sev,self.cfg["body_quantile"]))
        body=np.minimum(sev,self.threshold)
        self.pooled_body=float(np.average(body,weights=n[pos]))
        self.pooled_excess=float(np.average(np.maximum(sev-self.threshold,0),weights=n[pos]))
        self.severity_weight=float(n[pos].sum()/(n[pos].sum()+self.cfg["severity_credibility_prior_claims"]))
        self.sev=LossModel("helper",kind="tree",leaves=7,seed=self.cfg["seed"])._tree("gamma") if kind=="tree" else GammaRegressor(alpha=.1,max_iter=600,tol=1e-7)
        self.sev.fit(z[pos],body,sample_weight=n[pos])
        return self

    def components(self,d):
        from engine import prepare
        if self.name=="Constant":
            return {"frequency":np.full(len(d),self.pool_frequency),"severity":np.full(len(d),self.pool_severity),"pure_premium":np.full(len(d),self.pool_loss)}
        if hasattr(self,"base"):return self.base.components(prepare(d))
        z=self.pre.transform(prepare(d)); f=np.maximum(self.freq.predict(z),1e-9)
        if self.name.startswith("Frequency_"):s=np.full(len(d),self.pool_severity)
        else:s=self.severity_weight*self.sev.predict(z)+(1-self.severity_weight)*self.pooled_body+self.pooled_excess
        return {"frequency":f,"severity":s,"pure_premium":np.maximum(f*s,1e-9)}

    def predict(self,d):return self.components(d)["pure_premium"]


def calibrate(y,p,mode,prior_sd=.2):
    """Log-level reliability shrinkage; prior is an assumption, not a fitted industry fact.

    Delta-method standard error uses sum of squared independent policy residuals.
    It is only a local uncertainty approximation under rare heavy losses.
    """
    y,p=np.asarray(y,dtype=float),np.asarray(p,dtype=float)
    if len(y)!=len(p) or not np.isfinite(y).all() or not np.isfinite(p).all() or (y<0).any() or (p<=0).any() or not np.isfinite(prior_sd) or prior_sd<=0:
        raise ValueError("Invalid calibration inputs")
    if y.sum()<=0:raise ValueError("Positive calibration loss is required")
    ratio=float(y.sum()/p.sum())
    log_variance=float(np.sum((y-p)**2)/y.sum()**2)
    reliability=float(prior_sd**2/(prior_sd**2+log_variance))
    if mode=="none":factor=1.
    elif mode=="full":factor=ratio
    elif mode=="credibility":factor=float(np.exp(reliability*np.log(ratio)))
    else:raise ValueError("Unknown calibration mode")
    return {"factor":factor,"raw_ratio":ratio,"log_ratio_variance_approximation":log_variance,
            "reliability":reliability,"prior_sd":prior_sd,"mode":mode}


def select_candidate(summary):
    """Heuristic one-SE rule. Correlated CV folds do not give an inferential SE."""
    table=summary.copy()
    nonconstant=table[table.model!="Constant"]
    best=nonconstant.sort_values(["mean_deviance","complexity","candidate"]).iloc[0]
    threshold=float(best.mean_deviance+best.heuristic_se)
    eligible=nonconstant[nonconstant.mean_deviance<=threshold]
    chosen=eligible.sort_values(["complexity","mean_deviance","candidate"]).iloc[0]
    baseline=table[table.model=="Constant"].sort_values("mean_deviance").iloc[0]
    return {"technical_candidate":str(chosen.candidate),"best_mean_candidate":str(best.candidate),
            "constant_benchmark_candidate":str(baseline.candidate),"one_se_threshold":threshold,
            "selected_mean_deviance":float(chosen.mean_deviance),"benchmark_mean_deviance":float(baseline.mean_deviance),
            "rule":"Simplest nonconstant candidate within a heuristic one-SE of the best nonconstant mean. Calibration strategy compared on the same outer folds. Constant retained as a mandatory governance comparator.",
            "caveat":"Repeated CV folds overlap. SE is a complexity-selection heuristic, not a valid independent-fold confidence interval. Development CV after earlier exploration is not a fresh prospective test."}
