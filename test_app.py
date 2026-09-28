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


def test_search_filters_ranges_sorting_and_pagination(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel,NOx Control,CO2 Mass,SO2 Mass\n"
        b"100,Big River Plant,KY,U1,2024,Coal,Selective Catalytic Reduction,900000,400\n"
        b"100,Big River Plant,KY,U2,2024,Coal,,600000,900\n"
        b"200,Gas Station,KY,G1,2024,Pipeline Natural Gas,,300000,1\n"
        b"300,Ohio Coal Works,OH,C1,2024,Coal,,800000,50\n"
        b"100,Big River Plant,KY,U1,2023,Coal,Selective Catalytic Reduction,850000,420\n"
    )
    assert upload(client, content, approve=True).json["status"] == "imported"

    def search(query: str) -> dict:
        response = client.get(f"/api/annual-records?{query}")
        assert response.status_code == 200, response.json
        return response.json

    result = search("state=ky&reporting_year=2024&primary_fuel=coal&co2_mass_min=500000")
    assert result["total"] == 2
    assert {row["unit_id"] for row in result["records"]} == {"U1", "U2"}

    assert search("facility_name=river")["total"] == 3
    assert search("nox_control_information=catalytic")["total"] == 2
    assert search("reporting_year=2024&so2_mass_max=500")["total"] == 3

    top = search("reporting_year=2024&sort=co2_mass&order=desc&limit=2")
    assert [row["co2_mass"] for row in top["records"]] == [900000, 800000]
    assert top["total"] == 4 and top["count"] == 2

    page_two = search("reporting_year=2024&sort=co2_mass&order=desc&limit=2&offset=2")
    assert [row["co2_mass"] for row in page_two["records"]] == [600000, 300000]

    history = search("facility_id=100&unit_id=u1&reporting_year_min=2023&reporting_year_max=2024&sort=reporting_year&order=asc")
    assert [row["reporting_year"] for row in history["records"]] == [2023, 2024]

    assert client.get("/api/annual-records?sort=not_a_column").status_code == 400
    assert client.get("/api/annual-records?co2_mass_min=abc").status_code == 400

    csv_text = client.get("/api/annual-records.csv?state=KY&primary_fuel=coal").data.decode()
    assert len(csv_text.strip().splitlines()) == 1 + 3


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
    captured = {}
    monkeypatch.setenv("EPA_API_KEY", "test-server-key")
    monkeypatch.setattr(app_module, "retrieve_dataframe", lambda url, params=None: (captured.update({"params": params}) or (frame, b"source")))

    response = client.post("/api/data/retrieve/epa-campd", json={"url": source_url, "approve": True})

    assert response.status_code == 200
    assert response.json["status"] == "imported"
    with client.application.extensions["epa_sessionmaker"]() as session:
        provenance = session.execute(text("SELECT source_url_or_api FROM data_provenance")).scalar_one()
        assert provenance == source_url
        query_parameters = session.execute(text("SELECT query_parameters FROM data_provenance")).scalar_one()
        assert "test-server-key" not in query_parameters
    assert captured["params"]["api_key"] == "test-server-key"
