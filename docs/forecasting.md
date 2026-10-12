# XGBoost consumption forecasting

The application trains a separate CPU XGBoost model for each unit using its stored meter readings. Model JSON, training metadata, evaluation results, jobs, and forecasts are stored in PostgreSQL, so replacing Render's filesystem does not discard them. Forecasting reads consumption history and projects costs; it does not alter billing, balances, or electrical state.

## Train from actual readings

1. Review and deploy an approved release using the [release checklist](release-readiness.md). The entrypoint applies migrations and starts one forecasting worker alongside Gunicorn; no simulator or demo training starts.
2. Register a physical unit, configure its actual electricity rate, provision the meter token, and collect at least 720 covered hourly observations for that meter.
3. Sign in as an administrator, select the unit, open **Forecasting**, and click **Train XGBoost**. The button queues a background job; ten-second dashboard polling reports progress. Existing models stay available while retraining runs.
4. Inspect hourly predictions, held-out metrics, persistence/seasonal baselines, and observed-versus-predicted charts. Tenants can view only their assigned unit's results and cannot request training.

Every model and forecast retains its reading source. Previous synthetic models and readings are retained outside the real workspace, never converted into hardware models. They do not establish accuracy on occupied dormitory units. Archived real units are excluded from training/forecasting until explicitly restored. Startup does not restore archived units or reset balances.

## Hourly target and coverage

The target is incremental hourly kWh, not the cumulative meter counter. Preprocessing reads at most 45 days of ordered, source-matched measurements for that unit. It distributes each accepted counter interval uniformly over elapsed time. That is an allocation assumption, not a recovered within-interval load curve.

An interval contributes to an hour only when its ending measurement was observed by that hour's end. Cross-boundary intervals are excluded from earlier buckets to avoid using future information. An hourly target needs at least 95% observed coverage. Only fully closed hours are eligible. Intervals over 90 minutes, counter decreases, epoch changes, and missing observations are excluded. Live simulator pauses over two minutes are also excluded; they cannot create artificial zero-use hours. Values outside 0–25 kWh per hour are rejected, consistent with the meter API's maximum power range.

Hardware needs **720 covered hourly observations**, corresponding to thirty fully covered days. These observations may span a longer window when gaps occur. Training also needs 120 eligible samples after a 24-hour feature warmup. These are software minimums, not research claims that thirty days cover all occupancy patterns. A failed job reports available covered hours; the application does not manufacture data to meet the minimum. Isolated regression fixtures retain a separate 168-hour synthetic gate; it is not used by normal real-mode startup.

## Features and evaluation

Features include Asia/Manila hour sine/cosine, weekday/weekend, 1/2/24-hour consumption lags, past 6/24-hour averages, and observed-hour counts. A six-hour average needs four observations; a 24-hour average needs twenty. Missing values remain missing for XGBoost's native handling. Each sample requires the preceding hour and at least four of the previous six hours. Future targets never become that sample's features.

Eligible samples are split chronologically: 60% training, 20% validation, and 20% held-out test. Two tree depths (2 and 4) are compared using validation MAE, with up to 160 boosting rounds and validation-only early stopping. The selected model is evaluated once on the test segment and saved without refitting on that test segment. Training uses one CPU thread and a fixed random seed. Persisted metadata records the feature schema, source, coverage, split boundaries/counts, selected parameters, and XGBoost version.

The dashboard reports measured test MAE and RMSE in kWh and MAPE for nonzero actuals. It reports excluded zero targets; MAPE is unavailable if all actual targets are zero. Very small nonzero targets can make MAPE unstable. Persistence predicts the previous hour; the seasonal baseline predicts the same hour one day earlier. Seasonal metrics use only targets with that observation available and display their sample count. Charts retain the last 72 held-out points, while metrics cover the entire test segment.

The first report is **next-hour chronological evaluation**: earlier test observations can become later inputs. A separate fixed-model rolling-origin backtest evaluates **1/6/12/24-hour** endpoint and cumulative kWh errors using the same recursive inference as issuance. Each origin uses only preceding observations; subsequent lags use predictions. Future actuals are read only for scoring. The selected model is not refit or selected on those scores. Up to eight held-out origins, at least 24 hours apart, are evaluated. Origins without 24 consecutive targets or the preceding observation are excluded, with explicit counts/status. This finite coverage is not a calibrated uncertainty model.

Every horizon retains MAE/RMSE/MAPE and sample/zero-exclusion counts for endpoints and cumulative consumption. Persistence uses the last observed hour; seasonal uses the corresponding previous-day observations and reports its own matched sample count. The dashboard summarizes cumulative baseline MAE alongside model endpoint/cumulative MAE; the API retains the full metrics. Older model JSON without this additive report remains readable and requests retraining for multi-hour evaluation. No confidence intervals or predictive accuracy claim is added. An isolated occupancy-change fixture actually performs worse than the simple baselines; its evidence is retained in [the validation report](software-validation.md).

Retraining is due after `FORECAST_RETRAIN_DAYS` (default 7). At `FORECAST_MAX_MODEL_DAYS` (default 30), new issuance stops and the API returns `stale_model` with historical evaluation but no projections/credit duration. Settings must satisfy `1 <= retrain <= maximum <= 365`. These are operational policy limits, not empirical drift detection. An administrator requests retraining after checking covered fresh data and changes in occupancy; the software does not silently auto-retrain or claim the model remains accurate until the maximum age.

## Predictions and estimated credit duration

The worker checks for queued jobs every ten seconds and generates one forecast series per model and UTC issue hour when recent coverage is sufficient. It requires the last closed hour plus four covered hours in the previous six. Predictions are nonnegative and checked against the supported consumption range. The saved series contains the remaining current partial hour when needed and the next 24 full hours. Later predictions use earlier predictions recursively.

The API displays only future full-hour points still within that series, so its remaining horizon decreases until a new series is issued. A trained model without usable recent data reports **waiting for readings**. An expired series is not presented as a current forecast. Saved historical evaluation remains available.

Credit duration accumulates projected costs using the unit's current balance and applicable scheduled electricity rates. It assumes uniform consumption within each predicted hour, prorates the remaining current hour, and splits hours at rate changes. It returns an estimate only if credit would exhaust within the available continuous forecast. Missing coverage/rates, zero projected use, or credit lasting beyond that horizon returns **Cannot yet be determined reliably**. It never extrapolates beyond the series or promises a physical shutoff time.

## Trusted-terminal commands

Use the same activated virtual environment and database configuration as the application:

```bash
# Direct training and evaluation for one active unit:
flask --app wsgi train-forecast --unit-id 1

# Process queued jobs and refresh predictions continuously:
flask --app wsgi forecast-worker --interval 10

# One worker cycle, useful for diagnosis:
flask --app wsgi forecast-worker --once
```

The Render/cloud startup helpers already run one worker; avoid starting another unnecessarily. Duplicate pending/running requests for a unit reuse the existing job. Jobs left running for over fifteen minutes are marked interrupted so an administrator can retry them. Check `.dormenergy-cloud/forecaster.log` locally or the combined Render service logs if a job stalls.

## Move to real measurements

Register separate hardware units and provision their scoped device tokens as described in [the API contract](api.md). Collect genuine timestamped measurements, use `SIMULATION_MODE=false`, and run the forecasting worker without a simulator. Existing simulation artifacts remain labeled simulation and are not converted into hardware models. New synthetic training and prediction generation require simulation mode.

Define data collection conditions, covered periods, train/validation/test windows, baselines, multi-hour backtests, and acceptance criteria before conducting real trials. Record the actual metrics even when XGBoost performs worse than a baseline. Thirty covered days merely satisfy this prototype's hardware training gate.
