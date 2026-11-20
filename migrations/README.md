# Database migrations

The project uses Alembic for schema versioning. The committed SQLite database is
the demonstration data snapshot; migrations describe the schema needed by a new
database and provide a safe path for future schema changes.

Create and activate the virtual environment, install `requirements.txt`, and
run the following commands from the repository root:

```powershell
alembic upgrade head
```

To create a new revision after changing `models.py`:

```powershell
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
```

The baseline migration is idempotent and uses the SQLAlchemy model metadata to
create missing tables. This allows it to initialize a new SQLite database while
also safely recognizing the already-populated demonstration database.
