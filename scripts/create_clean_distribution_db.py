"""
scripts/create_clean_distribution_db.py — Generate a pristine, empty database for release distribution.
Seeds all 31 Iranian provinces, canonical banks, and verified cities, but contains ZERO customer data.
"""
import os
import sys

# Add project root to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.db_migration import run_database_migration, check_database_health
from database import DBHandler


def generate_clean_db(output_path: str = "cbi_clean_template.db"):
    abs_path = os.path.abspath(output_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
        print(f"Removed existing file at: {abs_path}")

    print(f"Creating pristine template database at: {abs_path}")
    import database
    orig_path = database.DB_PATH
    try:
        database.DB_PATH = abs_path
        DBHandler.init_db()
        run_database_migration(abs_path)
        health = check_database_health(abs_path)
        print("Template DB health check:", health["integrity"])
        print("Table counts in template DB:", health["table_counts"])
        print("Is Healthy:", health["is_healthy"])
        assert health["is_healthy"], "Clean template database failed health check!"
        print("Clean template database created successfully.")
    finally:
        database.DB_PATH = orig_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "cbi_clean_template.db")
    generate_clean_db(out)
