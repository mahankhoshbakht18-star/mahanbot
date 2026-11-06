"""
core/saved_page_analyzer.py — Specialized Read-Only Inspector for Saved Page 3 HTMLs.

Audits static HTML files stored in saved_pages/ to extract form structure,
controls, ASP.NET WebForms tokens, and bank options at time of capture.

STRICT PRINCIPLES:
- Strictly Read-Only (no network requests, no replay, no state mutation).
- Never leaks raw secrets (no raw ViewState, EventValidation, cookies, OTPs, or NID).
- Enforces the 3-Way Reality Distinction:
  1. LOCAL_SAVED_FILE: Local filesystem rendering (file:///).
  2. LIVE_NETWORK_REQUEST: Real HTTP transport returning fresh delta.
  3. AUTHENTICATED_SERVER_ACCEPTANCE: Server accepts credentials and commits transaction.
"""

import os
import re
import glob
from typing import Any, Dict, List, Optional


class SavedPage3Analyzer:
    """Read-only static inspector for saved ASP.NET WebForms HTML pages."""

    @staticmethod
    def classify_source_mode(url_or_path: str) -> str:
        """Categorize page source to avoid confusing local files with live server sessions."""
        s = str(url_or_path or "").strip().lower()
        if s.startswith("file://") or os.path.exists(s) or ":\\" in s:
            return "LOCAL_SAVED_FILE"
        if "ve.cbi.ir" in s or "localhost" in s or "127.0.0.1" in s:
            return "LIVE_NETWORK_URL"
        return "UNKNOWN_SOURCE"

    @classmethod
    def analyze_html_content(cls, html_content: str, source_path: str = "") -> Dict[str, Any]:
        """
        Extract complete structural and semantic inventory from Page 3 HTML content
        without leaking confidential token values.
        """
        if not html_content or not isinstance(html_content, str):
            return {
                "valid": False,
                "error": "empty_or_invalid_html",
                "source_mode": cls.classify_source_mode(source_path),
            }

        report: Dict[str, Any] = {
            "valid": True,
            "source_path": os.path.basename(source_path) if source_path else "inline",
            "source_mode": cls.classify_source_mode(source_path),
            "is_page3_select_bnk": False,
            "form": {
                "action": None,
                "method": None,
                "has_form_tag": False,
            },
            "controls": {
                "ddlBankName_present": False,
                "ddlCity_present": False,
                "btnSave_present": False,
                "rbtnHozori_present": False,
                "rbtnGheirHozori_present": False,
            },
            "webforms": {
                "script_manager_present": False,
                "update_panel2_present": False,
                "update_progress_present": False,
                "has_do_postback": False,
                "viewstate_present": False,
                "viewstate_length": 0,
                "eventvalidation_present": False,
                "eventvalidation_length": 0,
            },
            "security_traps": {
                "has_close_session": False,
                "has_onbeforeunload_trap": False,
            },
            "applicant_meta": {
                "province_label": None,
                "selected_city": None,
                "has_tracking_code": False,
                "tracking_code": None,
            },
            "banks_snapshot": {
                "total_options": 0,
                "active_capacity_banks": [],
                "reserve_banks": [],
                "all_options": [],
            },
            "stale_errors": [],
        }

        # 1. Page Identity
        if "SelectBnkShb" in html_content or "ctl00_ContentPlaceHolder1_ddlBankName" in html_content:
            report["is_page3_select_bnk"] = True

        # 2. Form tag details
        form_m = re.search(r'<form\s+([^>]*?)>', html_content, re.IGNORECASE)
        if form_m:
            report["form"]["has_form_tag"] = True
            f_attrs = form_m.group(1)
            act_m = re.search(r'action=["\']([^"\']*)["\']', f_attrs, re.IGNORECASE)
            met_m = re.search(r'method=["\']([^"\']*)["\']', f_attrs, re.IGNORECASE)
            report["form"]["action"] = act_m.group(1) if act_m else None
            report["form"]["method"] = (met_m.group(1) if met_m else "POST").upper()

        # 3. Interactive Controls presence
        report["controls"]["ddlBankName_present"] = "ctl00_ContentPlaceHolder1_ddlBankName" in html_content or "ddlBankName" in html_content
        report["controls"]["ddlCity_present"] = "ctl00_ContentPlaceHolder1_ddlCity" in html_content or "ddlCity" in html_content
        report["controls"]["btnSave_present"] = "ctl00_ContentPlaceHolder1_btnSave" in html_content or "btnSave" in html_content
        report["controls"]["rbtnHozori_present"] = "rbtnHozori" in html_content
        report["controls"]["rbtnGheirHozori_present"] = "rbtnGheirHozori" in html_content

        # 4. ASP.NET WebForms & AJAX infrastructure
        report["webforms"]["script_manager_present"] = "ScriptManager" in html_content
        report["webforms"]["update_panel2_present"] = "ctl00_ContentPlaceHolder1_UpdatePanel2" in html_content
        report["webforms"]["update_progress_present"] = "UpdateProgress" in html_content
        report["webforms"]["has_do_postback"] = "__doPostBack" in html_content

        # ViewState & EventValidation (existence & approximate byte count only; never print value!)
        vs_m = re.search(r'id=["\']__VIEWSTATE["\'][^>]*value=["\']([^"\']*)["\']', html_content)
        if vs_m:
            report["webforms"]["viewstate_present"] = True
            report["webforms"]["viewstate_length"] = len(vs_m.group(1))

        ev_m = re.search(r'id=["\']__EVENTVALIDATION["\'][^>]*value=["\']([^"\']*)["\']', html_content)
        if ev_m:
            report["webforms"]["eventvalidation_present"] = True
            report["webforms"]["eventvalidation_length"] = len(ev_m.group(1))

        # 5. Security & Logout Traps
        if "function CloseSession" in html_content or "CloseSession()" in html_content:
            report["security_traps"]["has_close_session"] = True
        if "onbeforeunload" in html_content:
            report["security_traps"]["has_onbeforeunload_trap"] = True

        # 6. Applicant Location Metadata
        prov_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labStateName["\'][^>]*>(.*?)</span>', html_content, re.IGNORECASE)
        if prov_m:
            report["applicant_meta"]["province_label"] = re.sub(r'<[^>]+>', '', prov_m.group(1)).strip()

        city_sel_m = re.search(r'<select[^>]*id=["\']ctl00_ContentPlaceHolder1_ddlCity["\'][^>]*>(.*?)</select>', html_content, re.DOTALL | re.IGNORECASE)
        if city_sel_m:
            sel_m = re.search(r'<option[^>]*selected[^>]*>(.*?)</option>', city_sel_m.group(1), re.IGNORECASE)
            if sel_m:
                report["applicant_meta"]["selected_city"] = re.sub(r'<[^>]+>', '', sel_m.group(1)).strip()

        # Tracking code (if page was already submitted)
        tr_m = re.search(r'(?:کد\s*رهگیری|شماره\s*پیگیری|کد\s*پیگیری)\s*[:=]?\s*([0-9]{5,20})', html_content)
        if tr_m:
            report["applicant_meta"]["has_tracking_code"] = True
            report["applicant_meta"]["tracking_code"] = tr_m.group(1)

        # 7. Bank Options parsing
        bank_sel_m = re.search(r'<select[^>]*id=["\']ctl00_ContentPlaceHolder1_ddlBankName["\'][^>]*>(.*?)</select>', html_content, re.DOTALL | re.IGNORECASE)
        if bank_sel_m:
            options = re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', bank_sel_m.group(1), re.IGNORECASE)
            for val, txt in options:
                val = val.strip()
                txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', txt)).strip()
                if not val or val == "0" or "انتخاب" in txt:
                    continue
                is_reserve = any(k in txt for k in ["رزرو", "ذخیره", "تخصیص ظرفیت جدید"])
                has_active = "دارای ظرفیت" in txt and not is_reserve
                entry = {
                    "code": val,
                    "title": txt,
                    "has_active_quota": has_active,
                    "is_reserve": is_reserve,
                }
                report["banks_snapshot"]["all_options"].append(entry)
                if has_active:
                    report["banks_snapshot"]["active_capacity_banks"].append(txt)
                elif is_reserve:
                    report["banks_snapshot"]["reserve_banks"].append(txt)
            report["banks_snapshot"]["total_options"] = len(report["banks_snapshot"]["all_options"])

        # 8. Stale Errors
        err_m = re.search(r'id=["\']ctl00_ContentPlaceHolder1_labErrMsg["\'][^>]*>(.*?)</span>', html_content, re.IGNORECASE)
        if err_m:
            err_txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', err_m.group(1))).strip()
            if err_txt:
                report["stale_errors"].append(err_txt)

        return report

    @classmethod
    def analyze_file(cls, file_path: str) -> Dict[str, Any]:
        """Analyze a local HTML file strictly read-only."""
        if not os.path.isfile(file_path):
            return {
                "valid": False,
                "error": f"file_not_found: {file_path}",
                "source_mode": cls.classify_source_mode(file_path),
            }
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return cls.analyze_html_content(content, source_path=file_path)
        except Exception as exc:
            return {
                "valid": False,
                "error": f"read_error: {exc}",
                "source_mode": cls.classify_source_mode(file_path),
            }

    @classmethod
    def discover_saved_pages(cls, base_dir: str = "saved_pages") -> List[Dict[str, Any]]:
        """Find and analyze all saved Page 3 HTML files across saved_pages directory."""
        if not os.path.isdir(base_dir):
            return []
        results = []
        for root, _, files in os.walk(base_dir):
            for file_name in files:
                if file_name.lower().endswith(".html"):
                    full_p = os.path.join(root, file_name)
                    # Check size (skip huge dumps or 0 bytes)
                    sz = os.path.getsize(full_p)
                    if 500 < sz < 5_000_000:
                        analysis = cls.analyze_file(full_p)
                        if analysis.get("is_page3_select_bnk"):
                            results.append({
                                "file_path": full_p,
                                "file_name": file_name,
                                "folder": os.path.basename(root),
                                "size_bytes": sz,
                                "analysis": analysis,
                            })
        return results
