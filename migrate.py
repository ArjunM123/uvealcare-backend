"""
Safe schema migration: adds any columns that exist in models.py but not
yet in the live database. This is the pattern to use from now on whenever
a model changes — NOT "rm uvealcare.db" and reseed, which only ever made
sense for the local SQLite file and would be destructive on a real hosted
database with real data in it.

This script only ADDS missing columns. It never drops or modifies
existing data, and it's safe to run more than once — it skips anything
that's already there.

Run with:  python3 migrate.py
"""

from sqlalchemy import inspect, text
from database import engine

# Add entries here whenever a new column is added to models.py.
# Format: (table_name, column_name, column_type_sql)
NEW_COLUMNS = [
    ("decisions", "surveillance_protocol", "VARCHAR"),
    ("patients", "sex", "VARCHAR"),
    ("patients", "phone", "VARCHAR"),
    ("patients", "insurance", "VARCHAR"),
    ("patients", "primary_provider", "VARCHAR"),
    ("patients", "referring_provider", "VARCHAR"),
]


def run():
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column, col_type in NEW_COLUMNS:
            existing_columns = [c["name"] for c in inspector.get_columns(table)]
            if column in existing_columns:
                print(f"  {table}.{column} already exists — skipping.")
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            print(f"  Added {table}.{column} ({col_type}).")
    print("Migration complete.")


if __name__ == "__main__":
    run()
