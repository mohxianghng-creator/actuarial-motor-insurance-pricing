# Calculation checks

The complete local delivery records:

- Deterministic frozen-model scoring for 10,000 risks in Python and R, checked against Excel formulas.
- Agreement on 216 deterministic commercial scenarios within numerical tolerances.
- Native R refits on the same development fold assignments, with implementation differences documented.
- Offline dashboard controls and calculation checks.

Full workbook formula evaluation used a scoped formula evaluator and Python/R reference outputs. A separate spreadsheet engine checked a sample of 50 risks and a calculator case. Microsoft Excel COM recalculation was unavailable. This is not a claim that every desktop spreadsheet application has been tested.

The real-data inputs and generated verification files remain in the complete local delivery. This source selection reruns the available synthetic Python and generic R checks. `SOURCE_SELECTION_CHECKS.json` records the local results. Empirical tests skip when required results are absent.

Numerical agreement verifies implementation. It does not validate demand assumptions, prove a margin is fair, demonstrate production readiness or establish future loss-ratio performance.

The dashboard interface received an additional boundary-case review. See [Dashboard review](DASHBOARD_REVIEW.md) for its scope and checks. No empirical models were refitted for this revision.
