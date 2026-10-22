"""
core/applicant_config_service.py — Single source of truth for applicant settings,
locations, priority bank matching, visit methods, branches, and live DOM observations.
"""
import datetime
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.persian_normalizer import (
    is_special_location_case,
    match_location_name,
    normalize_compact_key,
    normalize_location_key,
    normalize_national_id,
    normalize_persian_text,
)


@dataclass
class BankPreference:
    bank_id: int
    bank_name: str
    bank_code: str
    priority: int
    visit_method: str = "prefer_online"
    branch_id: Optional[int] = None
    preferred_branch_text: Optional[str] = None
    reserve_allowed: bool = True
    enabled: bool = True


@dataclass
class ApplicantConfig:
    applicant_id: int
    national_id: str
    full_name: str
    province_id: Optional[int] = None
    province_name: str = ""
    site_province_value: Optional[str] = None
    city_id: Optional[int] = None
    city_name: str = ""
    site_city_value: Optional[str] = None
    location_status: str = "PENDING"
    is_location_valid: bool = False
    priority_banks: List[BankPreference] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def get_preferred_bank_names(self) -> List[str]:
        return [b.bank_name for b in self.priority_banks if b.enabled]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["preferred_banks"] = self.get_preferred_bank_names()
        return d


class ApplicantConfigurationService:
    """
    Central service providing authoritative applicant configurations,
    verifying locations, matching bank priorities against DOM options,
    and recording verified live DOM observations.
    """

    def __init__(self, db_path: str = "cbi_ultimate.db"):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_applicant_config(self, nid_or_id: Any) -> Optional[ApplicantConfig]:
        """
        Loads the authoritative configuration for an applicant by National ID or internal ID.
        Combines relational records (applicant_locations, applicant_bank_preferences, banks, provinces, cities)
        with fallback to applicant JSON data if migration is pending.
        """
        clean_nid = normalize_national_id(str(nid_or_id))
        is_numeric_id = str(nid_or_id).isdigit() and len(str(nid_or_id)) < 8

        conn = self._get_connection()
        try:
            c = conn.cursor()
            if is_numeric_id:
                c.execute("SELECT id, full_name, national_id, status, data FROM applicants WHERE id = ?", (int(nid_or_id),))
            else:
                c.execute("SELECT id, full_name, national_id, status, data FROM applicants WHERE national_id = ?", (clean_nid,))

            app_row = c.fetchone()
            if not app_row:
                return None

            aid = app_row["id"]
            nid = app_row["national_id"]
            full_name = app_row["full_name"] or ""
            raw_data = json.loads(app_row["data"]) if app_row["data"] else {}

            # 1. Fetch location from applicant_locations table
            c.execute(
                """SELECT al.province_id, al.city_id, al.original_province_text, al.original_city_text,
                          al.verification_status, p.canonical_name AS prov_name, p.site_value AS prov_site_val,
                          ci.canonical_name AS city_name, ci.site_value AS city_site_val
                   FROM applicant_locations al
                   LEFT JOIN provinces p ON al.province_id = p.id
                   LEFT JOIN cities ci ON al.city_id = ci.id
                   WHERE al.applicant_id = ?""",
                (aid,),
            )
            loc_row = c.fetchone()

            province_id = None
            province_name = ""
            site_province_val = None
            city_id = None
            city_name = ""
            site_city_val = None
            loc_status = "PENDING"

            if loc_row:
                province_id = loc_row["province_id"]
                province_name = loc_row["prov_name"] or loc_row["original_province_text"] or ""
                site_province_val = loc_row["prov_site_val"]
                city_id = loc_row["city_id"]
                city_name = loc_row["city_name"] or loc_row["original_city_text"] or ""
                site_city_val = loc_row["city_site_val"]
                loc_status = loc_row["verification_status"]
            else:
                # Fallback to JSON data if relational record not yet populated
                province_name = raw_data.get("state") or raw_data.get("province") or ""
                city_name = raw_data.get("city") or ""
                loc_status = "PENDING"

            is_loc_valid = (loc_status == "VERIFIED") or bool(province_name and city_name)

            # 2. Fetch Bank Preferences
            c.execute(
                """SELECT abp.bank_id, b.canonical_name, b.code, abp.priority, abp.visit_method,
                          abp.branch_id, abp.preferred_branch_text, abp.reserve_allowed, abp.enabled
                   FROM applicant_bank_preferences abp
                   JOIN banks b ON abp.bank_id = b.id
                   WHERE abp.applicant_id = ? AND abp.enabled = 1
                   ORDER BY abp.priority ASC""",
                (aid,),
            )
            pref_rows = c.fetchall()

            priority_banks: List[BankPreference] = []
            if pref_rows:
                for pr in pref_rows:
                    priority_banks.append(
                        BankPreference(
                            bank_id=pr["bank_id"],
                            bank_name=pr["canonical_name"],
                            bank_code=pr["code"],
                            priority=pr["priority"],
                            visit_method=pr["visit_method"] or "prefer_online",
                            branch_id=pr["branch_id"],
                            preferred_branch_text=pr["preferred_branch_text"],
                            reserve_allowed=bool(pr["reserve_allowed"]),
                            enabled=bool(pr["enabled"]),
                        )
                    )
            else:
                # Fallback from JSON data
                raw_banks = raw_data.get("priority_banks") or raw_data.get("banks") or []
                v_mode = raw_data.get("bank_mode") or "prefer_online"
                allow_res = bool(raw_data.get("allow_reserve", True))
                p_branches = raw_data.get("priority_branches") or raw_data.get("branch_code")
                p_branch_str = ", ".join(p_branches) if isinstance(p_branches, list) else (str(p_branches) if p_branches else None)

                for idx, b in enumerate(raw_banks, start=1):
                    b_name = b.get("name") if isinstance(b, dict) else str(b)
                    priority_banks.append(
                        BankPreference(
                            bank_id=idx,
                            bank_name=b_name,
                            bank_code=normalize_compact_key(b_name),
                            priority=idx,
                            visit_method=v_mode,
                            preferred_branch_text=p_branch_str,
                            reserve_allowed=allow_res,
                            enabled=True,
                        )
                    )

            return ApplicantConfig(
                applicant_id=aid,
                national_id=nid,
                full_name=full_name,
                province_id=province_id,
                province_name=province_name,
                site_province_value=site_province_val,
                city_id=city_id,
                city_name=city_name,
                site_city_value=site_city_val,
                location_status=loc_status,
                is_location_valid=is_loc_valid,
                priority_banks=priority_banks,
                raw_data=raw_data,
            )
        finally:
            conn.close()

    def validate_site_location(
        self,
        applicant_config: ApplicantConfig,
        site_province: str,
        site_city: str,
    ) -> Tuple[bool, str, str]:
        """
        Validates site's displayed province & city against applicant's configured location.
        Returns:
            (is_valid, status_code, diagnostic_message)
        """
        from core.persian_normalizer import normalize_location_key, normalize_compact_key

        expected_prov = applicant_config.province_name
        expected_city = applicant_config.city_name

        if not expected_prov and not expected_city:
            # Nothing defined in applicant config -> cannot strictly reject, but warn
            return True, "LOCATION_UNCONFIGURED", "اطلاعات استان و شهر متقاضی هنوز تنظیم نشده است."

        # Check Province
        if expected_prov and site_province:
            k_exp = normalize_location_key(expected_prov)
            k_site = normalize_location_key(site_province)
            c_exp = normalize_compact_key(expected_prov)
            c_site = normalize_compact_key(site_province)

            if k_exp != k_site and c_exp != c_site:
                # Check for special alias
                special = is_special_location_case(expected_prov, site_province)
                if special is not True:
                    return (
                        False,
                        "LOCATION_MISMATCH",
                        f"استان پرونده در صفحه زنده ({site_province}) با استان متقاضی ({expected_prov}) مغایرت دارد. عملیات متوقف شد.",
                    )

        # Check City
        if expected_city and site_city:
            k_exp_c = normalize_location_key(expected_city)
            k_site_c = normalize_location_key(site_city)
            c_exp_c = normalize_compact_key(expected_city)
            c_site_c = normalize_compact_key(site_city)

            special_c = is_special_location_case(expected_city, site_city)
            if special_c is False or (special_c is None and (k_exp_c != k_site_c and c_exp_c != c_site_c)):
                return (
                    False,
                    "LOCATION_MISMATCH",
                    f"شهر انتخاب‌شده در صفحه زنده ({site_city}) با شهر متقاضی ({expected_city}) مغایرت دارد. عملیات متوقف شد.",
                )

        return True, "VERIFIED", "استان و شهر در صفحه زنده با متقاضی تطابق دارند."

    def match_priority_banks_to_dom_options(
        self,
        applicant_config: ApplicantConfig,
        dom_options: List[Dict[str, Any]],
        *,
        allow_reserve_global: bool = True,
        sniper_mode_enabled: bool = False,
        capacity_sniping: bool = False,
    ) -> Dict[str, Any]:
        """
        Matches applicant's priority banks to live options extracted from #ctl00_ContentPlaceHolder1_ddlBankName.
        Extracts bank title, matches canonical identities, and determines:
          - chosen_bank: the highest priority candidate ready to be selected
          - status: 'MATCHED' | 'BANK_RESERVE_ONLY' | 'BANK_NOT_AVAILABLE' | 'NO_PREFERENCES'
          - matches summary
        """
        from bot_select_bank import canonicalize_bank_name, extract_option_bank_title, _is_reserve_option

        if not applicant_config.priority_banks:
            return {
                "status": "NO_PREFERENCES",
                "chosen_bank": None,
                "reason": "هیچ بانک اولویت‌داری برای متقاضی تعریف نشده است.",
                "available_options_count": len(dom_options),
            }

        # Filter out placeholder option (val=0 or empty)
        valid_options = [
            opt for opt in dom_options
            if str(opt.get("value") or opt.get("v") or "").strip() not in ("", "0")
        ]

        # Parse DOM options into structured items with canonical IDs
        parsed_options = []
        for opt in valid_options:
            val = str(opt.get("value") or opt.get("v") or "").strip()
            text = str(opt.get("text") or opt.get("t") or "").strip()
            is_reserve = _is_reserve_option(text) or bool(opt.get("is_reserve"))
            bank_title = extract_option_bank_title(text)
            canon_code = canonicalize_bank_name(bank_title)
            parsed_options.append({
                "value": val,
                "text": text,
                "title": bank_title,
                "canon_code": canon_code,
                "is_reserve": is_reserve,
                "is_selected": bool(opt.get("selected") or opt.get("s")),
            })

        # Match each priority bank in order of priority (1, 2, 3...)
        active_candidates = []
        reserve_candidates = []
        missing_banks = []

        for pref in applicant_config.priority_banks:
            if not pref.enabled:
                continue

            pref_canon = pref.bank_code or canonicalize_bank_name(pref.bank_name)
            matched_opts = [
                opt for opt in parsed_options
                if opt["canon_code"] == pref_canon
            ]

            if not matched_opts:
                missing_banks.append(pref.bank_name)
                continue

            # Evaluate effective reserve permission for this bank
            effective_allow_reserve = (
                pref.reserve_allowed and (allow_reserve_global or capacity_sniping or sniper_mode_enabled)
            )

            for opt in matched_opts:
                cand_info = {
                    "preference": pref,
                    "option": opt,
                    "value": opt["value"],
                    "bank_name": pref.bank_name,
                    "option_text": opt["text"],
                    "is_reserve": opt["is_reserve"],
                    "priority": pref.priority,
                    "visit_method": pref.visit_method,
                    "preferred_branch": pref.preferred_branch_text,
                }
                if opt["is_reserve"]:
                    if effective_allow_reserve:
                        reserve_candidates.append(cand_info)
                else:
                    active_candidates.append(cand_info)

        # Decision Logic:
        # 1. Prefer active candidate in order of priority
        if active_candidates:
            active_candidates.sort(key=lambda x: x["priority"])
            return {
                "status": "MATCHED",
                "chosen_bank": active_candidates[0],
                "candidates_count": len(active_candidates),
                "is_reserve": False,
                "reason": f"بانک با ظرفیت فعال «{active_candidates[0]['bank_name']}» (اولویت {active_candidates[0]['priority']}) انتخاب شد.",
            }

        # 2. If only reserve candidates exist and reserve is allowed
        if reserve_candidates:
            reserve_candidates.sort(key=lambda x: x["priority"])
            return {
                "status": "MATCHED",
                "chosen_bank": reserve_candidates[0],
                "candidates_count": len(reserve_candidates),
                "is_reserve": True,
                "reason": f"بانک در وضعیت رزرو «{reserve_candidates[0]['bank_name']}» (اولویت {reserve_candidates[0]['priority']}) در صف پایش سهمیه انتخاب شد.",
            }

        # 3. If matching bank was found but was reserve-only and reserve is disallowed
        has_any_matching_option = any(
            opt["canon_code"] in [p.bank_code for p in applicant_config.priority_banks]
            for opt in parsed_options
        )
        if has_any_matching_option:
            return {
                "status": "BANK_RESERVE_ONLY",
                "chosen_bank": None,
                "reason": "بانک‌های اولویت‌دار در وضعیت رزرو قرار دارند و پذیرش رزرو برای متقاضی غیرفعال است.",
            }

        # 4. Bank not available at all
        return {
            "status": "BANK_NOT_AVAILABLE",
            "chosen_bank": None,
            "missing_banks": missing_banks,
            "reason": f"هیچ‌یک از بانک‌های اولویت‌دار متقاضی ({', '.join(applicant_config.get_preferred_bank_names())}) در فهرست بانک‌های صفحه موجود نیستند.",
        }

    def record_bank_page_observation(
        self,
        applicant_id: int,
        session_id: str,
        site_province: str,
        site_city: str,
        available_banks: List[Dict[str, Any]],
        raw_options: List[Dict[str, Any]],
        *,
        selected_bank: str = "",
        selected_mode: str = "",
        selected_branch: str = "",
        error_message: str = "",
        capacity_status: str = "UNKNOWN",
        verification_status: str = "PENDING",
    ) -> int:
        """
        Records an exact timestamped DOM observation for page 3 into bank_page_observations.
        """
        conn = self._get_connection()
        try:
            c = conn.cursor()
            now = time.time()
            c.execute(
                """INSERT INTO bank_page_observations
                   (applicant_id, session_id, observed_at, site_province, site_city,
                    available_banks, raw_options, selected_bank, selected_mode,
                    selected_branch, error_message, capacity_status, verification_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    applicant_id,
                    session_id,
                    now,
                    site_province,
                    site_city,
                    json.dumps(available_banks, ensure_ascii=False),
                    json.dumps(raw_options, ensure_ascii=False),
                    selected_bank,
                    selected_mode,
                    selected_branch,
                    error_message,
                    capacity_status,
                    verification_status,
                ),
            )
            obs_id = c.lastrowid
            conn.commit()
            return obs_id
        finally:
            conn.close()

    def get_latest_observation(
        self, applicant_id: int, max_age_seconds: float = 60.0
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches the most recent verified observation for the applicant if within max_age_seconds.
        """
        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute(
                """SELECT * FROM bank_page_observations
                   WHERE applicant_id = ?
                   ORDER BY observed_at DESC LIMIT 1""",
                (applicant_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            age = time.time() - row["observed_at"]
            if age > max_age_seconds:
                return None  # Stale!

            obs = dict(row)
            obs["available_banks"] = json.loads(obs["available_banks"]) if obs["available_banks"] else []
            obs["raw_options"] = json.loads(obs["raw_options"]) if obs["raw_options"] else []
            obs["age_seconds"] = age
            return obs
        finally:
            conn.close()
