"""Flask JSON API for epaData data ingestion and display."""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, send_file
from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from data_service import EPA_CAMPD_ANNUAL_URL, SOURCE_DEFINITIONS, RANGE_FILTERS, annual_filters, approve_uploaded_dataset, build_annual_query, cancel_uploaded_dataset, count_annual_records, ingest_dataframe, normalized_search_args, read_dataframe, retrieve_campd_dataframe, retrieve_dataframe
from models import AnnualRecord, Base, DataProvenance, Dataset, Facility, Unit, UploadValidationError


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _record_dict(record: AnnualRecord, unit: Unit, facility: Facility) -> dict[str, Any]:
    return {
        "annual_record_id": record.annual_record_id,
        "facility_id": facility.epa_facility_id,
        "facility_name": facility.facility_name,
        "state": facility.state,
        "county": facility.county,
        "unit_id": unit.epa_unit_id,
        "unit_type": unit.unit_type,
        "primary_fuel": unit.primary_fuel,
        "secondary_fuel": unit.secondary_fuel,
        "reporting_year": record.reporting_year,
        "operating_time": _json_value(record.operating_time),
        "gross_load": _json_value(record.gross_load),
        "steam_load": _json_value(record.steam_load),
        "heat_input": _json_value(record.heat_input),
        "co2_mass": _json_value(record.co2_mass),
        "so2_mass": _json_value(record.so2_mass),
        "nox_mass": _json_value(record.nox_mass),
        "so2_control_information": record.so2_control_information,
        "nox_control_information": record.nox_control_information,
        "pm_control_information": record.pm_control_information,
        "program_code": record.program_code,
    }


def _csv_file(rows: list[dict[str, Any]], filename: str) -> Any:
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name=filename)


def _dataset_query(dataset_id: int) -> Any:
    return select(AnnualRecord, Unit, Facility).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility).where(AnnualRecord.dataset_id == dataset_id).order_by(AnnualRecord.reporting_year, Facility.facility_name, Unit.epa_unit_id)


def _annual_base_query_for_unit(facility_id: str, unit_id: str) -> Any:
    return select(AnnualRecord, Unit, Facility).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility).where(Facility.epa_facility_id == facility_id, Unit.epa_unit_id == unit_id).order_by(AnnualRecord.reporting_year)


def create_app(database_url: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DATABASE_URL"] = database_url or "sqlite:///epa_data.db"
    app.config["UPLOAD_FOLDER"] = "instance/uploads"
    engine = create_engine(app.config["DATABASE_URL"], future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    app.extensions["epa_engine"] = engine
    app.extensions["epa_sessionmaker"] = SessionLocal

    @app.get("/")
    def index() -> Any:
        return render_template("index.html")

    @app.get("/unit/<facility_id>/<unit_id>")
    def unit_detail_page(facility_id: str, unit_id: str) -> Any:
        return render_template("unit_detail.html", facility_id=facility_id, unit_id=unit_id)

    @app.get("/api")
    def api_index() -> Any:
        return jsonify({
            "service": "epaData",
            "status": "ok",
            "message": "EPA data backend is running. Use the API endpoints below.",
            "endpoints": {
                "health": "/api/health",
                "sources": "/api/sources",
                "datasets": "/api/datasets",
                "facilities": "/api/facilities",
                "annual_records": "/api/annual-records",
                "annual_records_csv": "/api/annual-records.csv",
                "upload": "POST /api/data/upload",
                "retrieve": "POST /api/data/retrieve",
            },
        })

    @app.get("/favicon.ico")
    def favicon() -> tuple[str, int]:
        return "", 204

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok", "service": "epaData"})

    @app.get("/api/sources")
    def sources() -> Any:
        return jsonify(SOURCE_DEFINITIONS)

    @app.get("/api/datasets")
    def datasets() -> Any:
        with SessionLocal() as session:
            rows = session.scalars(select(Dataset).order_by(Dataset.retrieval_or_upload_date.desc())).all()
            return jsonify([
                {
                    "dataset_id": row.dataset_id,
                    "dataset_name": row.dataset_name,
                    "data_source": row.data_source,
                    "reporting_year": row.reporting_year,
                    "status": row.status,
                    "raw_records": row.number_of_raw_records,
                    "accepted_records": row.number_of_accepted_records,
                    "rejected_records": row.number_of_rejected_records,
                }
                for row in rows
            ])

    @app.post("/api/data/upload")
    def upload_data() -> Any:
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "multipart field 'file' is required"}), 400
        content = uploaded.read()
        try:
            frame = read_dataframe(content, uploaded.filename)
            approve = request.args.get("approve", "false").lower() == "true"
            with SessionLocal() as session:
                result = ingest_dataframe(session, frame, filename=uploaded.filename, source_name="EPA CAMPD upload", content=content, approve=approve, storage_dir=app.config["UPLOAD_FOLDER"])
            return jsonify(result), 200 if result["status"] in {"imported", "pending"} else 422
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            app.logger.exception("Upload failed")
            return jsonify({"error": "Upload could not be processed", "detail": str(exc)}), 500

    @app.post("/api/data/upload/<int:dataset_id>/approve")
    def approve_upload(dataset_id: int) -> Any:
        try:
            with SessionLocal() as session:
                result = approve_uploaded_dataset(session, dataset_id)
            return jsonify(result), 200 if result["status"] == "imported" else 422
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/data/upload/<int:dataset_id>/cancel")
    def cancel_upload(dataset_id: int) -> Any:
        try:
            with SessionLocal() as session:
                return jsonify(cancel_uploaded_dataset(session, dataset_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/data/retrieve")
    def retrieve_data() -> Any:
        payload = request.get_json(silent=True) or {}
        url = payload.get("url")
        if not url:
            return jsonify({"error": "JSON field 'url' is required"}), 400
        try:
            params = payload.get("params") or {}
            frame, content = retrieve_dataframe(url, params=params)
            approve = bool(payload.get("approve", False))
            filename = payload.get("filename") or Path(url.split("?")[0]).name or "retrieved.csv"
            with SessionLocal() as session:
                result = ingest_dataframe(session, frame, filename=filename, source_name=payload.get("source_name", "EPA CAMPD retrieval"), approve=approve, storage_dir=app.config["UPLOAD_FOLDER"], source_url_or_api=url, query_parameters=payload.get("provenance_params", params))
            return jsonify(result)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 502
            app.logger.warning("Remote retrieval rejected by source with status %s", status_code)
            return jsonify({"error": f"The remote data source rejected the request (HTTP {status_code}). Check the reporting year and filters."}), 502
        except requests.RequestException:
            app.logger.warning("Remote retrieval failed while contacting the source")
            return jsonify({"error": "The remote data source could not be reached."}), 502
        except Exception:
            app.logger.exception("Remote retrieval failed")
            return jsonify({"error": "Remote data could not be retrieved."}), 502

    @app.post("/api/data/retrieve/epa-campd")
    def retrieve_epa_campd() -> Any:
        payload = request.get_json(silent=True) or {}
        api_key = os.getenv("EPA_API_KEY")
        if not api_key:
            return jsonify({"error": "EPA_API_KEY is not configured on the server."}), 503
        params = dict(payload.get("params") or {})
        if "reporting_year" in params and "year" not in params:
            params["year"] = params["reporting_year"]
        if not params.get("year"):
            return jsonify({"error": "A reporting year is required for CAMPD annual retrieval."}), 400
        payload["provenance_params"] = dict(params)
        try:
            frame, content = retrieve_campd_dataframe(params, api_key)
            year = int(params["year"])
            with SessionLocal() as session:
                result = ingest_dataframe(session, frame, filename=f"campd-{year}.json", source_name=SOURCE_DEFINITIONS["epa-campd"]["name"], content=content, approve=bool(payload.get("approve", False)), storage_dir=app.config["UPLOAD_FOLDER"], source_url_or_api=EPA_CAMPD_ANNUAL_URL, query_parameters=params)
            return jsonify(result), 200 if result["status"] in {"imported", "pending"} else 422
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 502
            app.logger.warning("CAMPD retrieval rejected by source with status %s", status_code)
            return jsonify({"error": f"CAMPD rejected the request (HTTP {status_code}). Check the reporting year and filters."}), 502
        except requests.RequestException:
            app.logger.warning("CAMPD retrieval failed while contacting the source")
            return jsonify({"error": "CAMPD could not be reached."}), 502

    @app.get("/api/annual-records")
    def annual_records() -> Any:
        try:
            args = request.args.to_dict()
            limit = min(max(int(args.pop("limit", 100)), 1), 1000)
            offset = max(int(args.pop("offset", 0)), 0)
            with SessionLocal() as session:
                query = build_annual_query(args)
                total = count_annual_records(session, args)
                rows = session.execute(query.limit(limit).offset(offset)).all()
                data = [_record_dict(record, unit, facility) for record, unit, facility in rows]
                interpreted = normalized_search_args(args)
                interpreted.pop("limit", None)
                interpreted.pop("offset", None)
                return jsonify({"count": len(data), "total": total, "limit": limit, "offset": offset, "records": data, "interpreted_filters": interpreted})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": f"Invalid query parameter: {exc}"}), 400

    @app.get("/api/annual-records.csv")
    def annual_records_csv() -> Any:
        args = request.args.to_dict()
        args.pop("limit", None)
        args.pop("offset", None)
        try:
            query = build_annual_query(args)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": f"Invalid query parameter: {exc}"}), 400
        with SessionLocal() as session:
            rows = session.execute(query).all()
        records = [_record_dict(record, unit, facility) for record, unit, facility in rows]
        return _csv_file(records, "annual-records.csv")

    @app.get("/api/datasets/<int:dataset_id>.csv")
    def dataset_csv(dataset_id: int) -> Any:
        with SessionLocal() as session:
            query = _dataset_query(dataset_id)
            rows = session.execute(query).all()
        return _csv_file([_record_dict(record, unit, facility) for record, unit, facility in rows], f"dataset-{dataset_id}.csv")

    @app.get("/api/uploads/<int:dataset_id>/errors.csv")
    def upload_errors_csv(dataset_id: int) -> Any:
        with SessionLocal() as session:
            rows = session.scalars(select(UploadValidationError).join(UploadValidationError.uploaded_file).where(UploadValidationError.uploaded_file.has(dataset_id=dataset_id)).order_by(UploadValidationError.source_row_number)).all()
        records = [{"source_row_number": row.source_row_number, "column_name": row.column_name, "error_code": row.error_code, "error_message": row.error_message, "raw_value": row.raw_value} for row in rows]
        return _csv_file(records, f"dataset-{dataset_id}-validation-errors.csv")

    @app.get("/api/datasets/<int:dataset_id>/provenance.csv")
    def provenance_csv(dataset_id: int) -> Any:
        with SessionLocal() as session:
            rows = session.scalars(select(DataProvenance).where(DataProvenance.dataset_id == dataset_id).order_by(DataProvenance.retrieval_date)).all()
        records = [{"source_name": row.source_name, "source_url_or_api": row.source_url_or_api, "retrieval_method": row.retrieval_method, "retrieval_date": _json_value(row.retrieval_date), "query_parameters": row.query_parameters, "reporting_year": row.reporting_year, "source_version": row.source_version, "checksum": row.checksum} for row in rows]
        return _csv_file(records, f"dataset-{dataset_id}-provenance.csv")

    @app.get("/api/units/<facility_id>/<unit_id>")
    def unit_detail(facility_id: str, unit_id: str) -> Any:
        with SessionLocal() as session:
            rows = session.execute(_annual_base_query_for_unit(facility_id, unit_id)).all()
        if not rows:
            return jsonify({"error": "Unit was not found."}), 404
        facility = rows[0][2]
        unit = rows[0][1]
        return jsonify({"facility": {"facility_id": facility.epa_facility_id, "facility_name": facility.facility_name, "state": facility.state, "county": facility.county, "latitude": facility.latitude, "longitude": facility.longitude}, "unit": {"unit_id": unit.epa_unit_id, "unit_type": unit.unit_type, "primary_fuel": unit.primary_fuel, "secondary_fuel": unit.secondary_fuel, "operating_date": _json_value(unit.operating_date), "retirement_date": _json_value(unit.retirement_date)}, "records": [_record_dict(record, unit, facility) for record, unit, facility in rows]})

    @app.get("/api/rankings")
    def rankings() -> Any:
        group_by = request.args.get("group_by", "unit")
        metric = request.args.get("metric", "co2_mass")
        try:
            limit = min(max(int(request.args.get("limit", 10)), 1), 100)
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        if group_by not in {"facility", "state", "unit"}:
            return jsonify({"error": "group_by must be facility, state, or unit"}), 400
        if metric not in RANGE_FILTERS:
            return jsonify({"error": f"unsupported metric '{metric}'"}), 400
        value = func.sum(RANGE_FILTERS[metric]).label("value")
        with SessionLocal() as session:
            if group_by == "state":
                query = select(Facility.state.label("group_key"), value).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility).group_by(Facility.state)
            elif group_by == "facility":
                query = select(Facility.epa_facility_id.label("group_key"), Facility.facility_name, Facility.state, value).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility).group_by(Facility.facility_id).group_by(Facility.epa_facility_id, Facility.facility_name, Facility.state)
            else:
                query = select(Facility.epa_facility_id.label("facility_id"), Facility.facility_name, Unit.epa_unit_id.label("unit_id"), Facility.state, value).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility).group_by(Unit.unit_id).group_by(Facility.epa_facility_id, Facility.facility_name, Unit.epa_unit_id, Facility.state)
            rows = session.execute(query.order_by(desc(value)).limit(limit)).all()
        return jsonify({"group_by": group_by, "metric": metric, "results": [dict(row._mapping) for row in rows]})

    @app.get("/api/compare")
    def compare() -> Any:
        facility_ids = [value.strip() for value in request.args.get("facility_ids", "").split(",") if value.strip()]
        unit_ids = [value.strip() for value in request.args.get("unit_ids", "").split(",") if value.strip()]
        if not facility_ids and not unit_ids:
            return jsonify({"error": "facility_ids or unit_ids is required"}), 400
        filters: dict[str, Any] = {}
        if request.args.get("reporting_year"):
            filters["reporting_year"] = request.args["reporting_year"]
        try:
            query = select(AnnualRecord, Unit, Facility).select_from(AnnualRecord).join(AnnualRecord.unit).join(Unit.facility)
            if facility_ids:
                query = query.where(Facility.epa_facility_id.in_(facility_ids))
            if unit_ids:
                query = query.where(Unit.epa_unit_id.in_(unit_ids))
            if filters:
                query = query.where(*annual_filters(filters))
            with SessionLocal() as session:
                rows = session.execute(query.order_by(Facility.facility_name, Unit.epa_unit_id, AnnualRecord.reporting_year)).all()
            return jsonify({"count": len(rows), "records": [_record_dict(record, unit, facility) for record, unit, facility in rows]})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": f"Invalid comparison parameter: {exc}"}), 400

    @app.get("/api/facilities")
    def facilities() -> Any:
        with SessionLocal() as session:
            rows = session.scalars(select(Facility).order_by(Facility.facility_name)).all()
            return jsonify([{"facility_id": row.epa_facility_id, "facility_name": row.facility_name, "state": row.state, "county": row.county} for row in rows])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
