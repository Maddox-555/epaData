from io import BytesIO

import pytest
import pandas as pd
from sqlalchemy import text

import app as app_module
from app import create_app
from data_service import parse_search_description


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


def test_description_search_interprets_project_example_and_downloads_from_database(client, monkeypatch):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel,CO2 Mass\n"
        b"100,Big River Plant,KY,U1,2025,Coal,600000\n"
        b"200,Gas Station,OH,G1,2025,Natural Gas,700000\n"
    )
    assert upload(client, content, approve=True).json["status"] == "imported"
    monkeypatch.delenv("EPA_API_KEY", raising=False)

    response = client.get("/api/annual-records", query_string={
        "description": "coal-fired units in Kentucky in 2025 with CO2 emissions greater than 500000",
    })
    assert response.status_code == 200
    assert response.json["interpreted_filters"] == {
        "state": "KY",
        "reporting_year": "2025",
        "primary_fuel": "coal",
        "co2_mass_min": "500000",
    }
    assert response.json["total"] == 1
    assert response.json["records"][0]["facility_name"] == "Big River Plant"

    csv_response = client.get("/api/annual-records.csv", query_string={
        "description": "coal-fired units in Kentucky in 2025 with CO2 emissions greater than 500000",
    })
    assert csv_response.status_code == 200
    assert "Big River Plant" in csv_response.data.decode()


def test_description_search_rejects_unrecognized_text(client):
    response = client.get("/api/annual-records", query_string={"description": "show everything"})

    assert response.status_code == 400
    assert "supported" in response.json["error"]


def test_description_parser_supports_novice_query_variations():
    assert parse_search_description("show coal plants in Kentucky that emitted more than 500k tons of CO2 in 2024") == {
        "state": "KY", "reporting_year": "2024", "primary_fuel": "coal", "co2_mass_min": "500000",
    }
    assert parse_search_description("natural gas units in Texas with gross load between 100,000 and 1.2 million MWh") == {
        "state": "TX", "primary_fuel": "natural gas", "gross_load_min": "100000", "gross_load_max": "1200000",
    }
    assert parse_search_description("top 10 facilities by CO2 emissions in Ohio")["sort"] == "co2_mass"


def test_upload_can_be_approved_after_preview_and_exposes_audit_downloads(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel,CO2 Mass\n"
        b"100,Test Plant,OH,U1,2022,Coal,20\n"
    )
    preview = upload(client, content)
    dataset_id = preview.json["dataset_id"]
    assert preview.json["status"] == "pending"
    assert client.get("/api/annual-records").json["count"] == 0

    approved = client.post(f"/api/data/upload/{dataset_id}/approve")
    assert approved.status_code == 200
    assert approved.json["status"] == "imported"
    assert client.get(f"/api/datasets/{dataset_id}.csv").status_code == 200
    assert client.get(f"/api/datasets/{dataset_id}/provenance.csv").status_code == 200
    detail = client.get("/api/units/100/U1")
    assert detail.status_code == 200
    assert detail.json["records"][0]["reporting_year"] == 2022


def test_invalid_upload_has_error_download_and_can_be_cancelled(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year\n"
        b"100,Test Plant,Ohio,U1,2022\n"
    )
    preview = upload(client, content)
    dataset_id = preview.json["dataset_id"]
    assert client.get(f"/api/uploads/{dataset_id}/errors.csv").status_code == 200
    cancelled = client.post(f"/api/data/upload/{dataset_id}/cancel")
    assert cancelled.json["status"] == "cancelled"
    assert client.post(f"/api/data/upload/{dataset_id}/approve").status_code == 400


def test_rankings_support_facility_state_and_unit_groups(client):
    content = (
        b"EPA Facility ID,Facility Name,State,EPA Unit ID,Reporting Year,Primary Fuel,CO2 Mass\n"
        b"100,Big River,KY,U1,2024,Coal,900\n"
        b"100,Big River,KY,U2,2024,Coal,100\n"
        b"200,Ohio Works,OH,U1,2024,Coal,500\n"
    )
    assert upload(client, content, approve=True).json["status"] == "imported"
    assert client.get("/api/rankings?group_by=facility&metric=co2_mass&limit=1").json["results"][0]["group_key"] == "100"
    assert client.get("/api/rankings?group_by=state&metric=co2_mass").json["results"][0]["group_key"] == "KY"
    assert client.get("/api/rankings?group_by=unit&metric=co2_mass").json["results"][0]["unit_id"] == "U1"
    comparison = client.get("/api/compare?facility_ids=100,200&reporting_year=2024")
    assert comparison.status_code == 200
    assert comparison.json["count"] == 3


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
    captured = {}
    monkeypatch.setenv("EPA_API_KEY", "test-server-key")
    monkeypatch.setattr(app_module, "retrieve_campd_dataframe", lambda params, api_key: (captured.update({"params": params, "api_key": api_key}) or (frame, b"source")))

    response = client.post("/api/data/retrieve/epa-campd", json={"params": {"year": 2022}, "approve": True})

    assert response.status_code == 200
    assert response.json["status"] == "imported"
    with client.application.extensions["epa_sessionmaker"]() as session:
        provenance = session.execute(text("SELECT source_url_or_api FROM data_provenance")).scalar_one()
        assert provenance.endswith("/emissions-mgmt/emissions/apportioned/annual")
        query_parameters = session.execute(text("SELECT query_parameters FROM data_provenance")).scalar_one()
        assert "test-server-key" not in query_parameters
    assert captured["params"]["year"] == 2022
    assert captured["api_key"] == "test-server-key"


def test_epa_campd_retrieval_requires_reporting_year(client, monkeypatch):
    monkeypatch.setenv("EPA_API_KEY", "test-server-key")

    response = client.post("/api/data/retrieve/epa-campd", json={"params": {"state": "KY"}})

    assert response.status_code == 400
    assert "reporting year" in response.json["error"]
