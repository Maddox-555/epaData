"""Populate the normalized source tables from official EPA and NOAA files."""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
from datetime import date, datetime, timezone
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
    WeatherFacilityLink,
    WeatherRecord,
    WeatherStation,
    WeightScenario,
)


NOAA_STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
NOAA_STATION_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/{station}.csv"
NOAA_DLY_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/{station}.dly"
TRACI_URL = "https://www.epa.gov/system/files/documents/2024-01/traci_2_2.xlsx"
EPA_FACILITIES_URL = "https://api.epa.gov/easey/facilities-mgmt/facilities/attributes"
EPA_ANNUAL_EMISSIONS_URL = "https://api.epa.gov/easey/emissions-mgmt/emissions/apportioned/annual"


def _download(url: str, headers: dict[str, str] | None = None) -> bytes:
    response = requests.get(url, headers=headers or {}, timeout=120)
    response.raise_for_status()
    return response.content


def _noaa_station_frame(station_id: str) -> pd.DataFrame | None:
    csv_url = NOAA_STATION_URL.format(station=station_id)
    response = requests.get(csv_url, timeout=120)
    if response.status_code == 200:
        return pd.read_csv(io.BytesIO(response.content), header=None, names=["station", "date", "element", "value", "mflag", "qflag", "sflag", "obstime"], dtype=str)
    if response.status_code != 404:
        response.raise_for_status()
    dly_response = requests.get(NOAA_DLY_URL.format(station=station_id), timeout=120)
    if dly_response.status_code == 404:
        return None
    dly_response.raise_for_status()
    rows: list[dict[str, str]] = []
    for line in dly_response.text.splitlines():
        if len(line) < 269:
            continue
        station, year, month, element = line[0:11], line[11:15], line[15:17], line[17:21]
        for day in range(1, 32):
            offset = 21 + (day - 1) * 8
            rows.append({
                "station": station,
                "date": f"{year}{month}{day:02d}",
                "element": element,
                "value": line[offset:offset + 5].strip(),
                "mflag": line[offset + 5:offset + 6].strip(),
                "qflag": line[offset + 6:offset + 7].strip(),
                "sflag": line[offset + 7:offset + 8].strip(),
                "obstime": "",
            })
    return pd.DataFrame(rows)


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


def load_noaa_stations(session: Session, content: bytes | None = None, url: str = NOAA_STATIONS_URL) -> int:
    content = content or _download(url)
    lines = [line for line in content.decode("ascii", errors="replace").splitlines() if len(line) >= 71]
    _source_dataset(session, "ghcnd-stations.txt", "NOAA NCEI GHCN-Daily", url, len(lines))
    inserted = 0
    for line in lines:
        if len(line) < 71:
            continue
        station_id = line[0:11].strip()
        if not station_id or session.scalar(select(WeatherStation).where(WeatherStation.ncei_station_id == station_id)):
            continue
        try:
            latitude, longitude, elevation = float(line[12:20]), float(line[21:30]), float(line[31:37])
        except ValueError:
            continue
        session.add(WeatherStation(
            ncei_station_id=station_id, latitude=latitude, longitude=longitude,
            elevation_m=None if elevation <= -999 else elevation,
            state=line[38:40].strip() or None, station_name=line[41:71].strip() or None,
            network=station_id[2], source_name="NOAA NCEI GHCN-Daily",
        ))
        inserted += 1
    session.commit()
    return inserted


def _nearest_station(session: Session, facility: Facility) -> tuple[WeatherStation, float] | None:
    if facility.latitude is None or facility.longitude is None:
        return None
    stations = session.scalars(select(WeatherStation)).all()
    if not stations:
        return None
    selected = min(stations, key=lambda station: (station.latitude - facility.latitude) ** 2 + (station.longitude - facility.longitude) ** 2)
    distance = 111.2 * math.sqrt((selected.latitude - facility.latitude) ** 2 + ((selected.longitude - facility.longitude) * math.cos(math.radians(facility.latitude))) ** 2)
    return selected, distance


def load_noaa_facility_weather(session: Session, years: list[int], station_limit: int | None = None) -> int:
    facilities = session.scalars(select(Facility).where(Facility.latitude.is_not(None), Facility.longitude.is_not(None))).all()
    stations = session.scalars(select(WeatherStation)).all()
    if not stations:
        return 0
    stations_by_state: dict[str | None, list[WeatherStation]] = {}
    for station in stations:
        stations_by_state.setdefault(station.state, []).append(station)
    existing_links = {
        (link.facility_id, link.weather_station_id)
        for link in session.scalars(select(WeatherFacilityLink)).all()
    }
    station_facilities: dict[int, list[Facility]] = {}
    for facility in facilities:
        candidates = stations_by_state.get(facility.state) or stations
        nearest = min(candidates, key=lambda station: (station.latitude - facility.latitude) ** 2 + (station.longitude - facility.longitude) ** 2)
        distance = 111.2 * math.sqrt((nearest.latitude - facility.latitude) ** 2 + ((nearest.longitude - facility.longitude) * math.cos(math.radians(facility.latitude))) ** 2)
        station_facilities.setdefault(nearest.weather_station_id, []).append(facility)
        if (facility.facility_id, nearest.weather_station_id) not in existing_links:
            session.add(WeatherFacilityLink(
                facility_id=facility.facility_id, weather_station_id=nearest.weather_station_id,
                link_type="nearest", distance_km=distance, is_primary=True,
                selection_method="nearest station by haversine distance",
                created_at=datetime.now(timezone.utc),
            ))
            existing_links.add((facility.facility_id, nearest.weather_station_id))
    session.commit()
    loaded = 0
    for station_id in list(station_facilities)[:station_limit] if station_limit is not None else station_facilities:
        station = next(station for station in stations if station.weather_station_id == station_id)
        if not station:
            break
        frame = _noaa_station_frame(station.ncei_station_id)
        if frame is None:
            print(f"NOAA {station.ncei_station_id}: no station file available")
            continue
        frame = frame[frame["date"].str[:4].astype(int).isin(years)]
        grouped: dict[str, dict[str, Any]] = {}
        for _, row in frame.iterrows():
            if row["qflag"] == "X" or row["value"] in {"-9999", "nan"}:
                continue
            grouped.setdefault(row["date"], {})[row["element"]] = row
        for day, values in grouped.items():
            def val(element: str, scale: float = 10.0) -> float | None:
                item = values.get(element)
                return None if item is None else float(item["value"]) / scale
            avg = val("TAVG")
            if avg is None and val("TMAX") is not None and val("TMIN") is not None:
                avg = (val("TMAX") + val("TMIN")) / 2
            record_date = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
            exists = session.scalar(select(WeatherRecord).where(WeatherRecord.weather_station_id == station.weather_station_id, WeatherRecord.observation_date == record_date))
            if exists:
                continue
            session.add(WeatherRecord(
                weather_station_id=station.weather_station_id, observation_date=record_date,
                average_temperature=avg, maximum_temperature=val("TMAX"), minimum_temperature=val("TMIN"),
                precipitation=val("PRCP", 10), snowfall=val("SNOW"), wind_speed=val("AWND", 10),
                cooling_degree_days=None if avg is None else max(avg - 18.3, 0),
                heating_degree_days=None if avg is None else max(18.3 - avg, 0),
                is_extreme_heat=None if val("TMAX") is None else val("TMAX") >= 35,
                extreme_heat_threshold=35 if val("TMAX") is not None else None,
                source_name="NOAA NCEI GHCN-Daily", source_record_id=f"{station.ncei_station_id}:{day}",
                measurement_unit_metadata={"temperature": "C", "precipitation": "mm", "snowfall": "mm", "wind_speed": "m/s", "degree_day_base": "18.3 C", "extreme_heat_threshold": "35 C TMAX"},
                quality_flags={key: item.get("qflag", "") for key, item in values.items()},
            ))
            loaded += 1
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
    parser.add_argument("--years", default="2023", help="Comma-separated NOAA years")
    parser.add_argument("--noaa-weather", action="store_true")
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
        print(f"NOAA stations: {load_noaa_stations(session)} inserted")
        if args.noaa_weather:
            years = [int(year.strip()) for year in args.years.split(",")]
            print(f"NOAA weather: {load_noaa_facility_weather(session, years)} records")
        print(f"Indicators: {seed_indicators(session)} inserted")


if __name__ == "__main__":
    main()