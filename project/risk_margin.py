"""Conditional empirical portfolio risk-budget diagnostics, not an economic-capital model."""
import numpy as np
import pandas as pd


def empirical_money_margin(losses, portfolio_size, expected_portfolio_loss,
                           simulations=2000, cost_of_capital=.12, seed=20261105):
    """Bootstrap complete policy losses, with a global mean rescaling to quote risk.

    Sampling a binomial number of positive policies then positive losses is exactly
    an IID bootstrap from the empirical policy-loss mixture. The mean rescaling
    imposes constant relative distribution shape across populations. Historical
    support, independence and fixed fitted parameters are strong assumptions.
    """
    losses=np.asarray(losses,dtype=float)
    if not np.isfinite(losses).all() or (losses<0).any() or losses.mean()<=0:
        raise ValueError("Nonnegative finite losses with a positive mean required")
    if portfolio_size<=0 or int(portfolio_size)!=portfolio_size or simulations<100:
        raise ValueError("Positive integer portfolio size and at least 100 simulations required")
    if expected_portfolio_loss<=0 or not 0<=cost_of_capital<=1:
        raise ValueError("Invalid expected loss or cost of capital")
    n=int(portfolio_size)
    pos=losses[losses>0]
    scale=expected_portfolio_loss/(n*losses.mean())
    rng=np.random.default_rng(seed)
    positive_counts=rng.binomial(n,len(pos)/len(losses),size=simulations)
    sums=np.array([rng.choice(pos,size=k,replace=True).sum()*scale for k in positive_counts])
    rows=[]
    for q in [.90,.95,.99]:
        percentile=float(np.quantile(sums,q))
        tail_mean=float(sums[sums>=percentile].mean())
        capital=max(0,percentile-expected_portfolio_loss)
        rows.append({"quantile":q,"portfolio_policies":n,"simulations":simulations,
            "expected_loss_target":expected_portfolio_loss,"simulated_mean_loss":sums.mean(),
            "empirical_aggregate_loss_quantile":percentile,"empirical_tail_mean":tail_mean,
            "indicative_capital_buffer":capital,"buffer_money_per_policy":capital/n,
            "assumed_cost_of_capital":cost_of_capital,
            "indicative_CoC_money_margin_per_policy":cost_of_capital*capital/n,
            "status":"CONDITIONAL_IID_EMPIRICAL_DIAGNOSTIC_NOT_APPROVED_MARGIN"})
    return pd.DataFrame(rows)
