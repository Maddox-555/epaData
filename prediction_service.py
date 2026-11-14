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
    return frame.drop_duplicates(["unit_id", "reporting_year"], keep="last").copy()


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
    for name, model in _models().items():
        model.fit(x_train, y_train)
        results[name] = {
            "validation": _prediction_metrics(y_validation, model.predict(x_validation)),
            "test": _prediction_metrics(y_test, model.predict(x_test)),
        }
    best_model = min(results, key=lambda name: results[name]["validation"]["mae"] or float("inf"))
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
    }
