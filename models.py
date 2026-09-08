"""SQLAlchemy models for the epaData course project.

The models use SQLAlchemy 2.x declarative typing and are intentionally kept in
one module while the project is small. Split them by domain when the Flask app
needs blueprints or migrations.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index("ix_datasets_source_year", "data_source", "reporting_year"),
        Index("ix_datasets_retrieval_date", "retrieval_or_upload_date"),
        Index("ix_datasets_status", "status"),
    )

    dataset_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    data_source: Mapped[str] = mapped_column(String(100), nullable=False)
    reporting_year: Mapped[int | None] = mapped_column(Integer)
    retrieval_or_upload_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    number_of_raw_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    number_of_accepted_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    number_of_rejected_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    notes: Mapped[str | None] = mapped_column(Text)

    annual_records: Mapped[list[AnnualRecord]] = relationship(back_populates="dataset")
    daily_power_records: Mapped[list[DailyPowerRecord]] = relationship(back_populates="dataset")
    uploaded_files: Mapped[list[UploadedFile]] = relationship(back_populates="dataset")
    provenance: Mapped[list[DataProvenance]] = relationship(back_populates="dataset")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="dataset")


class Facility(Base):
    __tablename__ = "facilities"
    __table_args__ = (
        UniqueConstraint("epa_facility_id", name="uq_facilities_epa_id"),
        Index("ix_facilities_state_county", "state", "county"),
        Index("ix_facilities_name", "facility_name"),
    )

    facility_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epa_facility_id: Mapped[str] = mapped_column(String(30), nullable=False)
    facility_name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    county: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    units: Mapped[list[Unit]] = relationship(back_populates="facility")
    weather_links: Mapped[list[WeatherFacilityLink]] = relationship(back_populates="facility")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="facility")
    score_results: Mapped[list[ScoreResult]] = relationship(back_populates="facility")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("facility_id", "epa_unit_id", name="uq_units_facility_epa_unit"),
        Index("ix_units_type", "unit_type"),
        Index("ix_units_primary_fuel", "primary_fuel"),
        Index("ix_units_secondary_fuel", "secondary_fuel"),
    )

    unit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.facility_id", ondelete="RESTRICT"), nullable=False)
    epa_unit_id: Mapped[str] = mapped_column(String(50), nullable=False)
    unit_type: Mapped[str | None] = mapped_column(String(100))
    primary_fuel: Mapped[str | None] = mapped_column(String(100))
    secondary_fuel: Mapped[str | None] = mapped_column(String(100))
    operating_date: Mapped[date | None] = mapped_column(Date)
    retirement_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    facility: Mapped[Facility] = relationship(back_populates="units")
    annual_records: Mapped[list[AnnualRecord]] = relationship(back_populates="unit")
    daily_power_records: Mapped[list[DailyPowerRecord]] = relationship(back_populates="unit")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="unit")
    score_results: Mapped[list[ScoreResult]] = relationship(back_populates="unit")


class AnnualRecord(Base):
    __tablename__ = "annual_records"
    __table_args__ = (
        UniqueConstraint("unit_id", "reporting_year", "dataset_id", name="uq_annual_unit_year_dataset"),
        CheckConstraint("reporting_year BETWEEN 1900 AND 2200", name="ck_annual_year"),
        Index("ix_annual_year_unit", "reporting_year", "unit_id"),
        Index("ix_annual_dataset_year", "dataset_id", "reporting_year"),
        Index("ix_annual_co2", "co2_mass"),
        Index("ix_annual_so2", "so2_mass"),
        Index("ix_annual_nox", "nox_mass"),
        Index("ix_annual_gross_load", "gross_load"),
        Index("ix_annual_heat_input", "heat_input"),
        Index("ix_annual_operating_time", "operating_time"),
    )

    annual_record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.unit_id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="RESTRICT"), nullable=False)
    reporting_year: Mapped[int] = mapped_column(Integer, nullable=False)
    operating_time: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    gross_load: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    steam_load: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    heat_input: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    co2_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    so2_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    nox_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    so2_control_information: Mapped[str | None] = mapped_column(String(255))
    nox_control_information: Mapped[str | None] = mapped_column(String(255))
    pm_control_information: Mapped[str | None] = mapped_column(String(255))
    program_code: Mapped[str | None] = mapped_column(String(100))
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    unit: Mapped[Unit] = relationship(back_populates="annual_records")
    dataset: Mapped[Dataset] = relationship(back_populates="annual_records")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="annual_record")


class TraciFactor(Base):
    __tablename__ = "traci_factors"
    __table_args__ = (
        UniqueConstraint("traci_version", "pollutant", "impact_category", "geography", name="uq_traci_factor_version_category"),
        Index("ix_traci_category_pollutant", "impact_category", "pollutant"),
        Index("ix_traci_version", "traci_version"),
    )

    traci_factor_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pollutant: Mapped[str] = mapped_column(String(100), nullable=False)
    impact_category: Mapped[str] = mapped_column(String(100), nullable=False)
    characterization_factor: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    factor_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_flow_unit: Mapped[str | None] = mapped_column(String(100))
    traci_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    geography: Mapped[str | None] = mapped_column(String(100))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    indicator_definitions: Mapped[list[IndicatorDefinition]] = relationship(back_populates="traci_factor")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        UniqueConstraint("content_sha256", name="uq_uploaded_file_sha256"),
        Index("ix_uploaded_files_status_date", "validation_status", "upload_date"),
        Index("ix_uploaded_files_dataset", "dataset_id"),
    )

    uploaded_file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="RESTRICT"))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    upload_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    number_of_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    number_of_accepted_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    number_of_rejected_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    dataset: Mapped[Dataset | None] = relationship(back_populates="uploaded_files")
    validation_errors: Mapped[list[UploadValidationError]] = relationship(back_populates="uploaded_file", cascade="all, delete-orphan")
    provenance: Mapped[list[DataProvenance]] = relationship(back_populates="uploaded_file")


class UploadValidationError(Base):
    __tablename__ = "upload_validation_errors"
    __table_args__ = (
        UniqueConstraint("uploaded_file_id", "source_row_number", "column_name", "error_code", name="uq_upload_error_location"),
        Index("ix_upload_errors_file_row", "uploaded_file_id", "source_row_number"),
        Index("ix_upload_errors_code", "error_code"),
    )

    validation_error_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.uploaded_file_id", ondelete="CASCADE"), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str] = mapped_column(String(50), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    raw_row_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    uploaded_file: Mapped[UploadedFile] = relationship(back_populates="validation_errors")


class DataProvenance(Base):
    __tablename__ = "data_provenance"
    __table_args__ = (
        Index("ix_provenance_source_date", "source_name", "retrieval_date"),
        Index("ix_provenance_dataset", "dataset_id"),
        Index("ix_provenance_file", "uploaded_file_id"),
    )

    provenance_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="RESTRICT"), nullable=False)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.uploaded_file_id", ondelete="RESTRICT"))
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url_or_api: Mapped[str | None] = mapped_column(Text)
    retrieval_method: Mapped[str] = mapped_column(String(50), nullable=False)
    retrieval_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query_parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reporting_year: Mapped[int | None] = mapped_column(Integer)
    source_version: Mapped[str | None] = mapped_column(String(100))
    checksum: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    dataset: Mapped[Dataset] = relationship(back_populates="provenance")
    uploaded_file: Mapped[UploadedFile | None] = relationship(back_populates="provenance")


class WeatherStation(Base):
    __tablename__ = "weather_stations"
    __table_args__ = (
        UniqueConstraint("ncei_station_id", name="uq_weather_station_ncei_id"),
        Index("ix_weather_stations_state_county", "state", "county"),
        Index("ix_weather_stations_coordinates", "latitude", "longitude"),
    )

    weather_station_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ncei_station_id: Mapped[str] = mapped_column(String(50), nullable=False)
    station_name: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str | None] = mapped_column(String(2))
    county: Mapped[str | None] = mapped_column(String(100))
    elevation_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    network: Mapped[str | None] = mapped_column(String(100))
    source_name: Mapped[str] = mapped_column(String(100), nullable=False, default="NOAA NCEI")
    active_from: Mapped[date | None] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)

    records: Mapped[list[WeatherRecord]] = relationship(back_populates="weather_station")
    facility_links: Mapped[list[WeatherFacilityLink]] = relationship(back_populates="weather_station")


class WeatherRecord(Base):
    __tablename__ = "weather_records"
    __table_args__ = (
        UniqueConstraint("weather_station_id", "observation_date", name="uq_weather_station_date"),
        Index("ix_weather_records_station_date", "weather_station_id", "observation_date"),
        Index("ix_weather_records_date", "observation_date"),
        Index("ix_weather_records_avg_temp", "average_temperature"),
        Index("ix_weather_records_max_temp", "maximum_temperature"),
        Index("ix_weather_records_cdd", "cooling_degree_days"),
        Index("ix_weather_records_extreme_heat", "is_extreme_heat"),
    )

    weather_record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weather_station_id: Mapped[int] = mapped_column(ForeignKey("weather_stations.weather_station_id", ondelete="RESTRICT"), nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    average_temperature: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    maximum_temperature: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    minimum_temperature: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    precipitation: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    snowfall: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    wind_speed: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    relative_humidity: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    pressure: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    cooling_degree_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    heating_degree_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    is_extreme_heat: Mapped[bool | None] = mapped_column(Boolean)
    extreme_heat_threshold: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    source_name: Mapped[str] = mapped_column(String(100), nullable=False, default="NOAA NCEI")
    source_record_id: Mapped[str | None] = mapped_column(String(100))
    measurement_unit_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_flags: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    weather_station: Mapped[WeatherStation] = relationship(back_populates="records")


class WeatherFacilityLink(Base):
    __tablename__ = "weather_facility_links"
    __table_args__ = (
        UniqueConstraint("facility_id", "weather_station_id", "valid_from", "valid_to", name="uq_facility_station_period"),
        Index("ix_weather_links_facility_primary", "facility_id", "is_primary"),
        Index("ix_weather_links_station", "weather_station_id"),
        Index("ix_weather_links_distance", "distance_km"),
    )

    weather_facility_link_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.facility_id", ondelete="RESTRICT"), nullable=False)
    weather_station_id: Mapped[int] = mapped_column(ForeignKey("weather_stations.weather_station_id", ondelete="RESTRICT"), nullable=False)
    link_type: Mapped[str] = mapped_column(String(30), nullable=False)
    distance_km: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selection_method: Mapped[str] = mapped_column(String(100), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    facility: Mapped[Facility] = relationship(back_populates="weather_links")
    weather_station: Mapped[WeatherStation] = relationship(back_populates="facility_links")


class DailyPowerRecord(Base):
    __tablename__ = "daily_power_records"
    __table_args__ = (
        UniqueConstraint("unit_id", "observation_date", "dataset_id", name="uq_daily_unit_date_dataset"),
        Index("ix_daily_power_unit_date", "unit_id", "observation_date"),
        Index("ix_daily_power_dataset_date", "dataset_id", "observation_date"),
        Index("ix_daily_power_co2", "co2_mass"),
    )

    daily_record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.unit_id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="RESTRICT"), nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    operating_time: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    gross_load: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    heat_input: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    co2_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    so2_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    nox_mass: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    source_row_number: Mapped[int | None] = mapped_column(Integer)

    unit: Mapped[Unit] = relationship(back_populates="daily_power_records")
    dataset: Mapped[Dataset] = relationship(back_populates="daily_power_records")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="daily_record")


class IndicatorDefinition(Base):
    __tablename__ = "indicator_definitions"
    __table_args__ = (
        UniqueConstraint("indicator_name", "version", name="uq_indicator_name_version"),
        Index("ix_indicators_category", "impact_category"),
        Index("ix_indicators_active", "is_active"),
    )

    indicator_definition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    indicator_name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    impact_category: Mapped[str | None] = mapped_column(String(100))
    formula_expression: Mapped[str] = mapped_column(Text, nullable=False)
    output_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    traci_factor_id: Mapped[int | None] = mapped_column(ForeignKey("traci_factors.traci_factor_id", ondelete="RESTRICT"))
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    traci_factor: Mapped[TraciFactor | None] = relationship(back_populates="indicator_definitions")
    calculated_indicators: Mapped[list[CalculatedIndicator]] = relationship(back_populates="indicator_definition")
    scenario_weights: Mapped[list[ScenarioWeight]] = relationship(back_populates="indicator_definition")


class CalculatedIndicator(Base):
    __tablename__ = "calculated_indicators"
    __table_args__ = (
        CheckConstraint(
            "annual_record_id IS NOT NULL OR daily_record_id IS NOT NULL OR facility_id IS NOT NULL OR unit_id IS NOT NULL",
            name="ck_indicator_has_subject",
        ),
        CheckConstraint("period_end >= period_start", name="ck_indicator_period_order"),
        Index("ix_calculated_indicator_definition_period", "indicator_definition_id", "period_start", "period_end"),
        Index("ix_calculated_indicator_facility_period", "facility_id", "period_start"),
        Index("ix_calculated_indicator_unit_period", "unit_id", "period_start"),
        Index("ix_calculated_indicator_dataset", "dataset_id"),
    )

    calculated_indicator_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    indicator_definition_id: Mapped[int] = mapped_column(ForeignKey("indicator_definitions.indicator_definition_id", ondelete="RESTRICT"), nullable=False)
    facility_id: Mapped[int | None] = mapped_column(ForeignKey("facilities.facility_id", ondelete="RESTRICT"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.unit_id", ondelete="RESTRICT"))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.dataset_id", ondelete="RESTRICT"))
    annual_record_id: Mapped[int | None] = mapped_column(ForeignKey("annual_records.annual_record_id", ondelete="RESTRICT"))
    daily_record_id: Mapped[int | None] = mapped_column(ForeignKey("daily_power_records.daily_record_id", ondelete="RESTRICT"))
    period_grain: Mapped[str] = mapped_column(String(30), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    output_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    calculation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    indicator_definition: Mapped[IndicatorDefinition] = relationship(back_populates="calculated_indicators")
    facility: Mapped[Facility | None] = relationship(back_populates="calculated_indicators")
    unit: Mapped[Unit | None] = relationship(back_populates="calculated_indicators")
    dataset: Mapped[Dataset | None] = relationship(back_populates="calculated_indicators")
    annual_record: Mapped[AnnualRecord | None] = relationship(back_populates="calculated_indicators")
    daily_record: Mapped[DailyPowerRecord | None] = relationship(back_populates="calculated_indicators")


class WeightScenario(Base):
    __tablename__ = "weight_scenarios"
    __table_args__ = (Index("ix_weight_scenarios_active", "is_active"),)

    weight_scenario_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    weights: Mapped[list[ScenarioWeight]] = relationship(back_populates="weight_scenario", cascade="all, delete-orphan")
    score_results: Mapped[list[ScoreResult]] = relationship(back_populates="weight_scenario")


class ScenarioWeight(Base):
    __tablename__ = "scenario_weights"
    __table_args__ = (
        UniqueConstraint("weight_scenario_id", "indicator_definition_id", name="uq_scenario_indicator"),
        CheckConstraint("weight >= 0", name="ck_scenario_weight_nonnegative"),
        Index("ix_scenario_weights_scenario", "weight_scenario_id"),
        Index("ix_scenario_weights_indicator", "indicator_definition_id"),
    )

    scenario_weight_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weight_scenario_id: Mapped[int] = mapped_column(ForeignKey("weight_scenarios.weight_scenario_id", ondelete="CASCADE"), nullable=False)
    indicator_definition_id: Mapped[int] = mapped_column(ForeignKey("indicator_definitions.indicator_definition_id", ondelete="RESTRICT"), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    normalization_method: Mapped[str | None] = mapped_column(String(100))

    weight_scenario: Mapped[WeightScenario] = relationship(back_populates="weights")
    indicator_definition: Mapped[IndicatorDefinition] = relationship(back_populates="scenario_weights")


class ScoreResult(Base):
    __tablename__ = "score_results"
    __table_args__ = (
        CheckConstraint("facility_id IS NOT NULL OR unit_id IS NOT NULL", name="ck_score_has_subject"),
        CheckConstraint("reporting_year IS NOT NULL OR observation_date IS NOT NULL", name="ck_score_has_period"),
        Index("ix_score_results_facility_year", "facility_id", "reporting_year"),
        Index("ix_score_results_unit_date", "unit_id", "observation_date"),
        Index("ix_score_results_scenario", "weight_scenario_id"),
    )

    score_result_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weight_scenario_id: Mapped[int] = mapped_column(ForeignKey("weight_scenarios.weight_scenario_id", ondelete="RESTRICT"), nullable=False)
    facility_id: Mapped[int | None] = mapped_column(ForeignKey("facilities.facility_id", ondelete="RESTRICT"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.unit_id", ondelete="RESTRICT"))
    reporting_year: Mapped[int | None] = mapped_column(Integer)
    observation_date: Mapped[date | None] = mapped_column(Date)
    score: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    weight_scenario: Mapped[WeightScenario] = relationship(back_populates="score_results")
    facility: Mapped[Facility | None] = relationship(back_populates="score_results")
    unit: Mapped[Unit | None] = relationship(back_populates="score_results")


def initialize_database(database_url: str = "sqlite:///epa_data.db") -> Any:
    """Create tables for a course-project bootstrap.

    Use Alembic migrations once the schema is shared or data must be preserved.
    """
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return engine
