"""Bonus: exposure-aware, configurable motor frequency/severity engine.

This module is separate from the CAS empirical results. It is contract-tested on
synthetic data, not validated against another real portfolio. Inputs must already
represent a consistent coverage, valuation and monetary basis.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler, SplineTransformer
from sklearn.linear_model import PoissonRegressor, GammaRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from pricing import quote_prices as fixed_margin_price


@dataclass
class MotorSchema:
    numeric_features: list[str]
    categorical_features: list[str]
    policy_id: str = "policy_id"
    exposure: str = "exposure"
    claim_count: str = "claim_count"
    aggregate_loss: str = "aggregate_loss"

    def __post_init__(self):
        features=self.numeric_features+self.categorical_features
        targets=[self.policy_id,self.exposure,self.claim_count,self.aggregate_loss]
        if not features or len(set(features))!=len(features) or set(features).intersection(targets):
            raise ValueError("Explicit distinct risk features must exclude identifiers, exposure and targets")


class GenericMotorEngine:
    def __init__(self, schema:MotorSchema, family="glm", alpha=.1, seed=20261105):
        if family not in ["glm","gam","gbm"]: raise ValueError("family must be glm/gam/gbm")
        self.schema,self.family,self.alpha,self.seed=schema,family,alpha,seed
        self.calibration_factor=1.0
        self.calibrated=False

    def _extract(self, frame, targets=False):
        s=self.schema
        cols=s.numeric_features+s.categorical_features
        required=cols+[s.exposure,s.policy_id]+([s.claim_count,s.aggregate_loss] if targets else [])
        if set(required)-set(frame): raise ValueError("Missing schema columns")
        if frame[s.policy_id].isna().any() or frame[s.policy_id].duplicated().any(): raise ValueError("Policy IDs must be unique and complete within the batch")
        e=frame[s.exposure].to_numpy(dtype=float)
        if not np.isfinite(e).all() or (e<=0).any(): raise ValueError("Exposure must be positive and finite")
        x=frame[cols].copy()
        for col in s.numeric_features:
            x[col]=pd.to_numeric(x[col],errors="raise")
            if not np.isfinite(x[col].dropna()).all(): raise ValueError("Nonfinite feature")
        for col in s.categorical_features: x[col]=x[col].fillna("Missing").astype(str)
        if not targets: return x,e
        n,y=frame[s.claim_count].to_numpy(dtype=float),frame[s.aggregate_loss].to_numpy(dtype=float)
        if not np.isfinite(n).all() or not np.isfinite(y).all() or (n<0).any() or (y<0).any() or (n!=np.floor(n)).any(): raise ValueError("Invalid targets")
        if ((n==0)&(y>0)).any() or ((n>0)&(y==0)).any(): raise ValueError("Reconcile zero-cost/zero-count claims before fitting")
        return x,e,n,y

    def fit(self, frame):
        x,e,n,y=self._extract(frame,True); s=self.schema
        if (n>0).sum()<30: raise ValueError("Too few positive policies")
        num=[("impute",SimpleImputer(strategy="median"))]
        if self.family=="gam": num.append(("spline",SplineTransformer(n_knots=5,include_bias=False)))
        if self.family!="gbm": num.append(("scale",StandardScaler()))
        cat=OrdinalEncoder(handle_unknown="use_encoded_value",unknown_value=-1) if self.family=="gbm" else OneHotEncoder(handle_unknown="ignore",sparse_output=False)
        blocks=[]
        if s.numeric_features: blocks.append(("num",Pipeline(num),s.numeric_features))
        if s.categorical_features: blocks.append(("cat",cat,s.categorical_features))
        self.pre=ColumnTransformer(blocks,sparse_threshold=0)
        z=self.pre.fit_transform(x)
        if self.family=="gbm":
            cat_ix=list(range(len(s.numeric_features),len(s.numeric_features)+len(s.categorical_features)))
            kw=dict(max_iter=120,max_leaf_nodes=7,min_samples_leaf=30,l2_regularization=10,early_stopping=False,categorical_features=cat_ix,random_state=self.seed)
            self.freq=HistGradientBoostingRegressor(loss="poisson",**kw)
            self.sev=HistGradientBoostingRegressor(loss="gamma",**kw)
        else:
            self.freq=PoissonRegressor(alpha=self.alpha,max_iter=600)
            self.sev=GammaRegressor(alpha=self.alpha,max_iter=600)
        # Rate target and exposure weights equal a Poisson log-exposure offset
        # in the unpenalized likelihood, up to terms independent of rate parameters.
        self.freq.fit(z,n/e,sample_weight=e)
        pos=n>0
        self.sev.fit(z[pos],y[pos]/n[pos],sample_weight=n[pos])
        self.calibration_factor=1.0; self.calibrated=False
        return self

    def predict_loss(self, frame):
        x,e=self._extract(frame)
        if not hasattr(self,"pre"): raise ValueError("Fit before scoring")
        z=self.pre.transform(x)
        return self.freq.predict(z)*self.sev.predict(z)*e*self.calibration_factor

    def calibrate(self, calibration_frame):
        self._extract(calibration_frame,True)
        # The caller must supply independent calibration rows; independence cannot
        # be inferred from IDs that may be reused across source systems.
        previous=self.calibration_factor
        uncalibrated=self.predict_loss(calibration_frame)/previous
        actual=calibration_frame[self.schema.aggregate_loss].sum()
        if actual<=0 or uncalibrated.sum()<=0: raise ValueError("Positive calibration loss required")
        self.calibration_factor=float(actual/uncalibrated.sum()); self.calibrated=True
        return self

    def quote(self, frame, pricing_config, locked_thresholds):
        out=fixed_margin_price(self.predict_loss(frame),pricing_config,locked_thresholds)
        out.insert(0,"PolicyID",frame[self.schema.policy_id].to_numpy())
        out["usage_status"]="GENERIC_TEMPLATE_REQUIRES_PORTFOLIO_VALIDATION"
        return out
