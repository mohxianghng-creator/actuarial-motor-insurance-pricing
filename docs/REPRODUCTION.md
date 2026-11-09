# Reproduction

## Source-only checks

`project/config.json` and `project/r_version/config.R` use relative example data paths and retain the study assumptions. The Python synthetic tests do not need the source workbook. The R generic test uses generated data and creates its results directory locally.

## Exact empirical reproduction

Restore authorized inputs from the complete local delivery to the same relative locations:

- The original workbook; set `data_path` in `project/config.json` to its location.
- `project/provenance/frozen_split_manifest.csv` and `legacy_v1_summary.json`.
- For the native R study: the exported `project/r_version/data/` files and input checksums.
- For frozen R scoring: `project/r_version/portable_model/`.

These inputs are deliberately excluded here. They include data-derived records or contracts. Do not regenerate historical partitions and describe them as the original validation.

From `project/`, the Python command is:

```sh
python run.py --data "path/to/2026 CAS Asia Summer Program Dataset.xlsx"
```

This build performs the development comparison and generates outputs under `results/`. It can take substantial time. Do not use the historical test outcomes to tune this iteration.

R's `run.R` scores the restored frozen contract. `run.R --research` also runs the separately labeled native research models. The native GBM is not an exact refit of Python's histogram boosting.

After restoring or generating the corresponding Python and R outputs, `cross_format/prepare_portable.py` exports the selected GLM contract. `cross_format/build_excel.py` builds the workbook and `cross_format/verify_excel.py` evaluates the supported formula subset. The builder expects the selected model and result schemas from this study; it is not a universal spreadsheet exporter.

## File layout

Keep the internal `project/` layout when restoring inputs. `cross_format/format_paths.py` uses a local scratch directory or the `MOTOR_FORMAT_WORK` environment variable. Generated outputs and data are ignored by Git.

The complete local delivery retains the original evidence, checksums and results. The public selection contains source and tests; it does not substitute new validation evidence for the original run.
