# epaData

EPA Data Management, Evaluation, and Visualization System.

## Documentation

- [Complete database schema](DATABASE_SCHEMA.md) (includes the maintained Mermaid ER diagram)
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

The loader and the named CAMPD retrieval route call the official paginated CAMPD APIs directly. Put your EPA API key in the server's `EPA_API_KEY` environment variable. The key is read only by Python on the server, sent to EPA from the server-side request, and is never accepted from browser JavaScript, returned by an API response, or committed to GitHub:

```powershell
$env:EPA_API_KEY = "your-key"
python populate_database.py --epa-years 2020,2021,2022,2023,2024
```

If the production gateway is temporarily unavailable, select an EPA gateway environment without changing the code. For example, the documented development gateway is:

```powershell
$env:EPA_API_ENV = "dev"
$env:EPA_API_KEY = "your-new-key"
python populate_database.py --epa-years 2020,2021,2022,2023,2024
```

`EPA_API_ENV` is empty by default, which uses the production gateway. Supported environment path values depend on the EPA deployment, including `dev`, `test`, and `beta`.

The `POST /api/data/retrieve/epa-campd` route returns `503` when `EPA_API_KEY` is not configured. Users provide CAMPD filters in the request's `params` object, such as `year`, `state`, facility ID, fuel type, unit type, and pollution-control fields; the server appends the secret API key before contacting EPA. Retrieval provenance stores the source URL, retrieval time, query parameters, reporting year, and imported record counts, but never stores the API key itself.

The API calls `facilities-mgmt/facilities/attributes` and `emissions-mgmt/emissions/apportioned/annual`, joins facility/unit attributes to annual emissions, validates the result, and records the endpoint in provenance. Do not commit the key or place it in a command that will be saved in shell history.

CAMPD field sources are split across those two official responses. The facilities-attributes response supplies `sourceCategory`, `primaryFuelInfo`, `secondaryFuelInfo`, `unitType`, `so2ControlInfo`, `noxControlInfo`, `pmControlInfo`, `commercialOperationDate`, and `programCodeInfo`. The annual-apportioned-emissions response supplies `sumOpTime`, `grossLoad`, `steamLoad`, `heatInput`, `co2Mass`, `so2Mass`, and `noxMass`. The importer maps these names into the normalized schema and updates existing facility/unit attributes during a later import. The current SQLite snapshot was created before these mappings were corrected, so rerun the CAMPD import for the desired years to backfill the previously empty fields.

Alternatively, CAMPD exports can be downloaded from the bulk-data or query interface and passed explicitly:

```powershell
python populate_database.py --epa-file .\campd-annual.csv
```

After EPA facilities have been loaded, load NASA POWER climate data for the years needed by the project. The weather workflow uses the EPA facility coordinates directly; NOAA station data is not required.

The loader uses NASA POWER's monthly **regional** endpoint in 10-degree latitude/longitude tiles. Regional requests return the native approximately 0.5-degree latitude by 0.625-degree longitude grid. The importer makes one request per tile and parameter, then assigns each EPA facility the nearest returned NASA grid point. This avoids one API request per facility and the point-request quota when more than 300 facilities are included.

The requested NASA POWER parameters are:

- `T2M`: monthly mean temperature, used for annual average temperature and cooling/heating degree days.
- `T2M_MAX`: monthly maximum temperature, used for annual maximum temperature and the count of extreme-heat months above 35 C.
- `T2M_MIN`: monthly minimum temperature, used for annual minimum temperature.
- `PRECTOTCORR`: bias-corrected precipitation, converted from monthly daily-rate values to an annual millimeter estimate.
- `WS2M`: wind speed at 2 meters, used for annual average wind speed.

For each requested year, the monthly values are summarized into one row in `weather_annual_records` for each facility. CDD and HDD use an 18.3 C base temperature, and the facility coordinates, selected NASA grid coordinates, units, degree-day base, and regional-grid resolution are retained in the row metadata. The active command is:

```powershell
python populate_database.py --nasa-weather --years 2020,2021,2022,2023,2024 --weather-workers 16
```

The `--weather-workers` value controls concurrent regional requests; it does not create additional weather data or change the selected variables. Existing `(facility_id, reporting_year)` rows are skipped, so copying the populated SQLite database to another device preserves the loaded weather data without requiring NASA POWER access. Internet access is only needed to load missing years, refresh records, or rebuild the database.

`--epa-file` accepts CSV, XLS, or XLSX. The database intentionally uses annual CAMPD grain; it does not include a daily power table because annual totals cannot be converted into valid daily observations. The loader records source URLs and creates dataset/provenance rows for each official source.

The source catalog currently covers EPA CAMPD, NASA POWER climate data, and TRACI. EPA CAMPD CSV/Excel/JSON imports use the annual CAMPD model. NASA POWER annual climate summaries are persisted in `weather_annual_records`.

Run the backend tests with `pytest`.

## Acknowledgments

- **ChatGPT (GPT-5.6 Luna)** by OpenAI was used to generate the majority of the codebase
- **GitHub Copilot** Prompts were provided to AI through this service across development
