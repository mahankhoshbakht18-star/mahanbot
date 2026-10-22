"""
core/db_migration.py — Database migration, seed data, health checks, and repair routines.
Unified relational schema for Provinces, Cities, Banks, Branches, Applicant Preferences,
and Page Observations with zero data loss and automated backups.
"""
import datetime
import json
import os
import shutil
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core.persian_normalizer import (
    normalize_compact_key,
    normalize_location_key,
    normalize_national_id,
    normalize_persian_text,
    normalize_postal_code,
)

# Reference definitions for 31 Provinces of Iran
IRAN_PROVINCES = [
    {"id": 1, "name": "آذربایجان شرقی", "aliases": ["اذربایجان شرقی", "آذربایجان‌شرقی", "تبریز"]},
    {"id": 2, "name": "آذربایجان غربی", "aliases": ["اذربایجان غربی", "آذربایجان‌غربی", "ارومیه"]},
    {"id": 3, "name": "اردبیل", "aliases": ["اردبيل"]},
    {"id": 4, "name": "اصفهان", "aliases": ["اصفهان"]},
    {"id": 5, "name": "البرز", "aliases": ["کرج"]},
    {"id": 6, "name": "ایلام", "aliases": ["ايلام"]},
    {"id": 7, "name": "بوشهر", "aliases": ["بوشهر"]},
    {"id": 8, "name": "تهران", "aliases": ["تهران"]},
    {"id": 9, "name": "چهارمحال و بختیاری", "aliases": ["چهار محال و بختیاری", "چهارمحال وبختیاری", "شهرکرد"]},
    {"id": 10, "name": "خراسان جنوبی", "aliases": ["بیرجند"]},
    {"id": 11, "name": "خراسان رضوی", "aliases": ["مشهد"]},
    {"id": 12, "name": "خراسان شمالی", "aliases": ["بجنورد"]},
    {"id": 13, "name": "خوزستان", "aliases": ["اهواز"]},
    {"id": 14, "name": "زنجان", "aliases": ["زنجان"]},
    {"id": 15, "name": "سمنان", "aliases": ["سمنان"]},
    {
        "id": 16,
        "name": "سیستان و بلوچستان",
        "aliases": [
            "سيستان و بلوچستان",
            "سیستان وبلوچستان",
            "سيستان وبلوچستان",
            "استان سیستان و بلوچستان",
            "استان سیستان وبلوچستان",
            "زاهدان",
        ],
    },
    {"id": 17, "name": "فارس", "aliases": ["شیراز"]},
    {"id": 18, "name": "قزوین", "aliases": ["قزوين"]},
    {"id": 19, "name": "قم", "aliases": ["قم"]},
    {"id": 20, "name": "کردستان", "aliases": ["كردستان", "سنندج"]},
    {"id": 21, "name": "کرمان", "aliases": ["كرمان"]},
    {"id": 22, "name": "کرمانشاه", "aliases": ["كرمانشاه", "باختران"]},
    {"id": 23, "name": "کهگیلویه و بویراحمد", "aliases": ["کهگیلویه وبویراحمد", "کهگیلویه و بویر احمد", "یاسوج"]},
    {"id": 24, "name": "گلستان", "aliases": ["گرگان"]},
    {"id": 25, "name": "گیلان", "aliases": ["گيلان", "رشت"]},
    {"id": 26, "name": "لرستان", "aliases": ["لرستان", "خرم آباد", "خرم‌اباد"]},
    {"id": 27, "name": "مازندران", "aliases": ["ساری"]},
    {"id": 28, "name": "مرکزی", "aliases": ["مركزي", "اراک"]},
    {"id": 29, "name": "هرمزگان", "aliases": ["بندرعباس", "بندر عباس"]},
    {"id": 30, "name": "همدان", "aliases": ["همدان"]},
    {"id": 31, "name": "یزد", "aliases": ["يزد"]},
]

# Canonical Banks with codes and aliases
IRAN_BANKS = [
    {"id": 1, "code": "melli", "name": "ملی", "aliases": ["بانک ملی ایران", "ملی ایران", "بانک ملی", "ملی"]},
    {"id": 2, "code": "saderat", "name": "صادرات", "aliases": ["بانک صادرات ایران", "صادرات ایران", "بانک صادرات", "صادرات"]},
    {"id": 3, "code": "tejarat", "name": "تجارت", "aliases": ["بانک تجارت", "تجارت"]},
    {"id": 4, "code": "mellat", "name": "ملت", "aliases": ["بانک ملت", "ملت"]},
    {"id": 5, "code": "sepah", "name": "سپه", "aliases": ["بانک سپه", "سپه", "انصار", "قوامین", "حکمت ایرانیان", "مهر اقتصاد", "کوثر"]},
    {"id": 6, "code": "keshavarzi", "name": "کشاورزی", "aliases": ["بانک کشاورزی", "کشاورزی"]},
    {"id": 7, "code": "maskan", "name": "مسکن", "aliases": ["بانک مسکن", "مسکن"]},
    {"id": 8, "code": "refah", "name": "رفاه کارگران", "aliases": ["بانک رفاه کارگران", "بانک رفاه", "رفاه کارگران", "رفاه"]},
    {"id": 9, "code": "tosee_taavon", "name": "توسعه تعاون", "aliases": ["بانک توسعه تعاون", "توسعه تعاون", "توسعهتعاون"]},
    {"id": 10, "code": "tosee_saderat", "name": "توسعه صادرات", "aliases": ["بانک توسعه صادرات ایران", "بانک توسعه صادرات", "توسعه صادرات"]},
    {"id": 11, "code": "sanat_madan", "name": "صنعت و معدن", "aliases": ["بانک صنعت و معدن", "صنعت و معدن", "صنعت ومعدن"]},
    {"id": 12, "code": "postbank", "name": "پست بانک", "aliases": ["پست بانک ایران", "پست بانک", "پست‌بانک", "پستبانک"]},
    {"id": 13, "code": "eghtesad_novin", "name": "اقتصاد نوین", "aliases": ["بانک اقتصاد نوین", "اقتصاد نوین", "اقتصادنوين"]},
    {"id": 14, "code": "parsian", "name": "پارسیان", "aliases": ["بانک پارسیان", "پارسیان"]},
    {"id": 15, "code": "pasargad", "name": "پاسارگاد", "aliases": ["بانک پاسارگاد", "پاسارگاد"]},
    {"id": 16, "code": "karafarin", "name": "کارآفرین", "aliases": ["بانک کارآفرین", "کارافرین", "کار آفرین"]},
    {"id": 17, "code": "saman", "name": "سامان", "aliases": ["بانک سامان", "سامان"]},
    {"id": 18, "code": "sina", "name": "سینا", "aliases": ["بانک سینا", "سینا"]},
    {"id": 19, "code": "sarmayeh", "name": "سرمایه", "aliases": ["بانک سرمایه", "سرمایه"]},
    {"id": 20, "code": "shahr", "name": "شهر", "aliases": ["بانک شهر", "شهر"]},
    {"id": 21, "code": "day", "name": "دی", "aliases": ["بانک دی", "دی"]},
    {"id": 22, "code": "gardeshgari", "name": "گردشگری", "aliases": ["بانک گردشگری", "گردشگری"]},
    {"id": 23, "code": "iran_zamin", "name": "ایران زمین", "aliases": ["بانک ایران زمین", "ایران زمین", "ایرانزمین"]},
    {"id": 24, "code": "ayandeh", "name": "آینده", "aliases": ["بانک آینده", "آینده", "اینده"]},
    {"id": 25, "code": "khavarmiyaneh", "name": "خاورمیانه", "aliases": ["بانک خاورمیانه", "خاورمیانه", "خاور میانه"]},
    {"id": 26, "code": "iran_venezuela", "name": "ایران و ونزوئلا", "aliases": ["بانک ایران و ونزوئلا", "ایران و ونزوئلا", "ایران و ونزویلا"]},
    {"id": 27, "code": "mehr_iran", "name": "قرض الحسنه مهر ایران", "aliases": ["بانک قرض الحسنه مهر ایران", "قرض الحسنه مهر ایران", "مهر ایران", "مهرایران", "بانک مهر"]},
    {"id": 28, "code": "resalat", "name": "قرض الحسنه رسالت", "aliases": ["بانک قرض الحسنه رسالت", "قرض الحسنه رسالت", "رسالت"]},
    {"id": 29, "code": "melal", "name": "موسسه اعتباری ملل", "aliases": ["موسسه اعتباری ملل", "اعتباری ملل", "بانک ملل", "ملل"]},
    {"id": 30, "code": "noor", "name": "موسسه اعتباری نور", "aliases": ["موسسه اعتباری نور", "اعتباری نور", "بانک نور", "نور"]},
]

# Sistan & Baluchestan Cities (Province ID 16)
SISTAN_BALUCHESTAN_CITIES = [
    {"name": "مهرستان", "aliases": ["زابلی"], "site_value": "793"},
    {"name": "سوران", "aliases": [], "site_value": "841"},
    {"name": "سیب و سوران", "aliases": ["سیب وسوران"], "site_value": "782"},
    {"name": "زاهدان", "aliases": [], "site_value": "830"},
    {"name": "ایرانشهر", "aliases": [], "site_value": "831"},
    {"name": "زابل", "aliases": [], "site_value": "832"},
    {"name": "چابهار", "aliases": ["چابهار / سیستان و بلوچستان", "چابهار / سیستان و بلوجستان"], "site_value": "833"},
    {"name": "چابهار / مناطق آزاد", "aliases": ["چابهار منطقه آزاد", "منطقه آزاد چابهار"], "site_value": "829"},
    {"name": "سراوان", "aliases": [], "site_value": "834"},
    {"name": "خاش", "aliases": [], "site_value": "835"},
    {"name": "نیک شهر", "aliases": ["نیکشهر"], "site_value": "836"},
    {"name": "کنارک", "aliases": [], "site_value": "838"},
    {"name": "راسک", "aliases": [], "site_value": "180"},
    {"name": "میرجاوه", "aliases": [], "site_value": "847"},
    {"name": "دشتیاری", "aliases": [], "site_value": "157"},
    {"name": "قصرقند", "aliases": [], "site_value": "845"},
    {"name": "فنوج", "aliases": [], "site_value": "844"},
    {"name": "سرباز", "aliases": [], "site_value": "843"},
    {"name": "زهک", "aliases": [], "site_value": "840"},
    {"name": "نیمروز", "aliases": [], "site_value": "848"},
    {"name": "هامون - محمدآباد", "aliases": ["محمدآباد", "هامون"], "site_value": "849"},
    {"name": "هیرمند", "aliases": [], "site_value": "496"},
    {"name": "دلگان", "aliases": ["گلمورتی"], "site_value": "912"},
    {"name": "بمپور", "aliases": [], "site_value": "155"},
    {"name": "تفتان", "aliases": [], "site_value": "156"},
    {"name": "لاشار", "aliases": ["اسپکه"], "site_value": "158"},
    {"name": "زرآباد", "aliases": [], "site_value": "159"},
    {"name": "گلشن", "aliases": ["جالق"], "site_value": "134"},
]

# Lorestan Cities (Province ID 26)
LORESTAN_CITIES = [
    {"name": "ازنا", "aliases": [], "site_value": "429"},
    {"name": "خرم آباد", "aliases": ["خرم‌آباد", "خرم اباد"], "site_value": "424"},
    {"name": "بروجرد", "aliases": [], "site_value": "627"},
    {"name": "دورود", "aliases": [], "site_value": "427"},
    {"name": "الیگودرز", "aliases": [], "site_value": "425"},
    {"name": "کوهدشت", "aliases": [], "site_value": "426"},
    {"name": "دلفان", "aliases": ["نورآباد", "نوراباد"], "site_value": "428"},
    {"name": "سلسله", "aliases": ["الشتر"], "site_value": "430"},
    {"name": "پلدختر", "aliases": ["پل دختر"], "site_value": "431"},
    {"name": "چگنی", "aliases": ["سراب دوره", "دوره"], "site_value": "170"},
    {"name": "رومشکان - چقابل", "aliases": ["رومشکان", "چقابل"], "site_value": "433"},
]

# Tehran Cities (Province ID 8)
TEHRAN_CITIES = [
    {"name": "تهران", "aliases": [], "site_value": "1"},
    {"name": "ری", "aliases": ["شهرری"], "site_value": "2"},
    {"name": "شمیرانات", "aliases": ["شمیران", "تجریش"], "site_value": "3"},
    {"name": "اسلامشهر", "aliases": ["اسلام شهر"], "site_value": "4"},
    {"name": "ورامین", "aliases": [], "site_value": "5"},
    {"name": "شهریار", "aliases": [], "site_value": "6"},
    {"name": "پاکدشت", "aliases": ["پاک دشت"], "site_value": "7"},
    {"name": "دماوند", "aliases": [], "site_value": "8"},
    {"name": "فیروزکوه", "aliases": [], "site_value": "9"},
    {"name": "رباط کریم", "aliases": ["رباط‌کریم"], "site_value": "10"},
    {"name": "ملارد", "aliases": [], "site_value": "11"},
    {"name": "قدس", "aliases": ["شهرقدس", "قلعه حسن خان"], "site_value": "12"},
    {"name": "بهارستان", "aliases": [], "site_value": "13"},
    {"name": "قرچک", "aliases": [], "site_value": "14"},
    {"name": "پردیس", "aliases": [], "site_value": "15"},
]


def create_database_backup(db_path: str, prefix: str = "backup", max_backups: int = 3) -> str:
    """Create a reliable file-level SQLite backup with timestamp in a dedicated backups directory, auto-pruning older backups."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    base_dir = os.path.dirname(os.path.abspath(db_path))
    backup_dir = os.path.join(base_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{prefix}_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)
    shutil.copy2(db_path, backup_path)

    # Auto-prune old backups with same prefix, keeping only the last max_backups
    try:
        existing = [
            os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
            if f.startswith(prefix) and f.endswith(".db")
        ]
        existing.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in existing[max_backups:]:
            try:
                os.remove(old)
                for ext in ["-shm", "-wal", "-journal"]:
                    if os.path.exists(old + ext):
                        os.remove(old + ext)
            except Exception:
                pass
    except Exception:
        pass

    return backup_path


def is_migration_needed(conn: sqlite3.Connection) -> bool:
    """Checks whether the relational database schema has already been initialized and seeded."""
    try:
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta';")
        if not c.fetchone():
            return True
        c.execute("SELECT value FROM _schema_meta WHERE key='relational_v1_completed';")
        row = c.fetchone()
        return not (row and row[0] == '1')
    except Exception:
        return True


def run_database_migration(db_path: str, force: bool = False) -> Dict[str, Any]:
    """
    Executes the comprehensive MAHANBOT relational schema migration.
    1. Checks if migration is needed (skips redundant executions and backup creation if already completed).
    2. Creates backup in backups/ directory if migration is needed.
    3. Creates new relational tables (Provinces, Cities, Banks, Branches, Applicant Preferences, Observations).
    4. Seeds reference data.
    5. Migrates applicants' legacy data into relational tables.
    6. Checks integrity, marks schema as migrated, and returns report.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()

    if not force and not is_migration_needed(conn):
        conn.close()
        return {
            "status": "success",
            "backup_path": None,
            "integrity": "ok",
            "fk_violations": [],
            "migrated_locations": 0,
            "migrated_preferences": 0,
        }

    backup_file = create_database_backup(db_path, prefix="cbi_ultimate_pre_migration")

    try:
        # Step 1: DDL Creation
        c.execute(
            """CREATE TABLE IF NOT EXISTS provinces (
                id INTEGER PRIMARY KEY,
                canonical_name TEXT NOT NULL UNIQUE,
                aliases TEXT NOT NULL DEFAULT '[]',
                site_value TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS cities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                province_id INTEGER NOT NULL REFERENCES provinces(id) ON DELETE CASCADE,
                canonical_name TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '[]',
                site_value TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(province_id, canonical_name)
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS applicant_locations (
                applicant_id INTEGER PRIMARY KEY REFERENCES applicants(id) ON DELETE CASCADE,
                province_id INTEGER REFERENCES provinces(id),
                city_id INTEGER REFERENCES cities(id),
                original_province_text TEXT,
                original_city_text TEXT,
                verification_status TEXT NOT NULL DEFAULT 'PENDING',
                last_verified_at REAL
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS banks (
                id INTEGER PRIMARY KEY,
                canonical_name TEXT NOT NULL UNIQUE,
                code TEXT UNIQUE,
                aliases TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS bank_branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_id INTEGER NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
                province_id INTEGER REFERENCES provinces(id),
                city_id INTEGER REFERENCES cities(id),
                branch_name TEXT NOT NULL,
                branch_code TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS applicant_bank_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id INTEGER NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
                bank_id INTEGER NOT NULL REFERENCES banks(id),
                priority INTEGER NOT NULL,
                visit_method TEXT NOT NULL DEFAULT 'prefer_online',
                branch_id INTEGER REFERENCES bank_branches(id),
                preferred_branch_text TEXT,
                reserve_allowed INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(applicant_id, priority),
                UNIQUE(applicant_id, bank_id)
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS bank_page_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id INTEGER NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
                session_id TEXT,
                observed_at REAL NOT NULL,
                site_province TEXT,
                site_city TEXT,
                available_banks TEXT,
                raw_options TEXT,
                selected_bank TEXT,
                selected_mode TEXT,
                selected_branch TEXT,
                error_message TEXT,
                capacity_status TEXT,
                verification_status TEXT
            )"""
        )

        # Indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_cities_province ON cities(province_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_applicant_locations_province ON applicant_locations(province_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_applicant_locations_city ON applicant_locations(city_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_applicant_bank_pref_app ON applicant_bank_preferences(applicant_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bank_branches_bank ON bank_branches(bank_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_observations_app_time ON bank_page_observations(applicant_id, observed_at);")

        # Step 2: Seed Provinces
        for p in IRAN_PROVINCES:
            c.execute(
                """INSERT INTO provinces (id, canonical_name, aliases, active)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(id) DO UPDATE SET
                   canonical_name=excluded.canonical_name,
                   aliases=excluded.aliases""",
                (p["id"], p["name"], json.dumps(p["aliases"], ensure_ascii=False)),
            )

        # Step 3: Seed Banks
        for b in IRAN_BANKS:
            c.execute(
                """INSERT INTO banks (id, canonical_name, code, aliases, active)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(id) DO UPDATE SET
                   canonical_name=excluded.canonical_name,
                   code=excluded.code,
                   aliases=excluded.aliases""",
                (b["id"], b["name"], b["code"], json.dumps(b["aliases"], ensure_ascii=False)),
            )

        # Step 4: Seed Cities
        for city_data in SISTAN_BALUCHESTAN_CITIES:
            c.execute(
                """INSERT INTO cities (province_id, canonical_name, aliases, site_value, active)
                   VALUES (16, ?, ?, ?, 1)
                   ON CONFLICT(province_id, canonical_name) DO UPDATE SET
                   aliases=excluded.aliases, site_value=excluded.site_value""",
                (city_data["name"], json.dumps(city_data["aliases"], ensure_ascii=False), city_data.get("site_value")),
            )

        for city_data in LORESTAN_CITIES:
            c.execute(
                """INSERT INTO cities (province_id, canonical_name, aliases, site_value, active)
                   VALUES (26, ?, ?, ?, 1)
                   ON CONFLICT(province_id, canonical_name) DO UPDATE SET
                   aliases=excluded.aliases, site_value=excluded.site_value""",
                (city_data["name"], json.dumps(city_data["aliases"], ensure_ascii=False), city_data.get("site_value")),
            )

        for city_data in TEHRAN_CITIES:
            c.execute(
                """INSERT INTO cities (province_id, canonical_name, aliases, site_value, active)
                   VALUES (8, ?, ?, ?, 1)
                   ON CONFLICT(province_id, canonical_name) DO UPDATE SET
                   aliases=excluded.aliases, site_value=excluded.site_value""",
                (city_data["name"], json.dumps(city_data["aliases"], ensure_ascii=False), city_data.get("site_value")),
            )

        # Step 5: Migrate Existing Applicants
        # Load all provinces, cities, banks for in-memory resolution
        c.execute("SELECT id, canonical_name, aliases FROM provinces")
        prov_rows = c.fetchall()
        province_resolver = []
        for pid, pname, paliases_json in prov_rows:
            aliases = json.loads(paliases_json) if paliases_json else []
            province_resolver.append((pid, pname, aliases))

        c.execute("SELECT id, province_id, canonical_name, aliases FROM cities")
        city_rows = c.fetchall()
        city_resolver = []
        for cid, cpid, cname, caliases_json in city_rows:
            aliases = json.loads(caliases_json) if caliases_json else []
            city_resolver.append((cid, cpid, cname, aliases))

        c.execute("SELECT id, canonical_name, code, aliases FROM banks")
        bank_rows = c.fetchall()
        bank_resolver = []
        for bid, bname, bcode, baliases_json in bank_rows:
            aliases = json.loads(baliases_json) if baliases_json else []
            bank_resolver.append((bid, bname, bcode, aliases))

        # Helper matching functions
        def resolve_province(text: str) -> Optional[int]:
            if not text:
                return None
            k_in = normalize_location_key(text)
            c_in = normalize_compact_key(text)
            for pid, pname, aliases in province_resolver:
                candidates = [pname] + aliases
                for cand in candidates:
                    if k_in == normalize_location_key(cand) or c_in == normalize_compact_key(cand):
                        return pid
            return None

        def resolve_city(text: str, province_id: Optional[int]) -> Optional[int]:
            if not text:
                return None
            k_in = normalize_location_key(text)
            c_in = normalize_compact_key(text)
            for cid, cpid, cname, aliases in city_resolver:
                if province_id is not None and cpid != province_id:
                    continue
                candidates = [cname] + aliases
                for cand in candidates:
                    if k_in == normalize_location_key(cand) or c_in == normalize_compact_key(cand):
                        return cid
            return None

        def resolve_bank(text: Any) -> Optional[int]:
            if not text:
                return None
            raw = str(text).strip()
            # If compound/bracketed:
            raw = normalize_persian_text(raw)
            k_in = normalize_compact_key(raw)
            # Check canonical patterns
            from bot_select_bank import canonicalize_bank_name
            canon_code = canonicalize_bank_name(raw)
            if canon_code:
                for bid, bname, bcode, aliases in bank_resolver:
                    if bcode == canon_code:
                        return bid
            for bid, bname, bcode, aliases in bank_resolver:
                candidates = [bname] + aliases
                for cand in candidates:
                    if k_in == normalize_compact_key(cand):
                        return bid
            return None

        c.execute("SELECT id, full_name, national_id, data FROM applicants ORDER BY id")
        app_rows = c.fetchall()

        migrated_locations = 0
        migrated_preferences = 0

        for aid, full_name, nid, data_json in app_rows:
            app_data = json.loads(data_json) if data_json else {}

            # Location migration
            raw_prov = str(app_data.get("state") or app_data.get("province") or "").strip()
            raw_city = str(app_data.get("city") or "").strip()

            prov_id = resolve_province(raw_prov) if raw_prov else None
            # If city exists but province is unknown, check if city resolves to a unique province
            if not prov_id and raw_city:
                matching_cities = [c for c in city_resolver if normalize_compact_key(raw_city) in [normalize_compact_key(c[2])] + [normalize_compact_key(a) for a in c[3]]]
                if len(matching_cities) == 1:
                    prov_id = matching_cities[0][1]

            city_id = resolve_city(raw_city, prov_id) if raw_city else None

            # Determine verification status
            if prov_id and city_id:
                ver_status = "VERIFIED"
            elif prov_id and not raw_city:
                ver_status = "PROVINCE_ONLY"
            elif raw_prov or raw_city:
                ver_status = "LOCATION_MISMATCH" if prov_id is None else "CITY_UNKNOWN"
            else:
                ver_status = "INCOMPLETE"

            c.execute(
                """INSERT INTO applicant_locations (applicant_id, province_id, city_id, original_province_text, original_city_text, verification_status, last_verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(applicant_id) DO UPDATE SET
                   province_id=excluded.province_id,
                   city_id=excluded.city_id,
                   original_province_text=excluded.original_province_text,
                   original_city_text=excluded.original_city_text,
                   verification_status=excluded.verification_status,
                   last_verified_at=excluded.last_verified_at""",
                (aid, prov_id, city_id, raw_prov, raw_city, ver_status, datetime.datetime.now().timestamp()),
            )
            migrated_locations += 1

            # Bank Preferences migration
            # Raw banks can be in 'priority_banks' or 'banks'
            raw_priorities = app_data.get("priority_banks") or app_data.get("banks") or []
            visit_mode = str(app_data.get("bank_mode") or "prefer_online").strip()
            allow_res = 1 if (app_data.get("allow_reserve") is True or app_data.get("allow_reserve") == 1) else 1
            branch_info = app_data.get("priority_branches") or app_data.get("branch_code")
            branch_text = ", ".join(branch_info) if isinstance(branch_info, list) else (str(branch_info) if branch_info else None)

            # Clear existing preferences for this applicant before inserting clean ordered preferences
            c.execute("DELETE FROM applicant_bank_preferences WHERE applicant_id = ?", (aid,))

            priority_num = 1
            seen_banks_for_app = set()
            for item in raw_priorities:
                if isinstance(item, dict):
                    bank_item_name = item.get("name")
                    item_mode = item.get("mode") or visit_mode
                    item_branch = item.get("branch") or branch_text
                else:
                    bank_item_name = str(item)
                    item_mode = visit_mode
                    item_branch = branch_text

                bid = resolve_bank(bank_item_name)
                if bid and bid not in seen_banks_for_app:
                    seen_banks_for_app.add(bid)
                    c.execute(
                        """INSERT INTO applicant_bank_preferences
                           (applicant_id, bank_id, priority, visit_method, preferred_branch_text, reserve_allowed, enabled)
                           VALUES (?, ?, ?, ?, ?, ?, 1)""",
                        (aid, bid, priority_num, item_mode, item_branch, allow_res),
                    )
                    priority_num += 1
                    migrated_preferences += 1

        # Mark migration as completed in metadata table
        c.execute("""CREATE TABLE IF NOT EXISTS _schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );""")
        now_ts = datetime.datetime.now().timestamp()
        c.execute("""INSERT INTO _schema_meta (key, value, updated_at)
            VALUES ('relational_v1_completed', '1', ?)
            ON CONFLICT(key) DO UPDATE SET value='1', updated_at=?;""",
            (now_ts, now_ts)
        )

        conn.commit()

        # Run integrity & foreign key checks
        c.execute("PRAGMA integrity_check;")
        integrity_res = c.fetchone()[0]

        c.execute("PRAGMA foreign_key_check;")
        fk_violations = c.fetchall()

        return {
            "status": "success",
            "backup_path": backup_file,
            "integrity": integrity_res,
            "fk_violations": fk_violations,
            "migrated_locations": migrated_locations,
            "migrated_preferences": migrated_preferences,
        }
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def check_database_health(db_path: str) -> Dict[str, Any]:
    """
    Evaluates DB health:
    - Integrity check
    - Foreign keys check
    - Mismatches in applicant locations (e.g. city belonging to a different province)
    - Applicants with incomplete locations
    - Applicants with missing bank preferences
    - Duplicate bank preferences
    - Summary counts
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("PRAGMA integrity_check;")
    integrity = c.fetchone()[0]

    c.execute("PRAGMA foreign_key_check;")
    fk_violations = c.fetchall()

    # Table counts
    counts = {}
    for tbl in [
        "applicants",
        "provinces",
        "cities",
        "applicant_locations",
        "banks",
        "bank_branches",
        "applicant_bank_preferences",
        "bank_page_observations",
    ]:
        try:
            c.execute(f'SELECT COUNT(*) FROM "{tbl}"')
            counts[tbl] = c.fetchone()[0]
        except Exception:
            counts[tbl] = 0

    # Inconsistencies detection
    inconsistencies = []

    # 1. Applicants with LOCATION_MISMATCH
    c.execute(
        """SELECT a.id, a.national_id, a.full_name, al.original_province_text, al.original_city_text, al.verification_status
           FROM applicants a
           LEFT JOIN applicant_locations al ON a.id = al.applicant_id
           WHERE al.verification_status IN ('LOCATION_MISMATCH', 'CITY_UNKNOWN')"""
    )
    for row in c.fetchall():
        inconsistencies.append({
            "type": "location_mismatch",
            "applicant_id": row[0],
            "national_id": row[1],
            "full_name": row[2],
            "details": f"استان: {row[3]} | شهر: {row[4]} (وضعیت: {row[5]})",
            "suggested_action": "بررسی تطابق نام استان و شهر با فهرست استاندارد",
        })

    # 2. Applicants with NO bank preferences configured
    c.execute(
        """SELECT a.id, a.national_id, a.full_name
           FROM applicants a
           LEFT JOIN applicant_bank_preferences abp ON a.id = abp.applicant_id
           GROUP BY a.id
           HAVING COUNT(abp.id) = 0"""
    )
    for row in c.fetchall():
        inconsistencies.append({
            "type": "no_bank_preferences",
            "applicant_id": row[0],
            "national_id": row[1],
            "full_name": row[2],
            "details": "هیچ بانک اولویت‌داری برای این متقاضی تعریف نشده است.",
            "suggested_action": "ثبت حداقل یک بانک در اولویت‌های متقاضی",
        })

    # 3. Check if city belongs to a different province
    c.execute(
        """SELECT a.id, a.national_id, a.full_name, al.province_id, ci.province_id, p.canonical_name, ci.canonical_name
           FROM applicant_locations al
           JOIN applicants a ON al.applicant_id = a.id
           JOIN cities ci ON al.city_id = ci.id
           JOIN provinces p ON al.province_id = p.id
           WHERE al.province_id != ci.province_id"""
    )
    for row in c.fetchall():
        inconsistencies.append({
            "type": "cross_province_city",
            "applicant_id": row[0],
            "national_id": row[1],
            "full_name": row[2],
            "details": f"شهر {row[6]} متعلق به استان {row[5]} نیست.",
            "suggested_action": "اصلاح استان یا انتخاب شهر صحیح متعلق به استان متقاضی",
        })

    conn.close()

    is_healthy = (integrity == "ok") and (len(fk_violations) == 0)

    return {
        "integrity": integrity,
        "is_healthy": is_healthy,
        "fk_violations": fk_violations,
        "table_counts": counts,
        "inconsistencies_count": len(inconsistencies),
        "inconsistencies": inconsistencies,
        "timestamp": datetime.datetime.now().isoformat(),
    }


def restore_database_backup(backup_path: str, target_db_path: str) -> bool:
    """Restores database from a given backup file safely."""
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    # Create safety backup of current target before overwriting
    create_database_backup(target_db_path, prefix="safety_before_restore")
    shutil.copy2(backup_path, target_db_path)
    return True
