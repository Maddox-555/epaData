# epaData

EPA Data Management, Evaluation, and Visualization System.

## Documentation

- [Complete database schema](DATABASE_SCHEMA.md)
- [SQLAlchemy model definitions](models.py)

## Backend API

Install dependencies with `pip install -r requirements.txt`, then start the API:

```powershell
python app.py
```

The backend exposes JSON/CSV responses and does not implement a frontend yet:

- `GET /api/health` - service health check.
- `POST /api/data/upload` - upload CSV/XLS/XLSX as multipart field `file`; defaults to validation preview. Add `?approve=true` to import only if validation passes.
- `POST /api/data/retrieve` - retrieve a CSV, Excel, or JSON URL with `{ "url": "...", "params": {...} }`; set `approve` to `true` to import.
- `GET /api/datasets` - list retrieval/upload datasets and counts.
- `GET /api/facilities` - list imported facilities.
- `GET /api/annual-records` - search annual records with facility, state, county, unit, fuel, type, year, operating-time, load, heat-input, and emissions filters.
- `GET /api/annual-records.csv` - download the filtered annual-record query as CSV.

Run the backend tests with `pytest`.
