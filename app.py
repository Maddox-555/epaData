"""Flask JSON API for epaData data ingestion and display."""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from data_service import SOURCE_DEFINITIONS, build_annual_query, ingest_dataframe, read_dataframe, retrieve_dataframe
from models import AnnualRecord, Base, Dataset, Facility, Unit


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
        except Exception as exc:
            app.logger.exception("Remote retrieval failed")
            return jsonify({"error": "Remote data could not be retrieved", "detail": str(exc)}), 502

    @app.post("/api/data/retrieve/epa-campd")
    def retrieve_epa_campd() -> Any:
        payload = request.get_json(silent=True) or {}
        api_key = os.getenv("EPA_API_KEY")
        if not api_key:
            return jsonify({"error": "EPA_API_KEY is not configured on the server."}), 503
        params = dict(payload.get("params") or {})
        payload["provenance_params"] = dict(params)
        params["api_key"] = api_key
        payload["params"] = params
        payload.setdefault("source_name", SOURCE_DEFINITIONS["epa-campd"]["name"])
        with app.test_request_context("/api/data/retrieve", method="POST", json=payload):
            return retrieve_data()

    @app.get("/api/annual-records")
    def annual_records() -> Any:
        try:
            args = request.args.to_dict()
            limit = min(max(int(args.pop("limit", 100)), 1), 1000)
            offset = max(int(args.pop("offset", 0)), 0)
            with SessionLocal() as session:
                rows = session.execute(build_annual_query(args).limit(limit).offset(offset)).all()
                data = [_record_dict(record, unit, facility) for record, unit, facility in rows]
                return jsonify({"count": len(data), "limit": limit, "offset": offset, "records": data})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": f"Invalid query parameter: {exc}"}), 400

    @app.get("/api/annual-records.csv")
    def annual_records_csv() -> Any:
        args = request.args.to_dict()
        args.pop("limit", None)
        args.pop("offset", None)
        with SessionLocal() as session:
            rows = session.execute(build_annual_query(args).limit(10000)).all()
        output = io.StringIO()
        records = [_record_dict(record, unit, facility) for record, unit, facility in rows]
        if records:
            writer = csv.DictWriter(output, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="annual-records.csv")

    @app.get("/api/facilities")
    def facilities() -> Any:
        with SessionLocal() as session:
            rows = session.scalars(select(Facility).order_by(Facility.facility_name)).all()
            return jsonify([{"facility_id": row.epa_facility_id, "facility_name": row.facility_name, "state": row.state, "county": row.county} for row in rows])

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
