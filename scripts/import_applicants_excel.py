"""
scripts/import_applicants_excel.py
Imports applicants from an Excel (.xlsx) file into cbi_ultimate.db.
Usage:
    python scripts/import_applicants_excel.py [path_to_excel_file]
"""
import sqlite3
import json
import sys
import openpyxl
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from routes.applicants import parse_and_validate_excel_rows


def import_excel_to_db(excel_file: Path, db_path: Path):
    if not excel_file.exists():
        print(f"[ERROR] Excel file not found: {excel_file}")
        sys.exit(1)

    print(f"[*] Reading Excel file: {excel_file.name}...")
    wb = openpyxl.load_workbook(str(excel_file), data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))

    if not all_rows:
        print("[ERROR] Excel sheet is empty!")
        sys.exit(1)

    headers = [str(h or "").strip() for h in all_rows[0]]
    dict_rows = []
    for row_vals in all_rows[1:]:
        if not any(v is not None and str(v).strip() != "" for v in row_vals):
            continue
        row_dict = {headers[i]: row_vals[i] for i in range(min(len(headers), len(row_vals))) if headers[i]}
        dict_rows.append(row_dict)

    print(f"[*] Parsing {len(dict_rows)} applicant rows...")
    valid_records, error_rows = parse_and_validate_excel_rows(dict_rows)

    if error_rows:
        print(f"[WARNING] {len(error_rows)} rows had validation errors:")
        for err in error_rows:
            print(f"  - Row {err['row']} ({err['full_name']}): {err['errors']}")

    print(f"[*] Inserting/Updating {len(valid_records)} valid applicants into database ({db_path.name})...")
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()

    # Ensure table exists
    c.execute("""
        CREATE TABLE IF NOT EXISTS applicants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            national_id TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'Stopped',
            data TEXT
        )
    """)

    inserted = 0
    updated = 0
    for rec in valid_records:
        nid = rec["national_id"]
        name = rec["full_name"]
        d_str = json.dumps(rec["data"], ensure_ascii=False)

        existing = c.execute("SELECT id FROM applicants WHERE national_id=?", (nid,)).fetchone()
        if existing:
            c.execute("UPDATE applicants SET full_name=?, data=? WHERE national_id=?", (name, d_str, nid))
            updated += 1
        else:
            c.execute("INSERT INTO applicants (full_name, national_id, data, status) VALUES (?, ?, ?, 'Stopped')", (name, nid, d_str))
            inserted += 1

    conn.commit()
    conn.close()

    print("=" * 60)
    print(f"[SUCCESS] Import completed successfully!")
    print(f"  New applicants inserted: {inserted}")
    print(f"  Existing applicants updated: {updated}")
    print(f"  Total valid records in DB: {inserted + updated}")
    print("=" * 60)


if __name__ == "__main__":
    excel_arg = sys.argv[1] if len(sys.argv) > 1 else str(ROOT_DIR / "اطلاعات_متقاضیان_MAHANBOT.xlsx")
    db_arg = sys.argv[2] if len(sys.argv) > 2 else str(ROOT_DIR / "cbi_ultimate.db")
    import_excel_to_db(Path(excel_arg), Path(db_arg))
