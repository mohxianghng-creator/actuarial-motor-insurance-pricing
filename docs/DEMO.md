# Dashboard demo

Open `demo/DASHBOARD.html` in Chrome or Edge. Double-click the file from your file manager, or use `index.html` at the repository root. GitHub source previews do not execute JavaScript; download the repository or serve it as a static site to use the controls.

The demo embeds 600 generated quote risks. No original CAS records, fitted parameters or empirical results are included. Its lightweight model comparison uses four structures, three calibration modes and three grouped outer folds. These synthetic results are separate from the real-data study.

Region, Fuel, Brand and Driver age filter the quote cohort. They change the portfolio summaries, quote distribution and scenario calculations. They do not change the risk characteristics of an individual policy. Partial dependence and model validation describe the fitted synthetic model; they remain full-development evidence when cohort filters change.

Use Money margins to change expense and monetary-allowance rules, Business scenarios for competitor assumptions and Quote explorer for an individual financial trace. Save/load scenarios and CSV export work locally. All graphs use inline SVG and need no CDN or internet connection.

Rebuild from the repository root:

```sh
python project/build_demo.py
```

The seed is fixed at 31827. Dependencies use the main Python lock file. The build reads the relative example configuration and generates its own data. It does not read the original workbook. Model settings or library changes can alter generated results.

`project/dashboard_template.html` remains the template used by the research report builder. Opening it without data now shows a link to this runnable demo instead of a nonfunctional dashboard.

See [Dashboard review](DASHBOARD_REVIEW.md) for empty-cohort handling, input checks and repeatable browser tests. Clear filters preserves financial assumptions; Reset all inputs also restores pricing, market, search and evidence selectors.
