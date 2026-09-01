# Tests

Run from the project root with the project's own interpreter.

```
.venv\Scripts\python.exe tests\test_units.py     # fast, no network or browser
.venv\Scripts\python.exe tests\test_hosted.py    # auth + DB-backed storage
```

`test_units.py` covers the logic that decides what you apply to: resume
parsing, skill extraction, job families, visa detection, apply-URL resolution,
form-field mapping, job-title cleaning and per-country salary.

`test_hosted.py` starts a real server with `JOBPILOT_SERVERLESS=1` against a
throwaway database and checks the password gate, session cookie flags, that
documents are stored in the database rather than on disk, and that form
autofill refuses with a useful message.

Both use their own data directory (`JOBPILOT_DATA_DIR`), so they never touch
your real resume, jobs or applications.
