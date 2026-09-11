"""Data retrieval, validation, import, and query services."""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from models import (
    AnnualRecord,
    DataProvenance,
    Dataset,
    Facility,
    Unit,
    UploadedFile,
    UploadValidationError,
)


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

SOURCE_DEFINITIONS = {
    "epa-campd": {
        "name": "EPA CAMPD",
        "description": "EPA Clean Air Markets Division annual emissions and generation data.",
        "default_endpoint": "https://api.epa.gov/easey/campd/services/",
        "supported_import": True,
    },
    "noaa-ghcn-daily": {
        "name": "NOAA NCEI GHCN-Daily",
        "description": "NOAA daily weather observations for facility and weather analysis.",
        "default_endpoint": "https://www.ncei.noaa.gov/cdo-web/api/v2/data",
        "supported_import": False,
    },
    "traci": {
        "name": "TRACI",
        "description": "Versioned EPA TRACI characterization factors for impact calculations.",
        "default_endpoint": None,
        "supported_import": False,
    },
}

COLUMN_ALIASES = {
    "facility_id": ["epa facility id", "epa_facility_id", "facility id", "facility_id", "facilityid"],
    "facility_name": ["facility name", "facility_name", "facilityname"],
    "state": ["state", "state code"],
    "county": ["county", "county name"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "long"],
    "source_category": ["source category", "source_category"],
    "unit_id": ["epa unit id", "epa_unit_id", "unit id", "unit_id", "unitid"],
    "unit_type": ["unit type", "unit_type"],
    "primary_fuel": ["primary fuel", "primary_fuel"],
    "secondary_fuel": ["secondary fuel", "secondary_fuel"],
    "operating_date": ["operating date", "operating_date"],
    "retirement_date": ["retirement date", "retirement_date"],
    "reporting_year": ["reporting year", "reporting_year", "year"],
    "operating_time": ["operating time", "operating_time"],
    "gross_load": ["gross load", "gross_load"],
    "steam_load": ["steam load", "steam_load"],
    "heat_input": ["heat input", "heat_input"],
    "co2_mass": ["co2 mass", "co2_mass", "co2"],
    "so2_mass": ["so2 mass", "so2_mass", "so2"],
    "nox_mass": ["nox mass", "nox_mass", "nox"],
    "so2_control_information": ["so2 control information", "so2 control", "so2_control_information"],
    "nox_control_information": ["nox control information", "nox control", "nox_control_information"],
    "pm_control_information": ["pm control information", "pm control", "pm_control_information"],
    "program_code": ["program code", "program_code"],
}
REQUIRED_COLUMNS = {"facility_id", "facility_name", "state", "unit_id", "reporting_year"}
NUMERIC_COLUMNS = {
    "latitude", "longitude", "operating_time", "gross_load", "steam_load",
    "heat_input", "co2_mass", "so2_mass", "nox_mass",
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def canonicalize_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    aliases = {_key(alias): canonical for canonical, names in COLUMN_ALIASES.items() for alias in names}
    renamed: dict[Any, str] = {}
    unknown: list[str] = []
    for column in frame.columns:
        canonical = aliases.get(_key(column))
        if canonical:
            renamed[column] = canonical
        else:
            unknown.append(str(column))
    result = frame.rename(columns=renamed).copy()
    result = result.loc[:, ~result.columns.duplicated()]
    return result, unknown


def read_dataframe(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Only CSV, XLS, and XLSX files are supported.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    stream = io.BytesIO(content)
    if suffix == ".csv":
        return pd.read_csv(stream)
    return pd.read_excel(stream)


def validate_dataframe(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    frame, unknown_columns = canonicalize_columns(frame)
    errors: list[dict[str, Any]] = []
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    for column in missing:
        errors.append({"source_row_number": 0, "column_name": column, "error_code": "missing_required_column", "error_message": f"Required column '{column}' is missing."})
    if missing:
        return frame, errors, {"raw_records": len(frame), "accepted_records": 0, "rejected_records": len(frame), "unknown_columns": unknown_columns}

    frame = frame.copy()
    frame.index = range(2, len(frame) + 2)
    for column in NUMERIC_COLUMNS:
        if column in frame:
            converted = pd.to_numeric(frame[column], errors="coerce")
            invalid = frame[column].notna() & converted.isna()
            for row_number in frame.index[invalid]:
                errors.append({"source_row_number": int(row_number), "column_name": column, "error_code": "invalid_number", "error_message": "Value must be numeric.", "raw_value": str(frame.at[row_number, column])})
            frame[column] = converted
    frame["reporting_year"] = pd.to_numeric(frame["reporting_year"], errors="coerce")
    frame["state"] = frame["state"].astype("string").str.strip().str.upper()

    for row_number, row in frame.iterrows():
        for column in REQUIRED_COLUMNS:
            if pd.isna(row[column]) or str(row[column]).strip() == "":
                errors.append({"source_row_number": int(row_number), "column_name": column, "error_code": "missing_value", "error_message": "Required value is missing."})
        if not pd.isna(row["reporting_year"]) and not 1900 <= int(row["reporting_year"]) <= 2200:
            errors.append({"source_row_number": int(row_number), "column_name": "reporting_year", "error_code": "invalid_year", "error_message": "Reporting year must be between 1900 and 2200."})
        if row["state"] is not pd.NA and (len(str(row["state"])) != 2 or not str(row["state"]).isalpha()):
            errors.append({"source_row_number": int(row_number), "column_name": "state", "error_code": "invalid_state", "error_message": "State must be a two-letter code."})
        if not pd.isna(row.get("latitude")) and not -90 <= row["latitude"] <= 90:
            errors.append({"source_row_number": int(row_number), "column_name": "latitude", "error_code": "invalid_range", "error_message": "Latitude must be between -90 and 90."})
        if not pd.isna(row.get("longitude")) and not -180 <= row["longitude"] <= 180:
            errors.append({"source_row_number": int(row_number), "column_name": "longitude", "error_code": "invalid_range", "error_message": "Longitude must be between -180 and 180."})

    duplicate_mask = frame.duplicated(["facility_id", "unit_id", "reporting_year"], keep=False)
    for row_number in frame.index[duplicate_mask]:
        errors.append({"source_row_number": int(row_number), "column_name": "facility_id,unit_id,reporting_year", "error_code": "duplicate_key", "error_message": "Duplicate facility/unit/year record."})

    error_rows = {error["source_row_number"] for error in errors if error["source_row_number"] > 0}
    accepted = frame.loc[~frame.index.isin(error_rows)].copy()
    report = {
        "raw_records": len(frame),
        "accepted_records": len(accepted),
        "rejected_records": len(error_rows),
        "error_count": len(errors),
        "unknown_columns": unknown_columns,
        "preview": json.loads(accepted.head(10).where(pd.notna(accepted.head(10)), None).to_json(orient="records", date_format="iso")),
    }
    return accepted, errors, report


def _value(row: pd.Series, column: str, default: Any = None) -> Any:
    value = row.get(column, default)
    return default if pd.isna(value) else value


def import_accepted_rows(session: Session, accepted: pd.DataFrame, dataset: Dataset) -> int:
    facilities: dict[str, Facility] = {}
    units: dict[tuple[str, str], Unit] = {}
    for _, row in accepted.iterrows():
        facility_key = str(_value(row, "facility_id"))
        facility = facilities.get(facility_key) or session.scalar(select(Facility).where(Facility.epa_facility_id == facility_key))
        if facility is None:
            facility = Facility(
                epa_facility_id=facility_key,
                facility_name=str(_value(row, "facility_name")),
                state=str(_value(row, "state")),
                county=_value(row, "county"), latitude=_value(row, "latitude"), longitude=_value(row, "longitude"),
                source_category=_value(row, "source_category"), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            session.add(facility)
            session.flush()
        facilities[facility_key] = facility
        unit_key = (facility_key, str(_value(row, "unit_id")))
        unit = units.get(unit_key) or session.scalar(select(Unit).where(Unit.facility_id == facility.facility_id, Unit.epa_unit_id == unit_key[1]))
        if unit is None:
            unit = Unit(facility_id=facility.facility_id, epa_unit_id=unit_key[1], unit_type=_value(row, "unit_type"), primary_fuel=_value(row, "primary_fuel"), secondary_fuel=_value(row, "secondary_fuel"), operating_date=_value(row, "operating_date"), retirement_date=_value(row, "retirement_date"), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            session.add(unit)
            session.flush()
        units[unit_key] = unit
        session.add(AnnualRecord(unit_id=unit.unit_id, dataset_id=dataset.dataset_id, reporting_year=int(row["reporting_year"]), operating_time=_value(row, "operating_time"), gross_load=_value(row, "gross_load"), steam_load=_value(row, "steam_load"), heat_input=_value(row, "heat_input"), co2_mass=_value(row, "co2_mass"), so2_mass=_value(row, "so2_mass"), nox_mass=_value(row, "nox_mass"), so2_control_information=_value(row, "so2_control_information"), nox_control_information=_value(row, "nox_control_information"), pm_control_information=_value(row, "pm_control_information"), program_code=_value(row, "program_code"), created_at=datetime.now(timezone.utc)))
    return len(accepted)


def ingest_dataframe(session: Session, frame: pd.DataFrame, *, filename: str, source_name: str, content: bytes | None = None, approve: bool = False, storage_dir: str = "instance/uploads", source_url_or_api: str | None = None) -> dict[str, Any]:
    accepted, errors, report = validate_dataframe(frame)
    now = datetime.now(timezone.utc)
    dataset = Dataset(dataset_name=filename, data_source=source_name, reporting_year=None, retrieval_or_upload_date=now, original_filename=filename, number_of_raw_records=report["raw_records"], number_of_accepted_records=report["accepted_records"] if approve else 0, number_of_rejected_records=report["rejected_records"], status="pending")
    session.add(dataset)
    session.flush()
    if content is not None:
        path = Path(storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        stored = path / digest
        stored.write_bytes(content)
        uploaded = UploadedFile(dataset_id=dataset.dataset_id, original_filename=filename, storage_reference=str(stored), file_type=Path(filename).suffix.lower().lstrip("."), file_size_bytes=len(content), content_sha256=digest, upload_date=now, validation_status="approved" if approve and not errors else "valid" if not errors else "invalid", number_of_records=report["raw_records"], number_of_accepted_records=report["accepted_records"] if approve else 0, number_of_rejected_records=report["rejected_records"], validation_summary=report)
        session.add(uploaded)
    for error in errors:
        session.add(UploadValidationError(uploaded_file=uploaded if content is not None else None, source_row_number=error["source_row_number"], column_name=error.get("column_name"), error_code=error["error_code"], error_message=error["error_message"], raw_value=error.get("raw_value"), created_at=now)) if content is not None else None
    if approve and not errors:
        import_accepted_rows(session, accepted, dataset)
        dataset.status = "imported"
    elif approve:
        dataset.status = "failed"
    session.add(DataProvenance(dataset_id=dataset.dataset_id, source_name=source_name, retrieval_method="user_upload" if content is not None else "remote_url", retrieval_date=now, source_url_or_api=source_url_or_api if content is None else None))
    session.commit()
    return {"dataset_id": dataset.dataset_id, "status": dataset.status, "validation": report, "errors": errors}


def retrieve_dataframe(url: str, *, params: dict[str, Any] | None = None, timeout: int = 60) -> tuple[pd.DataFrame, bytes]:
    response = requests.get(url, params=params or {}, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("data", payload.get("results", payload))
        return pd.DataFrame(payload), response.content
    return read_dataframe(response.content, url.split("?")[0]), response.content


def build_annual_query(args: dict[str, Any]) -> Select[Any]:
    query = select(AnnualRecord, Unit, Facility).join(AnnualRecord.unit).join(Unit.facility)
    filters = []
    for model, field, arg in [(Facility, Facility.epa_facility_id, "facility_id"), (Facility, Facility.state, "state"), (Facility, Facility.county, "county"), (Unit, Unit.epa_unit_id, "unit_id"), (Unit, Unit.primary_fuel, "primary_fuel"), (Unit, Unit.secondary_fuel, "secondary_fuel"), (Unit, Unit.unit_type, "unit_type")]:
        if args.get(arg):
            filters.append(field == args[arg])
    for field, arg in [(AnnualRecord.reporting_year, "reporting_year"), (AnnualRecord.operating_time, "operating_time"), (AnnualRecord.gross_load, "gross_load"), (AnnualRecord.heat_input, "heat_input"), (AnnualRecord.co2_mass, "co2_mass"), (AnnualRecord.so2_mass, "so2_mass"), (AnnualRecord.nox_mass, "nox_mass")]:
        if args.get(arg):
            filters.append(field == int(args[arg]) if arg == "reporting_year" else field >= float(args[arg]))
    if filters:
        query = query.where(*filters)
    return query.order_by(desc(AnnualRecord.reporting_year), Facility.facility_name)
