"""Populate normalized EPA and TRACI source tables."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from data_service import ingest_dataframe
from models import (
    Base,
    DataProvenance,
    Dataset,
    Facility,
    IndicatorDefinition,
    TraciFactor,
    WeightScenario,
)


TRACI_URL = "https://www.epa.gov/system/files/documents/2024-01/traci_2_2.xlsx"
EPA_API_ROOT = os.getenv("EPA_API_ROOT", "https://api.epa.gov/easey").rstrip("/")
EPA_API_ENV = os.getenv("EPA_API_ENV", "").strip("/")
EPA_API_PREFIX = f"{EPA_API_ROOT}/{EPA_API_ENV}" if EPA_API_ENV else EPA_API_ROOT
EPA_FACILITIES_URL = f"{EPA_API_PREFIX}/facilities-mgmt/facilities/attributes"
EPA_ANNUAL_EMISSIONS_URL = f"{EPA_API_PREFIX}/emissions-mgmt/emissions/apportioned/annual"


def _download(url: str, headers: dict[str, str] | None = None) -> bytes:
    response = requests.get(url, headers=headers or {}, timeout=120)
    response.raise_for_status()
    return response.content


def _source_dataset(session: Session, name: str, source: str, url: str, count: int) -> Dataset:
    existing = session.scalar(select(Dataset).where(Dataset.dataset_name == name, Dataset.data_source == source))
    if existing is not None:
        return existing
    dataset = Dataset(
        dataset_name=name,
        data_source=source,
        retrieval_or_upload_date=datetime.now(timezone.utc),
        number_of_raw_records=count,
        number_of_accepted_records=count,
        status="imported",
        original_filename=Path(url.split("?")[0]).name or None,
    )
    session.add(dataset)
    session.flush()
    session.add(DataProvenance(
        dataset_id=dataset.dataset_id,
        source_name=source,
        source_url_or_api=url,
        retrieval_method="download",
        retrieval_date=dataset.retrieval_or_upload_date,
        checksum=None,
    ))
    return dataset


def _epa_pages(url: str, api_key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        request_params = {**params, "api_key": api_key, "page": page, "perPage": 500}
        for attempt in range(5):
            response = requests.get(url, params=request_params, timeout=120)
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            time.sleep(min(delay, 30))
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                message = response.text
            raise RuntimeError(f"CAMPD request failed ({response.status_code}): {message}")
        payload = response.json()
        page_records = payload
        if isinstance(payload, dict):
            for key in ("data", "results", "items", "records"):
                if isinstance(payload.get(key), list):
                    page_records = payload[key]
                    break
        if not isinstance(page_records, list):
            keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            raise RuntimeError(f"Unexpected CAMPD response from {url}; expected a record list, got {keys}")
        records.extend(page_records)
        total = int(response.headers.get("X-Total-Count", len(records)))
        if not page_records or len(records) >= total:
            return records
        page += 1


def _campd_key(value: Any) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _campd_column(frame: pd.DataFrame, *names: str) -> pd.Series:
    columns = {_campd_key(column): column for column in frame.columns}
    for name in names:
        if _campd_key(name) in columns:
            return frame[columns[_campd_key(name)]]
    return pd.Series([None] * len(frame), index=frame.index)


def load_campd_api(session: Session, api_key: str, years: list[int]) -> int:
    """Load EPA facility attributes and annual apportioned emissions by year."""
    total = 0
    for year in years:
        facilities = _epa_pages(EPA_FACILITIES_URL, api_key, {"year": year})
        emissions = _epa_pages(EPA_ANNUAL_EMISSIONS_URL, api_key, {"year": year})
        facility_frame = pd.DataFrame(facilities)
        emissions_frame = pd.DataFrame(emissions)
        if facility_frame.empty or emissions_frame.empty:
            print(f"EPA CAMPD {year}: no records returned")
            continue
        facility_frame["facility_id"] = _campd_column(facility_frame, "epaFacilityId", "facilityId", "facility_id")
        facility_frame["unit_id"] = _campd_column(facility_frame, "epaUnitId", "unitId", "unit_id")
        emissions_frame["facility_id"] = _campd_column(emissions_frame, "epaFacilityId", "facilityId", "facility_id")
        emissions_frame["unit_id"] = _campd_column(emissions_frame, "epaUnitId", "unitId", "unit_id")
        facility_frame["facility_id"] = facility_frame["facility_id"].astype(str)
        facility_frame["unit_id"] = facility_frame["unit_id"].astype(str)
        emissions_frame["facility_id"] = emissions_frame["facility_id"].astype(str)
        emissions_frame["unit_id"] = emissions_frame["unit_id"].astype(str)
        joined = emissions_frame.merge(facility_frame.drop_duplicates(["facility_id", "unit_id"]), on=["facility_id", "unit_id"], how="left", suffixes=("", "_facility"))
        output = pd.DataFrame({
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
            # CAMPD FacilityAttributesDTO has commercialOperationDate and operatingStatus,
            # but no retirement/end date field, so leave retirement_date unset.
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
        result = ingest_dataframe(
            session, output, filename=f"campd-{year}.json", source_name="EPA CAMPD",
            approve=True, source_url_or_api=f"{EPA_ANNUAL_EMISSIONS_URL}?year={year}",
            query_parameters={"year": year, "reporting_year": year},
        )
        total += result["validation"]["accepted_records"]
        print(f"EPA CAMPD {year}: {result['status']} ({result['validation']['accepted_records']} records)")
    return total


def load_traci(session: Session, content: bytes | None = None, url: str = TRACI_URL) -> int:
    content = content or _download(url)
    workbook = pd.ExcelFile(io.BytesIO(content))
    raw = pd.read_excel(workbook, sheet_name="Substances", header=None)
    header_row = raw.index[raw.apply(lambda row: row.astype(str).str.strip().eq("CAS #").any(), axis=1)][0]
    frame = raw.iloc[header_row + 1:].copy()
    frame.columns = raw.iloc[header_row].astype(str).str.strip()
    frame = frame.dropna(how="all")
    dataset = _source_dataset(session, "traci_2_2.xlsx", "TRACI", url, len(frame))
    inserted = 0
    seen: set[tuple[str, str, str, str]] = set(session.execute(select(TraciFactor.traci_version, TraciFactor.pollutant, TraciFactor.impact_category, TraciFactor.geography).where(TraciFactor.traci_version == "2.2")).all())
    for _, row in frame.iterrows():
        substance = row.get("Substance Name")
        if pd.isna(substance):
            continue
        for column in frame.columns:
            if column in {"CAS #", "Formatted CAS #", "Substance Name", "CF Flag", "CF Flag HH carcinogenic", "CF Flag HH non-carcinogenic"}:
                continue
            value = pd.to_numeric(row.get(column), errors="coerce")
            if pd.isna(value):
                continue
            if "Acidification" in column:
                category, unit = "acidification", "kg SO2 eq / kg substance"
            elif "Global Climate" in column:
                category, unit = "global warming", "kg CO2 eq / kg substance"
            elif "Photochemical" in column:
                category, unit = "smog formation", "kg O3 eq / kg substance"
            elif "Ozone Depletion" in column:
                category, unit = "ozone depletion", "kg CFC-11 eq / kg substance"
            elif "Particulate" in column:
                category, unit = "particulate matter", "kg PM2.5 eq / kg substance"
            elif "Ecotox" in column:
                category, unit = "ecotoxicity", "CTUeco/kg"
            elif "Human health" in column:
                category, unit = "human health", "CTUcancer/kg or CTUnoncancer/kg"
            else:
                continue
            key = ("2.2", str(substance), category, f"US site-generic:{column}")
            if key in seen:
                continue
            seen.add(key)
            session.add(TraciFactor(
                pollutant=str(substance), impact_category=category,
                characterization_factor=float(value), factor_unit=unit,
                reference_flow_unit="kg substance", traci_version="2.2",
                source_name="US EPA TRACI 2.2", source_url=url,
                geography=f"US site-generic:{column}", notes=column,
            ))
            inserted += 1
    session.commit()
    return inserted


def seed_indicators(session: Session) -> int:
    definitions = [
        ("CO2 per MWh", "CO2 mass divided by gross load", "kg/MWh"),
    ]
    inserted = 0
    for name, formula, unit in definitions:
        if session.scalar(select(IndicatorDefinition).where(IndicatorDefinition.indicator_name == name, IndicatorDefinition.version == "1.0")):
            continue
        session.add(IndicatorDefinition(indicator_name=name, description=formula, formula_expression=formula, output_unit=unit, version="1.0"))
        inserted += 1
    if session.scalar(select(WeightScenario).where(WeightScenario.scenario_name == "default")) is None:
        session.add(WeightScenario(scenario_name="default", description="Initial equal-weight analysis scenario", created_at=datetime.now(timezone.utc), created_by="populate_database"))
    session.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///epa_data.db"))
    parser.add_argument("--epa-file", type=Path, help="Local EPA CAMPD CSV/XLS/XLSX export")
    parser.add_argument("--epa-api-key", default=os.getenv("EPA_API_KEY"), help="EPA API key; preferably supplied through EPA_API_KEY")
    parser.add_argument("--epa-years", default="2023", help="Comma-separated CAMPD years")
    parser.add_argument("--traci-file", type=Path)
    args = parser.parse_args()
    engine = create_engine(args.database_url, future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if args.epa_api_key:
            load_campd_api(session, args.epa_api_key, [int(year.strip()) for year in args.epa_years.split(",")])
        if args.epa_file:
            content = args.epa_file.read_bytes()
            result = ingest_dataframe(session, pd.read_csv(io.BytesIO(content)) if args.epa_file.suffix.lower() == ".csv" else pd.read_excel(io.BytesIO(content)), filename=args.epa_file.name, source_name="EPA CAMPD", content=content, approve=True)
            print(f"EPA CAMPD: {result['status']} ({result['validation']['accepted_records']} records)")
        print(f"TRACI: {load_traci(session, args.traci_file.read_bytes() if args.traci_file else None)} factors")
        print(f"Indicators: {seed_indicators(session)} inserted")


if __name__ == "__main__":
    main()