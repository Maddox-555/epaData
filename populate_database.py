"""Populate normalized EPA, TRACI, and NASA POWER source tables."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import math
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
    WeatherAnnualRecord,
    WeightScenario,
)


NASA_POWER_MONTHLY_URL = "https://power.larc.nasa.gov/api/temporal/monthly/regional"
TRACI_URL = "https://www.epa.gov/system/files/documents/2024-01/traci_2_2.xlsx"
EPA_FACILITIES_URL = "https://api.epa.gov/easey/facilities-mgmt/facilities/attributes"
EPA_ANNUAL_EMISSIONS_URL = "https://api.epa.gov/easey/emissions-mgmt/emissions/apportioned/annual"


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
        response = requests.get(url, params={**params, "api_key": api_key, "page": page, "perPage": 500}, timeout=120)
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
            "primary_fuel": _campd_column(joined, "unitFuelType", "primaryFuel", "primary_fuel"),
            "secondary_fuel": _campd_column(joined, "secondaryFuel", "secondary_fuel"),
            "reporting_year": year,
            "operating_time": _campd_column(joined, "operatingTime", "operating_time"),
            "gross_load": _campd_column(joined, "grossLoad", "gross_load"),
            "steam_load": _campd_column(joined, "steamLoad", "steam_load"),
            "heat_input": _campd_column(joined, "heatInput", "heat_input"),
            "co2_mass": _campd_column(joined, "co2Mass", "co2_mass", "co2"),
            "so2_mass": _campd_column(joined, "so2Mass", "so2_mass", "so2"),
            "nox_mass": _campd_column(joined, "noxMass", "nox_mass", "nox"),
            "program_code": _campd_column(joined, "programCode", "program_code"),
        })
        result = ingest_dataframe(
            session, output, filename=f"campd-{year}.json", source_name="EPA CAMPD",
            approve=True, source_url_or_api=f"{EPA_ANNUAL_EMISSIONS_URL}?year={year}",
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


def load_nasa_power_annual_weather(session: Session, years: list[int], workers: int = 6) -> int:
    """Load compact annual climate summaries from NASA POWER at facility coordinates."""
    facilities = {
        facility.facility_id: facility
        for facility in session.scalars(select(Facility).where(Facility.latitude.is_not(None), Facility.longitude.is_not(None))).all()
    }
    existing = {
        (record.facility_id, record.reporting_year)
        for record in session.scalars(select(WeatherAnnualRecord)).all()
    }
    parameters = "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,WS2M"

    facilities_to_load = [
        facility for facility in facilities.values()
        if any((facility.facility_id, year) not in existing for year in years)
    ]
    if not facilities_to_load:
        return 0

    # Regional requests return NASA POWER's native grid (0.5 degree latitude by
    # 0.625 degree longitude), so a tile replaces one request per facility.
    tiles: dict[tuple[int, int], list[Facility]] = {}
    for facility in facilities_to_load:
        tile = (math.floor(facility.latitude / 10), math.floor(facility.longitude / 10))
        tiles.setdefault(tile, []).append(facility)

    def fetch_region(tile: tuple[int, int], parameter: str) -> tuple[tuple[int, int], str, list[tuple[float, float, dict[str, float]]]]:
        latitude_min, longitude_min = tile[0] * 10, tile[1] * 10
        request = {
            "parameters": parameter,
            "community": "RE",
            "longitude-min": longitude_min,
            "longitude-max": longitude_min + 10,
            "latitude-min": latitude_min,
            "latitude-max": latitude_min + 10,
            "start": min(years),
            "end": max(years),
            "format": "JSON",
        }
        for attempt in range(5):
            response = requests.get(NASA_POWER_MONTHLY_URL, params=request, timeout=(15, 60))
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                features = response.json().get("features", [])
                values = []
                for feature in features:
                    coordinates = feature.get("geometry", {}).get("coordinates", [])
                    parameters_data = feature.get("properties", {}).get("parameter", {})
                    if len(coordinates) >= 2 and parameter in parameters_data:
                        values.append((float(coordinates[1]), float(coordinates[0]), parameters_data[parameter]))
                if not values:
                    raise RuntimeError(f"NASA POWER regional response contained no {parameter} data for tile {tile}")
                return tile, parameter, values
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            time.sleep(min(delay, 30))
        response.raise_for_status()
        raise RuntimeError(f"NASA POWER regional request failed after retries for tile {tile}")

    regional_values: dict[tuple[int, int], dict[str, list[tuple[float, float, dict[str, float]]]]] = {}
    requests_to_make = [(tile, parameter) for tile in tiles for parameter in parameters.split(",")]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch_region, tile, parameter) for tile, parameter in requests_to_make]
        for completed, future in enumerate(as_completed(futures), start=1):
            tile, parameter, values = future.result()
            regional_values.setdefault(tile, {})[parameter] = values
            if completed % 25 == 0:
                print(f"NASA POWER regional weather: {completed}/{len(futures)} requests")

    def fetch(facility: Facility) -> tuple[int, dict[str, dict[str, float]], float, float]:
        tile = (math.floor(facility.latitude / 10), math.floor(facility.longitude / 10))
        tile_values = regional_values[tile]
        grid = tile_values["T2M"]
        nearest = min(grid, key=lambda point: (point[0] - facility.latitude) ** 2 + (point[1] - facility.longitude) ** 2)
        grid_latitude, grid_longitude = nearest[:2]
        values = {}
        for parameter in parameters.split(","):
            parameter_values = min(
                tile_values[parameter],
                key=lambda point: (point[0] - grid_latitude) ** 2 + (point[1] - grid_longitude) ** 2,
            )
            values[parameter] = parameter_values[2]
        return facility.facility_id, values, grid_latitude, grid_longitude

    def annual_rows(facility_id: int, values: dict[str, dict[str, float]], grid_latitude: float, grid_longitude: float) -> list[WeatherAnnualRecord]:
        facility = facilities[facility_id]
        rows: list[WeatherAnnualRecord] = []
        for year in years:
            keys = [f"{year}{month:02d}" for month in range(1, 13)]
            averages = [values["T2M"][key] for key in keys if values["T2M"].get(key, -999) > -900]
            maximums = [values["T2M_MAX"][key] for key in keys if values["T2M_MAX"].get(key, -999) > -900]
            minimums = [values["T2M_MIN"][key] for key in keys if values["T2M_MIN"].get(key, -999) > -900]
            precipitation = [values["PRECTOTCORR"][key] for key in keys if values["PRECTOTCORR"].get(key, -999) > -900]
            winds = [values["WS2M"][key] for key in keys if values["WS2M"].get(key, -999) > -900]
            if not averages:
                continue
            cdd = sum(max(value - 18.3, 0) for value in averages)
            hdd = sum(max(18.3 - value, 0) for value in averages)
            rows.append(WeatherAnnualRecord(
                facility_id=facility.facility_id,
                reporting_year=year,
                average_temperature=sum(averages) / len(averages),
                maximum_temperature=max(maximums) if maximums else None,
                minimum_temperature=min(minimums) if minimums else None,
                precipitation_total=sum(value * 365 / 12 for value in precipitation) if precipitation else None,
                snowfall_total=None,
                wind_speed_average=sum(winds) / len(winds) if winds else None,
                cooling_degree_days=cdd * 365 / 12,
                heating_degree_days=hdd * 365 / 12,
                extreme_heat_days=sum(1 for value in maximums if value >= 35),
                extreme_heat_threshold=35,
                source_name="NASA POWER",
                measurement_unit_metadata={"temperature": "C", "precipitation": "mm/day converted to annual mm", "wind_speed": "m/s", "degree_day_base": "18.3 C", "source_resolution": "monthly regional grid", "facility_latitude": facility.latitude, "facility_longitude": facility.longitude, "grid_latitude": grid_latitude, "grid_longitude": grid_longitude},
            ))
        return rows

    loaded = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch, facility) for facility in facilities_to_load]
        for completed, future in enumerate(as_completed(futures), start=1):
            facility_id, values, grid_latitude, grid_longitude = future.result()
            rows = [row for row in annual_rows(facility_id, values, grid_latitude, grid_longitude) if (row.facility_id, row.reporting_year) not in existing]
            session.bulk_insert_mappings(
                WeatherAnnualRecord,
                [{key: value for key, value in row.__dict__.items() if not key.startswith("_")} for row in rows],
            )
            existing.update((row.facility_id, row.reporting_year) for row in rows)
            loaded += len(rows)
            if completed % 100 == 0:
                session.commit()
                print(f"NASA POWER annual weather: {completed}/{len(futures)} facilities, {loaded} rows")
    session.commit()
    return loaded


def seed_indicators(session: Session) -> int:
    definitions = [
        ("CO2 per MWh", "CO2 mass divided by gross load", "kg/MWh"),
        ("average temperature", "Mean daily station temperature", "deg C"),
        ("CDD", "Cooling degree days using base temperature 18.3 C", "deg C days"),
        ("HDD", "Heating degree days using base temperature 18.3 C", "deg C days"),
        ("extreme heat day count", "Count of days above a documented threshold", "days"),
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
    parser.add_argument("--years", default="2023", help="Comma-separated NASA POWER years")
    parser.add_argument("--nasa-weather", action="store_true")
    parser.add_argument("--weather-workers", type=int, default=6)
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
        if args.nasa_weather:
            years = [int(year.strip()) for year in args.years.split(",")]
            print(f"NASA POWER weather: {load_nasa_power_annual_weather(session, years, args.weather_workers)} records")
        print(f"Indicators: {seed_indicators(session)} inserted")


if __name__ == "__main__":
    main()