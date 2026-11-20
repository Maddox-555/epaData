"""Leakage-aware next-year CO2 prediction using annual CAMPD records."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from data_service import _records_to_dataframe
from models import AnnualRecord, Facility, Unit


PREDICTION_TARGET = "next_year_co2_mass"
NUMERIC_FEATURES = [
    "co2_mass", "so2_mass", "nox_mass", "gross_load", "heat_input",
    "operating_time", "reporting_year",
]
CATEGORICAL_FEATURES = [
    "facility_id", "state", "primary_fuel", "unit_type", "so2_control_information",
    "nox_control_information", "pm_control_information", "program_code",
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
SENSITIVITY_FEATURES = {
    "co2_mass", "so2_mass", "nox_mass", "gross_load", "heat_input", "operating_time",
}


def _metric(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), 6)


def _prediction_metrics(actual: pd.Series, predicted: Any) -> dict[str, float | None]:
    actual_values = actual.to_numpy(dtype=float)
    predicted_values = predicted.astype(float)
    nonzero = actual_values != 0
    return {
        "mae": _metric(mean_absolute_error(actual_values, predicted_values)),
        "rmse": _metric(math.sqrt(mean_squared_error(actual_values, predicted_values))),
        "r2": _metric(r2_score(actual_values, predicted_values)) if len(actual_values) > 1 else None,
        "mape": _metric(
            (abs((actual_values[nonzero] - predicted_values[nonzero]) / actual_values[nonzero]).mean() * 100)
        ) if nonzero.any() else None,
    }


def _annual_rows(session: Session) -> pd.DataFrame:
    query = (
        select(AnnualRecord, Unit, Facility)
        .select_from(AnnualRecord)
        .join(AnnualRecord.unit)
        .join(Unit.facility)
        .order_by(AnnualRecord.reporting_year, AnnualRecord.annual_record_id)
    )
    rows = session.execute(query).all()
    frame = _records_to_dataframe(rows)
    if frame.empty:
        return frame
    # Multiple imported snapshots can describe the same unit/year. Keep the
    # latest row returned by the annual-record ordering for one time series.
    return frame.drop_duplicates(["facility_id", "unit_id", "reporting_year"], keep="last").copy()


def _build_prediction_frame(session: Session, state: str | None = None) -> pd.DataFrame:
    frame = _annual_rows(session)
    if frame.empty:
        return frame
    if state:
        frame = frame[frame["state"].fillna("").str.upper() == state.upper()]
    frame["target_year"] = frame["reporting_year"] + 1
    targets = frame[["unit_id", "reporting_year", "co2_mass"]].rename(
        columns={"reporting_year": "target_year", "co2_mass": PREDICTION_TARGET}
    )
    result = frame.merge(targets, on=["unit_id", "target_year"], how="inner", suffixes=("", "_target"))
    return result[result[PREDICTION_TARGET].notna()].copy()


def _preprocessor() -> ColumnTransformer:
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("one_hot", OneHotEncoder(handle_unknown="ignore")),
    ])
    return ColumnTransformer([
        ("numeric", numeric, NUMERIC_FEATURES),
        ("categorical", categorical, CATEGORICAL_FEATURES),
    ])


def _models() -> dict[str, Pipeline]:
    def pipeline(model: Any) -> Pipeline:
        return Pipeline([("preprocess", _preprocessor()), ("model", model)])

    return {
        "mean_baseline": pipeline(DummyRegressor(strategy="mean")),
        "ridge": pipeline(Ridge(alpha=1.0)),
        "random_forest": pipeline(RandomForestRegressor(
            n_estimators=120, max_depth=14, min_samples_leaf=2,
            random_state=42, n_jobs=-1,
        )),
    }


def _feature_importance(model: Pipeline) -> list[dict[str, float | str]]:
    estimator = model.named_steps["model"]
    preprocessor = model.named_steps["preprocess"]
    names = preprocessor.get_feature_names_out()
    if hasattr(estimator, "feature_importances_"):
        values = estimator.feature_importances_
    elif hasattr(estimator, "coef_"):
        values = abs(estimator.coef_)
        if values.ndim > 1:
            values = values[0]
    else:
        values = [0.0] * len(names)

    importance: dict[str, float] = {feature: 0.0 for feature in FEATURES}
    for transformed_name, value in zip(names, values):
        source_name = transformed_name.split("__", 1)[-1]
        source_feature = next(
            (feature for feature in FEATURES if source_name == feature or source_name.startswith(f"{feature}_")),
            source_name,
        )
        if source_feature in importance:
            importance[source_feature] += float(value)
    return [
        {"feature": feature, "importance": _metric(value) or 0.0}
        for feature, value in sorted(importance.items(), key=lambda item: item[1], reverse=True)
    ]


def _prediction_training(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Pipeline], pd.DataFrame, dict[str, pd.Series]]:
    years = sorted(int(year) for year in frame["target_year"].unique())
    if len(years) < 3:
        raise ValueError("At least three target years are required for chronological train, validation, and test splits.")
    train_end, validation_year, test_year = years[-3], years[-2], years[-1]
    split_masks = {
        "train": frame["target_year"] <= train_end,
        "validation": frame["target_year"] == validation_year,
        "test": frame["target_year"] == test_year,
    }
    if any(int(mask.sum()) == 0 for mask in split_masks.values()):
        raise ValueError("Each chronological split must contain at least one prediction row.")
    x_train = frame.loc[split_masks["train"], FEATURES]
    y_train = frame.loc[split_masks["train"], PREDICTION_TARGET]
    x_validation = frame.loc[split_masks["validation"], FEATURES]
    y_validation = frame.loc[split_masks["validation"], PREDICTION_TARGET]
    x_test = frame.loc[split_masks["test"], FEATURES]
    y_test = frame.loc[split_masks["test"], PREDICTION_TARGET]
    results: dict[str, Any] = {}
    models: dict[str, Pipeline] = {}
    predictions: dict[str, pd.Series] = {}
    for name, model in _models().items():
        model.fit(x_train, y_train)
        models[name] = model
        results[name] = {
            "validation": _prediction_metrics(y_validation, model.predict(x_validation)),
            "test": _prediction_metrics(y_test, model.predict(x_test)),
        }
        prediction_mask = split_masks["validation"] | split_masks["test"]
        predictions[name] = pd.Series(
            model.predict(frame.loc[prediction_mask, FEATURES]),
            index=frame.index[prediction_mask],
        )
    return results, models, frame.loc[split_masks["test"]].copy(), predictions


def _selected_prediction_model(results: dict[str, Any]) -> str:
    return min(results, key=lambda name: results[name]["validation"]["mae"] or float("inf"))


def _baseline_row(frame: pd.DataFrame, facility_id: str | None, unit_id: str | None) -> pd.Series:
    candidates = frame
    if facility_id:
        candidates = candidates[candidates["facility_id"].astype(str) == str(facility_id)]
    if unit_id:
        candidates = candidates[candidates["unit_id"].astype(str) == str(unit_id)]
    if candidates.empty:
        raise ValueError("No final-test unit matched the requested facility or unit.")
    return candidates.sort_values(["target_year", "unit_id"]).iloc[0]


def _scenario_result(model: Pipeline, row: pd.Series, changes: dict[str, Any], baseline_prediction: float) -> dict[str, Any]:
    scenario = row[FEATURES].copy()
    for feature, value in changes.items():
        if feature not in FEATURES:
            raise ValueError(f"Unsupported scenario feature '{feature}'.")
        if isinstance(value, dict) and "percent" in value:
            value = float(row[feature]) * (1 + float(value["percent"]) / 100)
        scenario[feature] = value
    prediction = float(model.predict(pd.DataFrame([scenario.to_dict()]))[0])
    absolute_change = prediction - baseline_prediction
    return {
        "prediction": _metric(prediction),
        "absolute_change": _metric(absolute_change),
        "percentage_change": _metric((absolute_change / baseline_prediction) * 100) if baseline_prediction else None,
    }


def run_sensitivity(
    session: Session,
    *,
    state: str | None = None,
    facility_id: str | None = None,
    unit_id: str | None = None,
    feature: str = "gross_load",
    minimum: float | None = None,
    maximum: float | None = None,
    steps: int = 11,
    model_name: str | None = None,
) -> dict[str, Any]:
    if feature not in SENSITIVITY_FEATURES:
        raise ValueError(f"feature must be one of: {', '.join(sorted(SENSITIVITY_FEATURES))}")
    if steps < 2 or steps > 51:
        raise ValueError("steps must be between 2 and 51.")
    if minimum is not None and not math.isfinite(minimum):
        raise ValueError("minimum must be finite.")
    if maximum is not None and not math.isfinite(maximum):
        raise ValueError("maximum must be finite.")
    if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
        raise ValueError("Sensitivity input values must be non-negative physical values, not percentages.")
    frame = _build_prediction_frame(session, state)
    if frame.empty:
        raise ValueError("No consecutive annual records are available for sensitivity analysis.")
    results, models, test_frame, _ = _prediction_training(frame)
    selected_model = model_name or _selected_prediction_model(results)
    if selected_model not in models:
        raise ValueError("model must be mean_baseline, ridge, or random_forest.")
    model = models[selected_model]
    baseline = _baseline_row(test_frame, facility_id, unit_id)
    baseline_value = float(baseline[feature])
    if not math.isfinite(baseline_value):
        raise ValueError(f"The selected unit has no usable baseline value for '{feature}'.")
    if minimum is None:
        minimum = baseline_value * 0.8
    if maximum is None:
        maximum = baseline_value * 1.2
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum.")
    training_values = pd.to_numeric(frame.loc[frame["target_year"] < baseline["target_year"], feature], errors="coerce").dropna()
    training_min = float(training_values.min()) if not training_values.empty else None
    training_max = float(training_values.max()) if not training_values.empty else None
    baseline_prediction = float(model.predict(pd.DataFrame([baseline[FEATURES].to_dict()]))[0])
    values = [minimum + (maximum - minimum) * index / (steps - 1) for index in range(steps)]
    points = []
    for value in values:
        result = _scenario_result(model, baseline, {feature: value}, baseline_prediction)
        points.append({
            "input_value": _metric(value),
            **result,
            "outside_training_range": bool(
                training_min is not None and (value < training_min or value > training_max)
            ),
        })
    return {
        "model": selected_model,
        "feature": feature,
        "baseline": {
            "facility_id": str(baseline["facility_id"]),
            "unit_id": str(baseline["unit_id"]),
            "reporting_year": int(baseline["target_year"]),
            "input_value": _metric(baseline_value),
            "prediction": _metric(baseline_prediction),
        },
        "training_range": {"minimum": _metric(training_min), "maximum": _metric(training_max)},
        "points": points,
        "warning": "Some sensitivity inputs are outside the training-data range." if any(point["outside_training_range"] for point in points) else None,
    }


def run_scenarios(
    session: Session,
    *,
    state: str | None = None,
    facility_id: str | None = None,
    unit_id: str | None = None,
    scenarios: list[dict[str, Any]] | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    if scenarios is not None and not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list.")
    frame = _build_prediction_frame(session, state)
    if frame.empty:
        raise ValueError("No consecutive annual records are available for scenario analysis.")
    results, models, test_frame, _ = _prediction_training(frame)
    selected_model = model_name or _selected_prediction_model(results)
    if selected_model not in models:
        raise ValueError("model must be mean_baseline, ridge, or random_forest.")
    model = models[selected_model]
    baseline = _baseline_row(test_frame, facility_id, unit_id)
    baseline_prediction = float(model.predict(pd.DataFrame([baseline[FEATURES].to_dict()]))[0])
    training_ranges: dict[str, dict[str, float | None]] = {}
    for feature in NUMERIC_FEATURES:
        values = pd.to_numeric(frame.loc[frame["target_year"] < baseline["target_year"], feature], errors="coerce").dropna()
        training_ranges[feature] = {
            "minimum": _metric(float(values.min())) if not values.empty else None,
            "maximum": _metric(float(values.max())) if not values.empty else None,
        }
    scenario_rows = [{"name": "Baseline", "changes": {}, "outside_training_range": False, **_scenario_result(model, baseline, {}, baseline_prediction)}]
    out_of_range = False
    for scenario in scenarios or []:
        if not isinstance(scenario, dict):
            raise ValueError("Each scenario must be an object.")
        name = str(scenario.get("name", "")).strip()
        changes = scenario.get("changes")
        if not name or not isinstance(changes, dict):
            raise ValueError("Each scenario must include a name and a changes object.")
        outside = False
        for feature, value in changes.items():
            if feature not in FEATURES:
                raise ValueError(f"Unsupported scenario feature '{feature}'.")
            resolved_value = value
            if isinstance(value, dict) and "percent" in value:
                resolved_value = float(baseline[feature]) * (1 + float(value["percent"]) / 100)
            if feature in training_ranges and training_ranges[feature]["minimum"] is not None:
                outside = outside or not (
                    training_ranges[feature]["minimum"] <= float(resolved_value) <= training_ranges[feature]["maximum"]
                )
        out_of_range = out_of_range or outside
        scenario_rows.append({
            "name": name, "changes": changes, "outside_training_range": outside,
            **_scenario_result(model, baseline, changes, baseline_prediction),
        })
    return {
        "model": selected_model,
        "baseline": {
            "facility_id": str(baseline["facility_id"]),
            "unit_id": str(baseline["unit_id"]),
            "reporting_year": int(baseline["target_year"]),
            "prediction": _metric(baseline_prediction),
        },
        "scenarios": scenario_rows,
        "warning": "Some scenario inputs are outside the training-data range." if out_of_range else None,
        "training_ranges": training_ranges,
    }


def run_prediction(session: Session, *, state: str | None = None) -> dict[str, Any]:
    frame = _build_prediction_frame(session, state)
    if frame.empty:
        raise ValueError("No consecutive annual records are available for next-year prediction.")
    years = sorted(int(year) for year in frame["target_year"].unique())
    if len(years) < 3:
        raise ValueError("At least three target years are required for chronological train, validation, and test splits.")
    train_end, validation_year, test_year = years[-3], years[-2], years[-1]
    split_masks = {
        "train": frame["target_year"] <= train_end,
        "validation": frame["target_year"] == validation_year,
        "test": frame["target_year"] == test_year,
    }
    if any(int(mask.sum()) == 0 for mask in split_masks.values()):
        raise ValueError("Each chronological split must contain at least one prediction row.")

    x_train, y_train = frame.loc[split_masks["train"], FEATURES], frame.loc[split_masks["train"], PREDICTION_TARGET]
    x_validation, y_validation = frame.loc[split_masks["validation"], FEATURES], frame.loc[split_masks["validation"], PREDICTION_TARGET]
    x_test, y_test = frame.loc[split_masks["test"], FEATURES], frame.loc[split_masks["test"], PREDICTION_TARGET]
    results: dict[str, Any] = {}
    split_predictions: dict[str, pd.Series] = {}
    for name, model in _models().items():
        model.fit(x_train, y_train)
        split_predictions[name] = pd.Series(
            model.predict(frame.loc[split_masks["validation"] | split_masks["test"], FEATURES]),
            index=frame.index[split_masks["validation"] | split_masks["test"]],
        )
        results[name] = {
            "validation": _prediction_metrics(y_validation, model.predict(x_validation)),
            "test": _prediction_metrics(y_test, model.predict(x_test)),
            "feature_importance": _feature_importance(model),
        }
    best_model = min(results, key=lambda name: results[name]["validation"]["mae"] or float("inf"))
    trend = (
        frame.groupby("target_year", as_index=False)[PREDICTION_TARGET]
        .sum()
        .rename(columns={"target_year": "year", PREDICTION_TARGET: "actual_co2"})
    )
    trend["predicted_co2"] = None
    selected_predictions = split_predictions[best_model]
    predicted_by_year = (
        frame.loc[selected_predictions.index, ["target_year"]]
        .assign(predicted_co2=selected_predictions)
        .groupby("target_year")["predicted_co2"]
        .sum()
    )
    trend["predicted_co2"] = trend["year"].map(predicted_by_year)
    trend = [
        {
            "year": int(row.year),
            "actual_co2": _metric(row.actual_co2),
            "predicted_co2": _metric(row.predicted_co2),
        }
        for row in trend.itertuples(index=False)
    ]
    return {
        "target": PREDICTION_TARGET,
        "objective": "Predict next-year unit CO2 emissions in short tons.",
        "feature_policy": "Only the prior year's observed unit data and static characteristics are used; next-year target values are excluded from features.",
        "features": FEATURES,
        "state_filter": state.upper() if state else None,
        "splits": {
            "train": {"target_years": [min(years), train_end], "records": int(split_masks["train"].sum())},
            "validation": {"target_year": validation_year, "records": int(split_masks["validation"].sum())},
            "test": {"target_year": test_year, "records": int(split_masks["test"].sum())},
        },
        "models": results,
        "selected_model": best_model,
        "feature_importance": results[best_model]["feature_importance"],
        "co2_trend": trend,
    }
