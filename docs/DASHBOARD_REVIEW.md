# Dashboard review

## Changes

- Empty cohorts show unavailable averages and ratios, an explanation and a Clear filters action. Empty stress and trade-off views no longer imply a 0% combined ratio.
- Filter options show counts conditional on the other filters. Zero-count choices remain selectable so the user can see and resolve an empty combination.
- Small quote cohorts carry a descriptive warning. Empty individual risk bands display unavailable means and ratios; their counts remain zero.
- The interface distinguishes live calculations from fixed model evidence, drift, risk-budget and saved robustness tables.
- Empty PolicyID searches clear the previous policy title and disable CSV export.
- Invalid assumptions hide old calculated results and disable export/save until corrected. Loading a valid scenario can recover from invalid current inputs.
- Imports check numeric types, identities, supported control bounds and filter values. Rejected files preserve the existing controls.
- Zero expected capacity gives zero expected volume and contribution; combined ratio is unavailable because no premium is accepted.
- Expected policy volume uses a decimal place. Market calculations use the supplied current cost assumptions instead of relying on a potentially stale row calculation.
- Demo generation explicitly reads the template as UTF-8.

## Verification

The generated-data page and the local real-data revision were checked in offline Chrome. Checks cover empty and small cohorts, no-search results, filters, invalid inputs, import recovery/rejection, zero capacity, chart rendering, CSV rows and mobile overflow.

The real-data revision retains the original embedded records, model scores and evidence. Its 60,000 quote-field comparisons and 648 additional Python reference comparisons passed within numerical tolerances. The generated page passed 3,600 quote-field comparisons. This is an interface and calculation review; it is not a new predictive model validation.

## Running the browser checks

From `project/`, install the pinned Node development dependency and run:

```sh
npm install
npm run test:dashboard
```

The test needs a Chrome/Chromium installation. Set `CHROME_EXECUTABLE` if needed; on non-Windows platforms Playwright can use its installed Chromium. The generated-data demo is the default target. Scratch downloads and screenshots use `.browser_work/` or `PRICING_VERIFY_WORK`.

A permitted real-data dashboard can be supplied through `REAL_DASHBOARD_PATH`. Independent local financial reference cases can be supplied through `FINANCIAL_CASES`; those records are not distributed with this source selection.
