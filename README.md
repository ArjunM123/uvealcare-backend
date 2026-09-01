# Clinical workflow platform — backend starter

A generalized backend: uveal melanoma is defined as *data* (a `DiseaseProfile`
with `DataFieldDefinition`s), not hardcoded schema. This is what lets the
platform extend to a second disease later without a rewrite.

## Run it

```bash
pip install fastapi sqlalchemy uvicorn pydantic --break-system-packages
python3 seed.py              # creates uvealcare.db, seeds the UM profile + 1 demo case
python3 -m uvicorn main:app --reload
```

Then visit `http://127.0.0.1:8000/docs` for interactive API docs.

## Files

- `models.py` — the generalized schema (Case, DataFieldDefinition, DataValue, ReadinessRule, DiseaseProfile)
- `seed.py` — defines uveal melanoma as config data (8 required fields, readiness rule)
- `main.py` — API: create/read cases, record field values, compute readiness %
- `database.py` — SQLite for local dev; swap `DATABASE_URL` to Postgres for the pilot

## Key endpoints

- `GET /cases/{id}` — case overview + care stage
- `POST /cases/{id}/values` — record a field value (imaging result, molecular test, etc.)
- `GET /cases/{id}/readiness` — computes readiness % generically from whatever the disease profile requires — this powers the "Case Readiness: X%" screen in your Figma

## Testing generalizability

Before wiring up your Figma screens, try writing a second `DiseaseProfile` in
`seed.py` for an unrelated workflow (any other multidisciplinary tumor board
use case works). If you can define it purely as new rows — new fields, new
readiness rule — without touching `models.py`, the abstraction is doing its
job. If you find yourself needing new columns or tables, that's a signal
something UM-specific leaked into the core schema — fix it now while it's cheap.

## Next steps from here

1. Wire your Figma "Case Readiness" screen to `GET /cases/{id}/readiness` — the response shape already matches (percent, checklist, missing items).
2. Wire imaging/molecular data entry forms to `POST /cases/{id}/values`.
3. Add a `POST /cases` endpoint to create new cases against a chosen disease profile.
4. Add auth (role-based, matching the `User.role` field) before any real patient data touches this.
5. Swap SQLite → Postgres and put it behind a BAA-compliant host before piloting with real PHI.
