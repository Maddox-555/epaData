"""Data retrieval, validation, import, and query services."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sqlalchemy import Select, desc, func, select
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
        "default_endpoint": "https://api.epa.gov/easey/emissions-mgmt/emissions/apportioned/annual",
        "supported_import": True,
    },
    "nasa-power": {
        "name": "NASA POWER",
        "description": "Monthly regional climate data summarized to annual facility climate records.",
        "default_endpoint": "https://power.larc.nasa.gov/api/temporal/monthly/regional",
        "supported_import": False,
    },
    "traci": {
        "name": "TRACI",
        "description": "Versioned EPA TRACI characterization factors for impact calculations.",
        "default_endpoint": None,
        "supported_import": False,
    },
}

EPA_API_ROOT = os.getenv("EPA_API_ROOT", "https://api.epa.gov/easey").rstrip("/")
EPA_API_ENV = os.getenv("EPA_API_ENV", "").strip("/")
EPA_API_PREFIX = f"{EPA_API_ROOT}/{EPA_API_ENV}" if EPA_API_ENV else EPA_API_ROOT
EPA_CAMPD_FACILITIES_URL = f"{EPA_API_PREFIX}/facilities-mgmt/facilities/attributes"
EPA_CAMPD_ANNUAL_URL = f"{EPA_API_PREFIX}/emissions-mgmt/emissions/apportioned/annual"

COLUMN_ALIASES = {
    "facility_id": ["epa facility id", "epa_facility_id", "facility id", "facility_id", "facilityid"],
    "facility_name": ["facility name", "facility_name", "facilityname"],
    "state": ["state", "state code"],
    "county": ["county", "county name"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "long"],
    "source_category": ["source category", "source_category", "sourceCategory"],
    "unit_id": ["epa unit id", "epa_unit_id", "unit id", "unit_id", "unitid"],
    "unit_type": ["unit type", "unit_type"],
    "primary_fuel": ["primary fuel", "primary_fuel", "primaryFuelInfo"],
    "secondary_fuel": ["secondary fuel", "secondary_fuel", "secondaryFuelInfo"],
    "operating_date": ["operating date", "operating_date", "commercialOperationDate"],
    # Keep the column for schema compatibility; CAMPD attributes do not publish a retirement date.
    "retirement_date": ["retirement date", "retirement_date"],
    "reporting_year": ["reporting year", "reporting_year", "year"],
    "operating_time": ["operating time", "operating_time", "sumOpTime"],
    "gross_load": ["gross load", "gross_load"],
    "steam_load": ["steam load", "steam_load"],
    "heat_input": ["heat input", "heat_input"],
    "co2_mass": ["co2 mass", "co2_mass", "co2"],
    "so2_mass": ["so2 mass", "so2_mass", "so2"],
    "nox_mass": ["nox mass", "nox_mass", "nox"],
    "so2_control_information": ["so2 control information", "so2 control", "so2_control_information", "so2ControlInfo"],
    "nox_control_information": ["nox control information", "nox control", "nox_control_information", "noxControlInfo"],
    "pm_control_information": ["pm control information", "pm control", "pm_control_information", "pmControlInfo"],
    "program_code": ["program code", "program_code", "programCodeInfo"],
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


def _date_value(row: pd.Series, column: str) -> date | None:
    value = _value(row, column)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


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
        else:
            for attribute in ("facility_name", "state", "county", "latitude", "longitude", "source_category"):
                value = _value(row, attribute)
                if value is not None:
                    setattr(facility, attribute, value)
            facility.updated_at = datetime.now(timezone.utc)
        facilities[facility_key] = facility
        unit_key = (facility_key, str(_value(row, "unit_id")))
        unit = units.get(unit_key) or session.scalar(select(Unit).where(Unit.facility_id == facility.facility_id, Unit.epa_unit_id == unit_key[1]))
        if unit is None:
            unit = Unit(facility_id=facility.facility_id, epa_unit_id=unit_key[1], unit_type=_value(row, "unit_type"), primary_fuel=_value(row, "primary_fuel"), secondary_fuel=_value(row, "secondary_fuel"), operating_date=_date_value(row, "operating_date"), retirement_date=_date_value(row, "retirement_date"), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
            session.add(unit)
            session.flush()
        else:
            for attribute in ("unit_type", "primary_fuel", "secondary_fuel", "operating_date", "retirement_date"):
                value = _date_value(row, attribute) if attribute in {"operating_date", "retirement_date"} else _value(row, attribute)
                if value is not None:
                    setattr(unit, attribute, value)
            unit.updated_at = datetime.now(timezone.utc)
        units[unit_key] = unit
        year = int(row["reporting_year"])
        annual_fields = {
            "operating_time": _value(row, "operating_time"),
            "gross_load": _value(row, "gross_load"),
            "steam_load": _value(row, "steam_load"),
            "heat_input": _value(row, "heat_input"),
            "co2_mass": _value(row, "co2_mass"),
            "so2_mass": _value(row, "so2_mass"),
            "nox_mass": _value(row, "nox_mass"),
            "so2_control_information": _value(row, "so2_control_information"),
            "nox_control_information": _value(row, "nox_control_information"),
            "pm_control_information": _value(row, "pm_control_information"),
            "program_code": _value(row, "program_code"),
        }
        existing_records = session.scalars(
            select(AnnualRecord).where(
                AnnualRecord.unit_id == unit.unit_id,
                AnnualRecord.reporting_year == year,
            )
        ).all()
        if existing_records:
            for record in existing_records:
                for attribute, value in annual_fields.items():
                    if value is not None:
                        setattr(record, attribute, value)
        else:
            session.add(AnnualRecord(
                unit_id=unit.unit_id,
                dataset_id=dataset.dataset_id,
                reporting_year=year,
                created_at=datetime.now(timezone.utc),
                **annual_fields,
            ))
    return len(accepted)


def ingest_dataframe(session: Session, frame: pd.DataFrame, *, filename: str, source_name: str, content: bytes | None = None, approve: bool = False, storage_dir: str = "instance/uploads", source_url_or_api: str | None = None, query_parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    accepted, errors, report = validate_dataframe(frame)
    now = datetime.now(timezone.utc)
    requested_year = (query_parameters or {}).get("reporting_year", (query_parameters or {}).get("year"))
    if isinstance(requested_year, list):
        requested_year = requested_year[0] if len(requested_year) == 1 else None
    try:
        requested_year = int(requested_year) if requested_year is not None else None
    except (TypeError, ValueError):
        requested_year = None
    dataset = Dataset(dataset_name=filename, data_source=source_name, reporting_year=requested_year, retrieval_or_upload_date=now, original_filename=filename, number_of_raw_records=report["raw_records"], number_of_accepted_records=report["accepted_records"] if approve else 0, number_of_rejected_records=report["rejected_records"], status="pending")
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
    session.add(DataProvenance(dataset_id=dataset.dataset_id, source_name=source_name, retrieval_method="user_upload" if source_url_or_api is None else "remote_url", retrieval_date=now, source_url_or_api=source_url_or_api, query_parameters=query_parameters))
    session.commit()
    return {"dataset_id": dataset.dataset_id, "status": dataset.status, "validation": report, "errors": errors}


def approve_uploaded_dataset(session: Session, dataset_id: int) -> dict[str, Any]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError("Dataset was not found.")
    uploaded = session.scalar(select(UploadedFile).where(UploadedFile.dataset_id == dataset_id))
    if uploaded is None:
        raise ValueError("Dataset has no stored upload file to approve.")
    if dataset.status == "cancelled":
        raise ValueError("Cancelled uploads cannot be approved.")
    content = Path(uploaded.storage_reference).read_bytes()
    accepted, errors, report = validate_dataframe(read_dataframe(content, uploaded.original_filename))
    if errors:
        dataset.status = "failed"
        uploaded.validation_status = "invalid"
        uploaded.validation_summary = report
        session.commit()
        return {"dataset_id": dataset_id, "status": dataset.status, "validation": report, "errors": errors}
    import_accepted_rows(session, accepted, dataset)
    dataset.number_of_accepted_records = report["accepted_records"]
    dataset.number_of_rejected_records = report["rejected_records"]
    dataset.status = "imported"
    uploaded.validation_status = "approved"
    uploaded.number_of_accepted_records = report["accepted_records"]
    uploaded.validation_summary = report
    session.commit()
    return {"dataset_id": dataset_id, "status": dataset.status, "validation": report, "errors": []}


def cancel_uploaded_dataset(session: Session, dataset_id: int) -> dict[str, Any]:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise ValueError("Dataset was not found.")
    if dataset.status == "imported":
        raise ValueError("Imported datasets cannot be cancelled.")
    uploaded = session.scalar(select(UploadedFile).where(UploadedFile.dataset_id == dataset_id))
    dataset.status = "cancelled"
    if uploaded is not None:
        uploaded.validation_status = "cancelled"
    session.commit()
    return {"dataset_id": dataset_id, "status": dataset.status}


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


def _campd_key(value: Any) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _campd_column(frame: pd.DataFrame, *names: str) -> pd.Series:
    columns = {_campd_key(column): column for column in frame.columns}
    for name in names:
        if _campd_key(name) in columns:
            return frame[columns[_campd_key(name)]]
    return pd.Series([None] * len(frame), index=frame.index)


def _campd_get_pages(url: str, api_key: str, year: int, timeout: int = 60) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        response = requests.get(url, params={"api_key": api_key, "year": year, "page": page, "perPage": 500}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        page_records: Any = payload
        if isinstance(payload, dict):
            for key in ("data", "results", "items", "records"):
                if isinstance(payload.get(key), list):
                    page_records = payload[key]
                    break
        if not isinstance(page_records, list):
            raise ValueError("CAMPD returned an unexpected response shape.")
        records.extend(page_records)
        total = int(response.headers.get("X-Total-Count", len(records)))
        if not page_records or len(records) >= total:
            return records
        page += 1


def retrieve_campd_dataframe(params: dict[str, Any], api_key: str) -> tuple[pd.DataFrame, bytes]:
    """Retrieve and join CAMPD annual emissions with facility attributes."""
    try:
        year = int(params.get("year") or params.get("reporting_year"))
    except (TypeError, ValueError):
        raise ValueError("CAMPD reporting year must be an integer.") from None
    facilities = pd.DataFrame(_campd_get_pages(EPA_CAMPD_FACILITIES_URL, api_key, year))
    emissions = pd.DataFrame(_campd_get_pages(EPA_CAMPD_ANNUAL_URL, api_key, year))
    if facilities.empty or emissions.empty:
        return pd.DataFrame(), b"[]"
    facilities["facility_id"] = _campd_column(facilities, "epaFacilityId", "facilityId", "facility_id").astype(str)
    facilities["unit_id"] = _campd_column(facilities, "epaUnitId", "unitId", "unit_id").astype(str)
    emissions["facility_id"] = _campd_column(emissions, "epaFacilityId", "facilityId", "facility_id").astype(str)
    emissions["unit_id"] = _campd_column(emissions, "epaUnitId", "unitId", "unit_id").astype(str)
    joined = emissions.merge(facilities.drop_duplicates(["facility_id", "unit_id"]), on=["facility_id", "unit_id"], how="left", suffixes=("", "_facility"))
    frame = pd.DataFrame({
        "facility_id": joined["facility_id"],
        "facility_name": _campd_column(joined, "facilityName", "facility_name"),
        "state": _campd_column(joined, "state", "stateCode"),
        "county": _campd_column(joined, "county", "countyName"),
        "latitude": _campd_column(joined, "latitude", "facilityLatitude"),
        "longitude": _campd_column(joined, "longitude", "facilityLongitude"),
        "unit_id": joined["unit_id"],
        "unit_type": _campd_column(joined, "unitType", "unit_type"),
        "source_category": _campd_column(joined, "sourceCategory", "source_category"),
        "primary_fuel": _campd_column(joined, "primaryFuelInfo", "unitFuelType", "primaryFuel", "primary_fuel"),
        "secondary_fuel": _campd_column(joined, "secondaryFuelInfo", "secondaryFuel", "secondary_fuel"),
        "operating_date": _campd_column(joined, "commercialOperationDate", "operatingDate", "operating_date"),
        "reporting_year": year,
        "operating_time": _campd_column(joined, "sumOpTime", "operatingTime", "operating_time"),
        "gross_load": _campd_column(joined, "grossLoad", "gross_load"),
        "steam_load": _campd_column(joined, "steamLoad", "steam_load"),
        "heat_input": _campd_column(joined, "heatInput", "heat_input"),
        "co2_mass": _campd_column(joined, "co2Mass", "co2_mass", "co2"),
        "so2_mass": _campd_column(joined, "so2Mass", "so2_mass", "so2"),
        "nox_mass": _campd_column(joined, "noxMass", "nox_mass", "nox"),
        "so2_control_information": _campd_column(joined, "so2ControlInfo", "so2ControlInformation", "so2_control_information"),
        "nox_control_information": _campd_column(joined, "noxControlInfo", "noxControlInformation", "nox_control_information"),
        "pm_control_information": _campd_column(joined, "pmControlInfo", "pmControlInformation", "pm_control_information"),
        "program_code": _campd_column(joined, "programCodeInfo", "programCode", "program_code"),
    })
    text_filters = {"state": "state", "primary_fuel": "primary_fuel", "unit_type": "unit_type", "so2_control_information": "so2_control_information", "nox_control_information": "nox_control_information", "pm_control_information": "pm_control_information"}
    for parameter, column in text_filters.items():
        value = str(params.get(parameter) or params.get(parameter.replace("_information", "")) or "").strip().lower()
        if value:
            frame = frame[frame[column].astype("string").str.lower().str.contains(value, na=False)]
    facility_id = str(params.get("facility_id") or "").strip()
    if facility_id:
        frame = frame[frame["facility_id"].astype(str) == facility_id]
    return frame, frame.to_json(orient="records").encode("utf-8")


EXACT_TEXT_FILTERS = {
    "facility_id": Facility.epa_facility_id,
    "unit_id": Unit.epa_unit_id,
    "state": Facility.state,
}
CONTAINS_TEXT_FILTERS = {
    "facility_name": Facility.facility_name,
    "county": Facility.county,
    "primary_fuel": Unit.primary_fuel,
    "secondary_fuel": Unit.secondary_fuel,
    "unit_type": Unit.unit_type,
    "so2_control_information": AnnualRecord.so2_control_information,
    "nox_control_information": AnnualRecord.nox_control_information,
    "pm_control_information": AnnualRecord.pm_control_information,
    "program_code": AnnualRecord.program_code,
}
RANGE_FILTERS = {
    "operating_time": AnnualRecord.operating_time,
    "gross_load": AnnualRecord.gross_load,
    "steam_load": AnnualRecord.steam_load,
    "heat_input": AnnualRecord.heat_input,
    "co2_mass": AnnualRecord.co2_mass,
    "so2_mass": AnnualRecord.so2_mass,
    "nox_mass": AnnualRecord.nox_mass,
}
SORT_COLUMNS = {
    "facility_name": Facility.facility_name,
    "facility_id": Facility.epa_facility_id,
    "unit_id": Unit.epa_unit_id,
    "state": Facility.state,
    "county": Facility.county,
    "primary_fuel": Unit.primary_fuel,
    "unit_type": Unit.unit_type,
    "reporting_year": AnnualRecord.reporting_year,
    **RANGE_FILTERS,
}

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}

DESCRIPTION_METRICS = {
    "co2": "co2_mass", "carbon dioxide": "co2_mass", "co2 emissions": "co2_mass",
    "so2": "so2_mass", "sulfur dioxide": "so2_mass", "so2 emissions": "so2_mass",
    "nox": "nox_mass", "nitrogen oxides": "nox_mass", "nox emissions": "nox_mass",
    "gross load": "gross_load", "heat input": "heat_input", "operating time": "operating_time",
}

DESCRIPTION_FUELS = {
    "natural gas": "natural gas", "natural-gas": "natural gas", "gas-fired": "natural gas",
    "gas": "natural gas", "coal-fired": "coal", "coal": "coal", "oil-fired": "oil",
    "oil": "oil", "biomass": "biomass", "petroleum coke": "petroleum coke",
}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _description_number(value: str) -> str:
    normalized = value.lower().replace(",", "").strip()
    match = re.fullmatch(r"([\d.]+)\s*(thousand|million|billion|k|m|b)?", normalized)
    if not match:
        raise ValueError(f"could not understand number '{value}'")
    amount = float(match.group(1))
    multiplier = {"thousand": 1_000, "k": 1_000, "million": 1_000_000, "m": 1_000_000, "billion": 1_000_000_000, "b": 1_000_000_000}.get(match.group(2), 1)
    result = amount * multiplier
    return str(int(result)) if result.is_integer() else str(result)


def parse_search_description(description: str) -> dict[str, str]:
    """Translate common project search wording into Explorer query parameters."""
    text = re.sub(r"\s+", " ", description.strip().lower())
    parsed: dict[str, str] = {}
    for name, code in sorted(STATE_NAMES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(name)}\b", text):
            parsed["state"] = code
            break
    state_code = re.search(r"\b(?:in|from|within)\s+([a-z]{2})\b", text)
    if state_code:
        parsed["state"] = state_code.group(1).upper()
    year_range = re.search(r"\b(?:from|between)\s+(20\d{2})\s+(?:to|and|through)\s+(20\d{2})\b", text)
    if year_range:
        parsed["reporting_year_min"] = year_range.group(1)
        parsed["reporting_year_max"] = year_range.group(2)
    else:
        year = re.search(r"\b(?:in|for|during|year)\s+(20\d{2})\b|\b(20\d{2})\s*[-/]\s*(20\d{2})\b", text)
        if year and year.group(1):
            parsed["reporting_year"] = year.group(1)
        elif year and year.group(2):
            parsed["reporting_year_min"] = year.group(2)
            parsed["reporting_year_max"] = year.group(3)
    for fuel, canonical in sorted(DESCRIPTION_FUELS.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z]){re.escape(fuel)}(?![a-z])", text):
            parsed["primary_fuel"] = canonical
            break
    metric_pattern = "|".join(sorted((re.escape(name) for name in DESCRIPTION_METRICS), key=len, reverse=True))
    comparison = r"(greater than|more than|above|over|at least|less than|below|under|at most|<=|>=|<|>)"
    number = r"([\d,.]+\s*(?:thousand|million|billion|k|m|b)?)"
    match = re.search(rf"(?P<metric>{metric_pattern})(?:\s+\w+){{0,5}}\s+{comparison}\s*{number}", text)
    if match:
        metric = DESCRIPTION_METRICS[match.group("metric")]
        operator = match.group(2)
        bound = _description_number(match.group(3))
        suffix = "max" if operator in {"less than", "below", "under", "at most", "<", "<="} else "min"
        parsed[f"{metric}_{suffix}"] = bound
    reverse_match = re.search(rf"{comparison}\s*{number}(?:\s+\w+){{0,5}}\s+(?P<metric>{metric_pattern})\b", text)
    if reverse_match:
        metric = DESCRIPTION_METRICS[reverse_match.group("metric")]
        operator = reverse_match.group(1)
        bound = _description_number(reverse_match.group(2))
        suffix = "max" if operator in {"less than", "below", "under", "at most", "<", "<="} else "min"
        parsed[f"{metric}_{suffix}"] = bound
    between = re.search(rf"(?P<metric>{metric_pattern})(?:\s+\w+){{0,5}}\s+between\s+{number}\s+and\s+{number}", text)
    if between:
        metric = DESCRIPTION_METRICS[between.group("metric")]
        parsed[f"{metric}_min"] = _description_number(between.group(2))
        parsed[f"{metric}_max"] = _description_number(between.group(3))
    ranking = re.search(rf"\b(top|highest|largest|bottom|lowest|smallest)\s*(\d+)?(?:\s+\w+){{0,4}}\s+by\s+(?P<metric>{metric_pattern})", text)
    if ranking:
        parsed["sort"] = DESCRIPTION_METRICS[ranking.group("metric")]
        parsed["order"] = "asc" if ranking.group(1) in {"bottom", "lowest", "smallest"} else "desc"
        parsed["limit"] = ranking.group(2) or "10"
    if not parsed:
        raise ValueError("Description did not contain a supported search condition. Try including a state, year, fuel, metric, comparison, or ranking; for example 'coal units in Kentucky in 2024 with CO2 above 500k'.")
    return parsed


def normalized_search_args(args: dict[str, Any]) -> dict[str, Any]:
    description = str(args.get("description") or "").strip()
    parsed = parse_search_description(description) if description else {}
    explicit = {key: value for key, value in args.items() if key != "description" and str(value).strip()}
    return {**parsed, **explicit}


def annual_filters(args: dict[str, Any]) -> list[Any]:
    """Translate search arguments into SQL conditions; raise ValueError for invalid numbers."""
    args = normalized_search_args(args)
    filters: list[Any] = []
    for arg, field in EXACT_TEXT_FILTERS.items():
        value = str(args.get(arg) or "").strip()
        if value:
            filters.append(func.upper(field) == value.upper())
    for arg, field in CONTAINS_TEXT_FILTERS.items():
        value = str(args.get(arg) or "").strip()
        if value:
            filters.append(field.ilike(f"%{_escape_like(value)}%", escape="\\"))
    for arg, operator in (("reporting_year", "eq"), ("reporting_year_min", "ge"), ("reporting_year_max", "le")):
        value = str(args.get(arg) or "").strip()
        if value:
            year = int(value)
            column = AnnualRecord.reporting_year
            filters.append(column == year if operator == "eq" else column >= year if operator == "ge" else column <= year)
    for name, field in RANGE_FILTERS.items():
        # A bare metric name is kept as a minimum for backward compatibility.
        for arg, is_minimum in ((name, True), (f"{name}_min", True), (f"{name}_max", False)):
            value = str(args.get(arg) or "").strip()
            if value:
                number = float(value)
                filters.append(field >= number if is_minimum else field <= number)
    return filters


def annual_order(args: dict[str, Any]) -> list[Any]:
    sort = str(args.get("sort") or "").strip()
    if not sort:
        return [desc(AnnualRecord.reporting_year), Facility.facility_name, Unit.epa_unit_id, AnnualRecord.annual_record_id]
    if sort not in SORT_COLUMNS:
        raise ValueError(f"unsupported sort column '{sort}'")
    order = str(args.get("order") or "desc").strip().lower()
    if order not in {"asc", "desc"}:
        raise ValueError("order must be 'asc' or 'desc'")
    column = SORT_COLUMNS[sort]
    primary = column.asc().nulls_last() if order == "asc" else column.desc().nulls_last()
    return [primary, AnnualRecord.annual_record_id]


def _annual_base_query(*columns: Any) -> Select[Any]:
    return select(*columns).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility)


def build_annual_query(args: dict[str, Any]) -> Select[Any]:
    query = _annual_base_query(AnnualRecord, Unit, Facility).where(*annual_filters(args))
    args = normalized_search_args(args)
    return query.order_by(*annual_order(args))


def count_annual_records(session: Session, args: dict[str, Any]) -> int:
    query = _annual_base_query(func.count(AnnualRecord.annual_record_id)).where(*annual_filters(args))
    return int(session.scalar(query) or 0)
