# Phase 4 forecasting plan — not yet implemented

The Phase 1 schema includes `forecasts` with unit, future hour, prediction, model version, source, and generation timestamp. The dashboard currently shows insufficient historical data. No XGBoost model has been trained or evaluated.

A later forecasting service will read **incremental kWh**, not cumulative kWh as the prediction target. Build per-unit hourly buckets using UTC storage and explicitly defined local time features. Track coverage, reject invalid samples, keep simulated and hardware data separate, and avoid treating communication gaps as zero usage. Reconstruct cumulative-counter intervals with a documented allocation assumption; never interpolate using future observations across a train/test boundary.

Use time-of-day, day-of-week, lagged kWh, and past-only rolling features. Split observations chronologically into training, validation, and held-out testing **before** fitting preprocessing. Tune only on the validation segment. Compare against persistence and seasonal baselines. Require sufficient complete history for the selected lag window and representative held-out days; do not choose an arbitrary accuracy threshold without adviser agreement.

Train `XGBRegressor` using `requirements-ml.txt`. Save the model and its training window, feature schema, unit/source, data coverage, and real evaluation outcomes. Report MAE and RMSE in kWh. Calculate MAPE only for nonzero actual values and report how many zero observations were excluded; also consider a separately specified aggregate metric for near-zero consumption. Never fabricate values.

Store future hourly predictions with unique unit/hour/model identifiers. Display actual-versus-predicted series only when those predictions exist. Mark any demonstration model trained on an example dataset as a **synthetic demonstration**, not a validated real-device forecast.

For duration, accumulate each predicted hour's projected cost at its applicable scheduled rate until it reaches the prepaid balance. Indicate an estimate, forecast horizon, and model/source. If no trained forecast exists, projected costs are zero, or the horizon cannot cover exhaustion, return “Cannot yet be determined reliably.” Do not extrapolate beyond the forecast horizon or guarantee an expiration time.
