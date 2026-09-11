# epaData

EPA Data Management, Evaluation, and Visualization System.

## Documentation

- [Complete database schema](DATABASE_SCHEMA.md)
- [ER Diagram](https://app.eraser.io/workspace/CeQBm54ImyOxlG4qE6Rx?origin=share)
- [SQLAlchemy model definitions](models.py)

## Backend API

Install dependencies with `pip install -r requirements.txt`, then start the API:

```powershell
python app.py
```

The backend exposes JSON/CSV responses and does not implement a frontend yet:

- `GET /api/health` - service health check.
- `GET /api/sources` - list the configured original source definitions and whether their schema can currently be imported.
- `POST /api/data/upload` - upload CSV/XLS/XLSX as multipart field `file`; defaults to validation preview. Add `?approve=true` to import only if validation passes.
- `POST /api/data/retrieve` - retrieve a CSV, Excel, or JSON URL with `{ "url": "...", "params": {...} }`; set `approve` to `true` to import.
- `POST /api/data/retrieve/epa-campd` - named EPA CAMPD retrieval route; accepts the same JSON body as the generic retrieval route and records the source URL in provenance.
- `GET /api/datasets` - list retrieval/upload datasets and counts.
- `GET /api/facilities` - list imported facilities.
- `GET /api/annual-records` - search annual records with facility, state, county, unit, fuel, type, year, operating-time, load, heat-input, and emissions filters.
- `GET /api/annual-records.csv` - download the filtered annual-record query as CSV.

### Populate source data

The repeatable loader is `populate_database.py`. It downloads the official TRACI 2.2 workbook and NASA POWER climate data, then seeds the indicator definitions:

```powershell
python populate_database.py
```

The loader can now call the official paginated CAMPD APIs directly. Put your EPA API key in the `EPA_API_KEY` environment variable, then choose one or more years:

```powershell
$env:EPA_API_KEY = "your-key"
python populate_database.py --epa-years 2020,2021,2022,2023,2024
```

The API calls `facilities-mgmt/facilities/attributes` and `emissions-mgmt/emissions/apportioned/annual`, joins facility/unit attributes to annual emissions, validates the result, and records the endpoint in provenance. Do not commit the key or place it in a command that will be saved in shell history.

Alternatively, CAMPD exports can be downloaded from the bulk-data or query interface and passed explicitly:

```powershell
python populate_database.py --epa-file .\campd-annual.csv
```

After EPA facilities have been loaded, load NASA POWER climate data for the years needed by the project. The loader uses linked facility coordinates and stores one annual summary per climate point/year:

```powershell
python populate_database.py --nasa-weather --years 2020,2021,2022,2023,2024 --weather-workers 16
```

`--epa-file` accepts CSV, XLS, or XLSX. The database intentionally uses annual CAMPD grain; it does not include a daily power table because annual totals cannot be converted into valid daily observations. The loader records source URLs and creates dataset/provenance rows for each official source.

The source catalog currently covers EPA CAMPD, NASA POWER climate data, and TRACI. EPA CAMPD CSV/Excel/JSON imports use the annual CAMPD model. NASA POWER annual climate summaries are persisted in `weather_annual_records`.

Run the backend tests with `pytest`.

## Acknowledgments

- **ChatGPT (GPT-5.6 Luna)** by OpenAI was used to generate the majority of the codebase
- **GitHub Copilot** Prompts were provided to AI through this service across development
