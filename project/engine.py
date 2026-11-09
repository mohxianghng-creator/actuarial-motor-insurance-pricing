"""Shared CAS data contracts, baseline estimators and diagnostic utilities.

V2 entry point: run.py. No legacy model-selection procedure is executed here.
"""

from __future__ import annotations

import argparse

import hashlib

import importlib.metadata

import json

import platform

import sys

import warnings

from pathlib import Path

import joblib

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

import numpy as np

import pandas as pd

from scipy import optimize, stats

from sklearn.compose import ColumnTransformer

from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from sklearn.impute import SimpleImputer

from sklearn.inspection import permutation_importance

from sklearn.linear_model import GammaRegressor, PoissonRegressor, TweedieRegressor

from sklearn.metrics import mean_absolute_error, mean_poisson_deviance, mean_tweedie_deviance, roc_auc_score

from sklearn.model_selection import train_test_split

from sklearn.pipeline import Pipeline

from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, SplineTransformer, StandardScaler

FEATURES = ["Power", "CarAge", "DriverAge", "Brand", "Gas", "Region", "Density"]

NUMERIC = ["CarAge", "DriverAge", "LogDensity"]

CATEGORICAL = ["Power", "Brand", "Gas", "Region"]

EPS = 1e-9

def dump_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)), encoding="utf-8")

def prepare(data):
    """Apply deterministic transforms only; learned preprocessing is fitted on train."""
    x = data[FEATURES].copy()
    for c in ["CarAge", "DriverAge", "Density"]:
        x[c] = pd.to_numeric(x[c], errors="raise")
        if (x[c].dropna() < 0).any():
            raise ValueError(f"Negative {c}")
        if not np.isfinite(x[c].dropna()).all():
            raise ValueError(f"Nonfinite {c}")
    x["LogDensity"] = np.log1p(x.pop("Density"))
    for c in CATEGORICAL:
        x[c] = x[c].fillna("Missing").astype(str)
    return x

def validate_training(d):
    missing = set(FEATURES + ["PolicyID", "ClaimNb", "ClaimAmount"]) - set(d)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if d.PolicyID.isna().any() or d.PolicyID.duplicated().any():
        raise ValueError("Training PolicyID must be complete and unique")
    for c in ["ClaimNb", "ClaimAmount"]:
        a = d[c].to_numpy(dtype=float)
        if not np.isfinite(a).all() or (a < 0).any():
            raise ValueError(f"Invalid target {c}")
    if not np.equal(d.ClaimNb, np.floor(d.ClaimNb)).all():
        raise ValueError("ClaimNb must be integer")
    if ((d.ClaimNb == 0) & (d.ClaimAmount > 0)).any():
        raise ValueError("Positive loss with zero claim count requires reconciliation")
    if ((d.ClaimNb > 0) & (d.ClaimAmount == 0)).any():
        raise ValueError("Zero-cost claims require a separate payment probability model")
    if (d.ClaimNb > 0).sum() < 100:
        raise ValueError("Too few positive policies for this model comparison")
    prepare(d)

def load_data(path):
    book = pd.read_excel(path, sheet_name=None)
    d, q, template = [book[s] for s in ["Dataset for Student", "Pricing Data", "Pricing Submission"]]
    validate_training(d)
    prepare(q)
    if q.PolicyID.isna().any() or q.PolicyID.duplicated().any():
        raise ValueError("Quote IDs must be complete and unique within Pricing Data")
    if template.PolicyID.duplicated().any() or set(q.PolicyID) != set(template.PolicyID):
        raise ValueError("Submission and pricing IDs differ")
    return d, q, template

def preprocessor(kind):
    imp = SimpleImputer(strategy="median")
    if kind == "spline":
        num = Pipeline([("impute", imp), ("spline", SplineTransformer(n_knots=5, degree=3, include_bias=False, extrapolation="linear")), ("scale", StandardScaler())])
    elif kind == "tree":
        num = imp
    else:
        num = Pipeline([("impute", imp), ("scale", StandardScaler())])
    cat = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1) if kind == "tree" else OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    return ColumnTransformer([("num", num, NUMERIC), ("cat", cat, CATEGORICAL)], sparse_threshold=0)

class LossModel:
    """Frequency x claim-weighted average severity, or direct Tweedie loss model."""
    def __init__(self, name, kind="linear", alpha=0.1, leaves=15, direct=False, hurdle=False, tail=False, seed=1):
        self.name, self.kind, self.alpha, self.leaves = name, kind, alpha, leaves
        self.direct, self.hurdle, self.tail, self.seed = direct, hurdle, tail, seed

    def _tree(self, loss):
        return HistGradientBoostingRegressor(loss=loss, max_iter=150, learning_rate=0.05, max_leaf_nodes=self.leaves,
            min_samples_leaf=100 if loss == "poisson" else 60, l2_regularization=10.0,
            early_stopping=False, categorical_features=[3, 4, 5, 6], random_state=self.seed)

    def fit(self, x, d):
        self.pre = preprocessor(self.kind)
        z = self.pre.fit_transform(x)
        n, y = d.ClaimNb.to_numpy(), d.ClaimAmount.to_numpy()
        if self.direct:
            self.pure = TweedieRegressor(power=1.5, alpha=self.alpha, link="log", max_iter=600, tol=1e-6).fit(z, y)
            return self
        self.freq = self._tree("poisson") if self.kind == "tree" else PoissonRegressor(alpha=self.alpha, max_iter=600, tol=1e-7)
        pos = n > 0
        if self.hurdle:
            self.occ = HistGradientBoostingClassifier(max_iter=150, max_leaf_nodes=self.leaves, learning_rate=0.05,
                min_samples_leaf=100, l2_regularization=10, early_stopping=False,
                categorical_features=[3, 4, 5, 6], random_state=self.seed).fit(z, pos.astype(int))
            # Conditional positive mean is estimated directly; not called a truncated Poisson likelihood.
            self.freq.fit(z[pos], n[pos])
        else:
            self.freq.fit(z, n)
        sev = y[pos] / n[pos]
        self.tail_threshold, self.excess_per_claim = None, 0.0
        if self.tail:
            self.tail_threshold = float(np.quantile(sev, 0.99))
            self.excess_per_claim = float(np.average(np.maximum(sev - self.tail_threshold, 0), weights=n[pos]))
            sev = np.minimum(sev, self.tail_threshold)
        self.sev = self._tree("gamma") if self.kind == "tree" else GammaRegressor(alpha=self.alpha, max_iter=600, tol=1e-7)
        self.sev.fit(z[pos], sev, sample_weight=n[pos])
        return self

    def components(self, x):
        z = self.pre.transform(x)
        if self.direct:
            return {"pure_premium": np.maximum(self.pure.predict(z), EPS)}
        freq = np.maximum(self.freq.predict(z), EPS)
        if self.hurdle:
            # Positive-count means must lie in [1, infinity), including unseen risks.
            freq = np.maximum(freq, 1.0) * self.occ.predict_proba(z)[:, 1]
        sev = np.maximum(self.sev.predict(z), EPS) + self.excess_per_claim
        return {"frequency": freq, "severity": sev, "pure_premium": freq * sev}

    def predict(self, x):
        return self.components(x)["pure_premium"]

def metrics(y, p):
    y, p = np.asarray(y), np.maximum(p, EPS)
    order = np.argsort(p, kind="stable")
    # Concentration Gini uses the model order; it is not a normalized Gini.
    ordered = pd.DataFrame({"score":p,"loss":y}).groupby("score",sort=True).agg(loss=("loss","sum"),count=("loss","size"))
    cum = np.r_[0, np.cumsum(ordered.loss) / max(y.sum(), EPS)]
    gini = 1 - 2 * np.trapezoid(cum, np.r_[0,np.cumsum(ordered["count"])/len(y)])
    top = np.argsort(p)[-max(1, int(np.ceil(len(p) / 10))):]
    return {"tweedie_deviance_p1.5": float(mean_tweedie_deviance(y, p, power=1.5)),
        "mae": float(mean_absolute_error(y, p)), "rmse": float(np.sqrt(np.mean((y-p)**2))),
        "actual_over_expected": float(y.sum()/p.sum()), "mean_prediction": float(p.mean()),
        "mean_actual": float(y.mean()), "concentration_gini": float(gini),
        "top_decile_loss_lift": float(y[top].mean()/max(y.mean(), EPS)) if np.ptp(p)>EPS else 1.0}

def zero_diagnostics(counts, test_counts, freq_prediction):
    """Training-only intercept likelihood fits; held-out scoring is diagnostic only."""
    n = np.asarray(counts)
    mu = float(n.mean())
    def nb_nll(t):
        m, a = np.exp(t)
        return -stats.nbinom.logpmf(n, 1/a, 1/(1+a*m)).sum()
    nb = optimize.minimize(nb_nll, np.log([mu, 0.5]), method="L-BFGS-B", bounds=[(-12, 3), (-12, 8)])
    nm, a = np.exp(nb.x)
    def zip_logpmf(y, theta):
        pi = stats.logistic.cdf(theta[0]); lam = np.exp(theta[1])
        out = np.log1p(-pi) + stats.poisson.logpmf(y, lam)
        return np.where(y == 0, np.logaddexp(np.log(max(pi, EPS)), out), out)
    zi = optimize.minimize(lambda t: -zip_logpmf(n, t).sum(), [-3, np.log(mu*1.1)], method="L-BFGS-B", bounds=[(-18, 10), (-12, 3)])
    pi, lam = float(stats.logistic.cdf(zi.x[0])), float(np.exp(zi.x[1]))
    return {"observed_zero_fraction_train": float((n==0).mean()), "poisson_mean": mu,
        "variance_to_mean_train": float(n.var()/mu), "poisson_expected_zero": float(np.exp(-mu)),
        "NB2_mean": float(nm), "NB2_alpha": float(a), "NB2_expected_zero": float((1+a*nm)**(-1/a)),
        "ZIP_structural_zero_estimate_intercept_only": pi, "ZIP_count_mean": lam,
        "ZIP_expected_zero": float(pi+(1-pi)*np.exp(-lam)), "NB_optimizer_success": bool(nb.success), "ZIP_optimizer_success": bool(zi.success),
        "heldout_poisson_logloss": float(-stats.poisson.logpmf(test_counts, mu).mean()),
        "heldout_NB2_logloss": float(-stats.nbinom.logpmf(test_counts, 1/a, 1/(1+a*nm)).mean()),
        "heldout_ZIP_logloss": float(-zip_logpmf(np.asarray(test_counts), zi.x).mean()),
        "conditional_poisson_expected_zero_test": float(np.exp(-freq_prediction).mean()),
        "observed_zero_fraction_test": float((np.asarray(test_counts)==0).mean()),
        "interpretation": "Many zeros follow naturally from low frequency. Intercept NB/ZIP fits cannot distinguish structural zeros from omitted heterogeneity. No structural-zero claim is made."}

def bootstrap_comparison(y, champion, baseline, repetitions, seed):
    rng = np.random.default_rng(seed)
    y, champion, baseline = map(np.asarray, (y, champion, baseline))
    # Paired differences avoid treating correlated model errors as independent.
    def point_loss(p):
        return 2*(y**0.5/(-0.5*0.5) - y*p**(-0.5)/(-0.5) + p**0.5/0.5)
    delta = point_loss(champion)-point_loss(baseline)
    out = []
    for _ in range(repetitions):
        ix = rng.integers(0, len(y), len(y))
        out.append([delta[ix].mean(), y[ix].sum()/champion[ix].sum()])
    a = np.asarray(out)
    return {"paired_deviance_delta_champion_minus_baseline": float(delta.mean()),
        "delta_CI95": np.quantile(a[:,0], [.025,.975]).tolist(),
        "champion_actual_over_expected_CI95": np.quantile(a[:,1], [.025,.975]).tolist(),
        "scope": "IID held-out policy bootstrap conditional on fitted models. Excludes parameter refitting, temporal dependence and unobserved catastrophe risk."}

def tail_analysis(d, directory):
    y = d.ClaimAmount.to_numpy()
    positive = y[y>0]
    rows = []
    for q in [.95,.975,.99]:
        u = float(np.quantile(positive, q)); excess = positive[positive>u]-u
        if len(excess) < 20:
            continue
        shape, _, scale = stats.genpareto.fit(excess, floc=0)
        mean_excess = scale/(1-shape) if shape<1 else None
        rows.append({"positive_policy_quantile": q, "threshold": u, "exceedances": len(excess),
            "GPD_shape": shape, "GPD_scale": scale, "GPD_mean_excess_if_finite": mean_excess,
            "empirical_mean_excess": excess.mean(), "finite_variance_fit": bool(shape<.5),
            "note": "Policy aggregate loss tail; not individual-claim severity. Exploratory, not automatically loaded into tariff."})
    pd.DataFrame(rows).to_csv(directory/"tail_threshold_sensitivity.csv", index=False)
    return {"positive_policy_loss_quantiles": dict(zip([str(v) for v in [.5,.9,.95,.99,.995,1]], np.quantile(positive,[.5,.9,.95,.99,.995,1]).tolist())),
        "largest_loss": float(y.max()), "largest_loss_share": float(y.max()/y.sum()),
        "top_10_loss_share": float(np.sort(y)[-10:].sum()/y.sum()),
        "top_1pct_positive_loss_share": float(np.sort(positive)[-max(1,int(np.ceil(len(positive)*.01))):].sum()/y.sum()),
        "warning": "Historical observations cannot identify unprecedented black-swan probabilities. No VaR/TVaR or solvency guarantee is inferred from these tail fits."}
