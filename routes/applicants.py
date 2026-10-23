"""
routes/applicants.py — Applicant CRUD, Excel import with leading-zero preservation, favorite banks, bank stop.
"""
import io
import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from database import DBHandler
from submission_policy import validate_submission_settings
from event_logger import EVENT_BROADCASTER, build_event, log_event
from core.deps import require_api_key
from core.helpers import (
    _normalize_nid,
    normalize_postal_code,
    normalize_mobile,
    _open_aiosqlite,
    _serialize_applicants,
)
from core.models import (
    ApplicantModel,
    FavoriteBanksRequest,
)

router = APIRouter()


@router.patch('/applicants/{nid}/submission-settings')
def update_submission_settings(nid: str, payload: Dict[str, Any], _: bool = Depends(require_api_key)):
    nid = _normalize_nid(nid)
    if not DBHandler.get_applicant(nid):
        raise HTTPException(status_code=404, detail='Applicant not found')
    if not payload or set(payload) - {'auto_final_submit', 'final_submit_interval_seconds'}:
        raise HTTPException(status_code=422, detail='Invalid submission settings')
    try:
        validate_submission_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not DBHandler.update_applicant_data(nid, payload):
        raise HTTPException(status_code=500, detail='Could not save submission settings')
    _emit_applicants_snapshot()
    return {'status': 'ok', 'submission_settings': payload}


def _emit_applicants_snapshot() -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("applicants_snapshot", applicants=_serialize_applicants())
    )


def parse_and_validate_excel_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse and validate applicant rows imported from Excel/CSV.
    Preserves leading zeros on national_id, spouse_national_id, child_national_id, postal_code/zip_code, and mobile.
    Returns (valid_records, error_rows).
    """
    valid_records: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []

    column_aliases = {
        "full_name": ("full_name", "name", "نام و نام خانوادگی", "نام", "متقاضی"),
        "national_id": ("national_id", "nid", "کد ملی", "کدملی", "شماره ملی"),
        "spouse_national_id": ("spouse_national_id", "spouse_nid", "کد ملی همسر", "کدملی همسر"),
        "child_national_id": ("child_national_id", "child_nid", "کد ملی فرزند", "کدملی فرزند"),
        "postal_code": ("postal_code", "zip_code", "zip", "کد پستی", "کدپستی"),
        "mobile": ("mobile", "phone_mobile", "موبایل", "شماره موبایل", "تلفن همراه"),
        "phone": ("phone", "tel", "تلفن ثابت", "تلفن"),
        "state": ("state", "province", "استان"),
        "city": ("city", "شهر"),
        "loan_type": ("loan_type", "نوع وام", "وام"),
        "priority_banks": ("priority_banks", "banks", "بانک", "بانک اولویت"),
        "bank_mode": ("bank_mode", "mode", "روش", "حضوری/غیرحضوری"),
        "allow_reserve": ("allow_reserve", "reserve", "بانک رزرو", "رزرو", "ذخیره"),
        "branch_code": ("branch_code", "branch", "شعبه", "کد شعبه"),
    }

    def _get_field(row_dict: Dict[str, Any], key: str) -> Any:
        for alias in column_aliases.get(key, (key,)):
            if alias in row_dict and row_dict[alias] is not None and str(row_dict[alias]).strip() != "":
                return row_dict[alias]
        return ""

    for idx, raw_row in enumerate(rows, start=1):
        row_errors: List[str] = []
        full_name = str(_get_field(raw_row, "full_name") or "").strip()
        nid = _normalize_nid(_get_field(raw_row, "national_id"))
        spouse_nid = _normalize_nid(_get_field(raw_row, "spouse_national_id"))
        child_nid = _normalize_nid(_get_field(raw_row, "child_national_id"))
        postal_code = normalize_postal_code(_get_field(raw_row, "postal_code"))
        mobile = normalize_mobile(_get_field(raw_row, "mobile"))
        phone = str(_get_field(raw_row, "phone") or "").strip()
        state = str(_get_field(raw_row, "state") or "").strip()
        city = str(_get_field(raw_row, "city") or "").strip()
        loan_type_raw = str(_get_field(raw_row, "loan_type") or "marriage").strip().lower()
        if loan_type_raw in {"فرزند", "فرزندآوری", "child", "childbearing"}:
            loan_type = "childbearing"
        else:
            loan_type = "marriage"

        if not full_name:
            row_errors.append("نام و نام خانوادگی خالی است")
        if not nid or not nid.isdigit() or len(nid) != 10:
            row_errors.append("کد ملی نامعتبر یا ناقص است (باید ۱۰ رقم باشد)")
        if spouse_nid and (not spouse_nid.isdigit() or len(spouse_nid) != 10):
            row_errors.append("کد ملی همسر باید ۱۰ رقم باشد")
        if child_nid and (not child_nid.isdigit() or len(child_nid) != 10):
            row_errors.append("کد ملی فرزند باید ۱۰ رقم باشد")
        if postal_code and (not postal_code.isdigit() or len(postal_code) != 10):
            row_errors.append("کد پستی باید دقیقاً ۱۰ رقم باشد")
        if mobile and (not mobile.isdigit() or len(mobile) != 11 or not mobile.startswith("09")):
            row_errors.append("شماره موبایل نامعتبر است (۱۱ رقم با ۰۹)")

        if row_errors:
            error_rows.append({"row": idx, "full_name": full_name or "-", "errors": row_errors})
            continue

        raw_banks = _get_field(raw_row, "priority_banks")
        if isinstance(raw_banks, list):
            banks_list = [str(b).strip() for b in raw_banks if str(b).strip()]
        elif raw_banks:
            banks_list = [b.strip() for b in str(raw_banks).replace("،", ",").split(",") if b.strip()]
        else:
            banks_list = []

        bank_mode = str(_get_field(raw_row, "bank_mode") or "").strip()
        branch_code = str(_get_field(raw_row, "branch_code") or "").strip()

        raw_allow_reserve = _get_field(raw_row, "allow_reserve")
        allow_reserve = None
        if raw_allow_reserve is not None and str(raw_allow_reserve).strip() != "":
            val_str = str(raw_allow_reserve).strip().lower()
            if val_str in ("true", "1", "بله", "دارد", "فعال"):
                allow_reserve = True
            elif val_str in ("false", "0", "خیر", "ندارد", "غیرفعال"):
                allow_reserve = False

        data_payload = {
            "mobile": mobile,
            "phone": phone,
            "spouse_national_id": spouse_nid,
            "child_national_id": child_nid,
            "zip_code": postal_code,
            "postal_code": postal_code,
            "state": state,
            "city": city,
            "loan_type": loan_type,
            "priority_banks": banks_list,
            "banks": banks_list,
            "bank_mode": bank_mode,
            "branch_code": branch_code,
            "allow_reserve": allow_reserve,
        }
        valid_records.append({
            "full_name": full_name,
            "national_id": nid,
            "data": data_payload,
        })

    return valid_records, error_rows


# ── Applicant endpoints ──────────────────────────────────────────────
@router.get("/")
def serve_index():
    from core.state import STATIC_DIR
    import os
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("index.html not found")


@router.get("/applicants")
def get_applicants():
    try:
        return _serialize_applicants()
    except Exception as exc:
        import logging
        logger = logging.getLogger("mahanbot")
        logger.warning("Failed to fetch applicants: %s", exc)
        return []


@router.post("/applicants")
async def save_applicant(req: ApplicantModel):
    try:
        national_id = _normalize_nid(req.national_id)
        if not national_id.isdigit() or len(national_id) != 10:
            raise HTTPException(status_code=422, detail="کد ملی باید دقیقاً ۱۰ رقم باشد")
        applicant_data = dict(req.data or {})
        try:
            validate_submission_settings(applicant_data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        spouse_national_id = _normalize_nid(applicant_data.get("spouse_national_id"))
        if spouse_national_id and (not spouse_national_id.isdigit() or len(spouse_national_id) != 10):
            raise HTTPException(status_code=422, detail="کد ملی همسر باید دقیقاً ۱۰ رقم باشد")
        applicant_data["spouse_national_id"] = spouse_national_id

        child_national_id = _normalize_nid(applicant_data.get("child_national_id"))
        if child_national_id and (not child_national_id.isdigit() or len(child_national_id) != 10):
            raise HTTPException(status_code=422, detail="کد ملی فرزند باید دقیقاً ۱۰ رقم باشد")
        if child_national_id:
            applicant_data["child_national_id"] = child_national_id

        raw_zip = applicant_data.get("postal_code") or applicant_data.get("zip_code")
        postal_code = normalize_postal_code(raw_zip)
        if postal_code and (not postal_code.isdigit() or len(postal_code) != 10):
            raise HTTPException(status_code=422, detail="کد پستی باید دقیقاً ۱۰ رقم باشد")
        applicant_data["zip_code"] = postal_code
        applicant_data["postal_code"] = postal_code

        if applicant_data.get("mobile"):
            applicant_data["mobile"] = normalize_mobile(applicant_data.get("mobile"))

        d_str = json.dumps(applicant_data, ensure_ascii=False)
        async with _open_aiosqlite() as conn:
            if req.id:
                await conn.execute(
                    "UPDATE applicants SET full_name=?, national_id=?, data=? WHERE id=?",
                    (req.full_name, national_id, d_str, req.id),
                )
            else:
                await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?) "
                    "ON CONFLICT(national_id) DO UPDATE SET full_name=excluded.full_name, data=excluded.data",
                    (req.full_name, national_id, d_str),
                )
            await conn.commit()
        _emit_applicants_snapshot()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@router.post("/applicants/import-excel")
async def import_applicants_excel(file: UploadFile = File(...)):
    """Import applicants from an Excel (.xlsx) file while preserving leading zeros on NID and postal code."""
    try:
        import openpyxl
        content = await file.read()
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            return {"status": "error", "msg": "فایل اکسل خالی است", "imported": 0, "errors": []}
        headers = [str(h or "").strip() for h in all_rows[0]]
        dict_rows = []
        for row_vals in all_rows[1:]:
            if not any(v is not None and str(v).strip() != "" for v in row_vals):
                continue
            row_dict = {headers[i]: row_vals[i] for i in range(min(len(headers), len(row_vals))) if headers[i]}
            dict_rows.append(row_dict)

        valid_records, error_rows = parse_and_validate_excel_rows(dict_rows)
        async with _open_aiosqlite() as conn:
            for rec in valid_records:
                d_str = json.dumps(rec["data"], ensure_ascii=False)
                await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?) "
                    "ON CONFLICT(national_id) DO UPDATE SET full_name=excluded.full_name, data=excluded.data",
                    (rec["full_name"], rec["national_id"], d_str),
                )
            await conn.commit()
        _emit_applicants_snapshot()
        return {
            "status": "ok",
            "imported": len(valid_records),
            "errors": error_rows,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"خطا در خواندن فایل اکسل: {exc}")


@router.post("/applicants/{nid}/favorite-banks")
def update_favorite_banks(nid: str, req: FavoriteBanksRequest, _: bool = Depends(require_api_key)):
    success = DBHandler.update_applicant_data(nid, {"favorite_banks": req.banks})
    if not success:
        raise HTTPException(status_code=404, detail="Applicant not found")
    _emit_applicants_snapshot()
    return {"status": "ok", "banks": req.banks}


@router.post("/applicants/{nid}/clear-tracking")
def clear_applicant_tracking(nid: str, _: bool = Depends(require_api_key)):
    cleared = DBHandler.clear_request_data(nid, "کد رهگیری و وضعیت قبلی جهت ثبت‌نام مجدد پاکسازی شد")
    if not cleared:
        raise HTTPException(status_code=404, detail="Applicant not found")
    log_event(nid, None, "سوابق ثبت‌نام و کد رهگیری قبلی پاکسازی شد؛ متقاضی آماده ثبت‌نام مجدد است.", "info")
    _emit_applicants_snapshot()
    return {"status": "ok", "nid": nid}


@router.delete("/applicants/{nid}")
async def delete_applicant(nid: str, _: bool = Depends(require_api_key)):
    async with _open_aiosqlite() as conn:
        await conn.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
        await conn.commit()
    _emit_applicants_snapshot()
    return {"status": "deleted"}


@router.post("/applicants/{nid}/banks/stop")
def stop_bank(nid: str, payload: Dict[str, Any], _: bool = Depends(require_api_key)):
    bank_name = (payload.get("bank") or "").strip()
    if not bank_name:
        raise HTTPException(status_code=400, detail="Bank name required")
    success = DBHandler.add_stopped_bank(nid, bank_name)
    if not success:
        raise HTTPException(status_code=404, detail="Applicant not found")
    log_event(nid, None, f"⛔ توقف بانک: {bank_name}", "warning")
    _emit_applicants_snapshot()
    return {"status": "ok", "bank": bank_name}


@router.get("/api/geo/provinces")
def get_geo_provinces():
    try:
        from core.db_migration import IRAN_PROVINCES
        return [{"id": p["id"], "name": p["name"]} for p in IRAN_PROVINCES]
    except Exception:
        return []


@router.get("/api/geo/cities")
def get_geo_cities(province: Optional[str] = None, province_id: Optional[int] = None):
    try:
        from core.db_migration import IRAN_PROVINCES, SISTAN_BALUCHESTAN_CITIES, LORESTAN_CITIES, TEHRAN_CITIES
        p_id = province_id
        if not p_id and province:
            norm_prov = province.strip()
            for p in IRAN_PROVINCES:
                if p["name"] == norm_prov or norm_prov in p.get("aliases", []):
                    p_id = p["id"]
                    break

        cities_map = {
            16: SISTAN_BALUCHESTAN_CITIES,
            26: LORESTAN_CITIES,
            8: TEHRAN_CITIES,
        }
        if p_id in cities_map:
            return [{"name": c["name"]} for c in cities_map[p_id]]

        import sqlite3, database
        conn = sqlite3.connect(database.DB_PATH)
        cur = conn.cursor()
        if p_id:
            rows = cur.execute("SELECT canonical_name FROM cities WHERE province_id=? ORDER BY canonical_name", (p_id,)).fetchall()
        elif province:
            rows = cur.execute("""
                SELECT c.canonical_name FROM cities c 
                JOIN provinces p ON c.province_id = p.id 
                WHERE p.canonical_name=? OR p.aliases LIKE ? 
                ORDER BY c.canonical_name
            """, (province, f"%{province}%")).fetchall()
        else:
            rows = cur.execute("SELECT canonical_name FROM cities ORDER BY canonical_name LIMIT 100").fetchall()
        conn.close()
        if rows:
            return [{"name": r[0]} for r in rows]
        return []
    except Exception:
        return []


@router.get("/api/geo/banks")
def get_geo_banks():
    try:
        from core.db_migration import IRAN_BANKS
        return [{"id": b["id"], "name": b["name"], "code": b["code"]} for b in IRAN_BANKS]
    except Exception:
        return []
