from io import BytesIO

import pytest
import pandas as pd
from sqlalchemy import text

import app as app_module
from app import create_app


@pytest.fixture
def client():
    app = create_app("sqlite:///:memory:")
    return app.test_client()


def upload(client, content: bytes, filename: str = "campd.csv", approve: bool = False):
    suffix = "?approve=true" if approve else ""
    return client.post(
        f"/api/data/upload{suffix}",
        data={"file": (BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def test_upload_previews_and_preserves_duplicate_errors(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel\n"
        b"100,Test Plant,OH,U1,2022,Coal\n"
        b"100,Test Plant,OH,U1,2022,Coal\n"
    )
    response = upload(client, content)

    assert response.status_code == 200
    assert response.json["status"] == "pending"
    assert response.json["validation"]["accepted_records"] == 0
    assert any(error["error_code"] == "duplicate_key" for error in response.json["errors"])
    assert client.get("/api/annual-records").json["count"] == 0


def test_approved_upload_is_searchable_and_downloadable(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel,Gross Load,CO2 Mass\n"
        b"100,Test Plant,OH,U1,2022,Coal,10,20\n"
    )
    response = upload(client, content, approve=True)

    assert response.status_code == 200
    assert response.json["status"] == "imported"
    records = client.get("/api/annual-records?state=OH&co2_mass=20").json
    assert records["count"] == 1
    assert records["records"][0]["facility_name"] == "Test Plant"
    assert client.get("/api/annual-records.csv").headers["Content-Type"] == "text/csv; charset=utf-8"


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_sources_endpoint_describes_supported_original_sources(client):
    response = client.get("/api/sources")

    assert response.status_code == 200
    assert response.json["epa-campd"]["supported_import"] is True
    assert response.json["nasa-power"]["default_endpoint"].startswith("https://")


def test_epa_campd_retrieval_records_source_url(client, monkeypatch):
    frame = pd.DataFrame([{
        "facility_id": "100", "facility_name": "Test Plant", "state": "OH",
        "unit_id": "U1", "reporting_year": 2022,
    }])
    source_url = "https://example.test/campd.csv"
    monkeypatch.setattr(app_module, "retrieve_dataframe", lambda url, params=None: (frame, b"source"))

    response = client.post("/api/data/retrieve/epa-campd", json={"url": source_url, "approve": True})

    assert response.status_code == 200
    assert response.json["status"] == "imported"
    with client.application.extensions["epa_sessionmaker"]() as session:
        provenance = session.execute(text("SELECT source_url_or_api FROM data_provenance")).scalar_one()
        assert provenance == source_url
