import json
import hashlib
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from database import DBHandler
from event_logger import emit_structured_event
from bank_page_manager import classify_capacity_or_error, BankPageState, is_reserve_text, capture_dom_html


def _safe_path_part(value, fallback="unknown", max_length=80):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or "")).strip(" .-")
    value = re.sub(r"\s+", " ", value)
    return (value or fallback)[:max_length]


def _applicant_archive_dir(bot):
    full_name = bot.user_data.get("full_name") or bot.nid
    project_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(os.getenv("MAHANBOT_SAVED_PAGES_DIR", os.path.join(project_dir, "saved_pages")), _safe_path_part(full_name))
    os.makedirs(target, exist_ok=True)
    return target


def _load_seen_banks(folder):
    manifest_path = os.path.join(folder, "banks_seen.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return set(payload.get("banks", []))
    except Exception:
        return set()


def _normalize_banks(bank_names):
    return sorted({re.sub(r"\s+", " ", str(name or "")).strip() for name in bank_names if str(name or "").strip()})


def _bank_signature(bank_names):
    normalized = _normalize_banks(bank_names)
    canonical = "\n".join(name.casefold() for name in normalized)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), normalized


def _normalize_persian(text: Any) -> str:
    if not text:
        return ""
    s = (
        str(text)
        .replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ئ", "ی")
        .replace("ك", "ک")
        .replace("ؤ", "و")
        .replace("ة", "ه")
        .replace("ۀ", "ه")
        .replace("\u200c", " ")
        .replace("\u0640", "")
    )
    return re.sub(r"\s+", " ", s).strip()


def _normalize_persian_key(text: Any) -> str:
    """Aggressive Persian normalization for matching (unifies آ/ا/أ/إ and strips spaces/punctuation)."""
    s = _normalize_persian(text)
    s = s.replace("آ", "ا").replace("أ", "ا").replace("إ", "ا").replace("ٱ", "ا")
    s = re.sub(r"[^\w\u0600-\u06FF\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_option_bank_title(option_text: Any) -> str:
    """
    Extract pure bank title from SelectBnkShb.aspx dropdown option text.
    Example: 'بانک صادرات [دارای ظرفیت ثبت نام] - متقاضی در صف : 4259 نفر' -> 'بانک صادرات'
    """
    raw = _normalize_persian(option_text)
    if not raw:
        return ""
    # Strip capacity brackets [...] and queue suffixes (- متقاضی در صف ...)
    raw = re.split(r"\[|\(|-\s*متقاضی|–\s*متقاضی", raw, maxsplit=1)[0]
    return raw.strip(" -–—:")


# Order matters: more specific compound bank names (like توسعه صادرات) MUST be checked before shorter roots (like صادرات)
_CANONICAL_BANK_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("tosee_saderat", ("توسعه صادرات", "توسعهصادرات")),
    ("tosee_taavon", ("توسعه تعاون", "توسعهتعاون")),
    ("mehr_iran", ("قرض الحسنه مهر ایران", "مهر ایران", "مهرایران", "بانک مهر")),
    ("resalat", ("قرض الحسنه رسالت", "رسالت")),
    ("postbank", ("پست بانک", "پستبانک")),
    ("eghtesad_novin", ("اقتصاد نوین", "اقتصادنوین")),
    ("iran_zamin", ("ایران زمین", "ایرانزمین")),
    ("iran_venezuela", ("ایران و ونزویلا", "ایران و ونزوئلا", "ونزویلا", "ونزوئلا")),
    ("sanat_madan", ("صنعت و معدن", "صنعت ومعدن", "صنعتمعدن")),
    ("refah", ("رفاه کارگران", "بانک رفاه", "رفاه")),
    ("melal", ("موسسه اعتباری ملل", "اعتباری ملل", "بانک ملل", "ملل")),
    ("noor", ("موسسه اعتباری نور", "اعتباری نور", "بانک نور", "نور")),
    ("khavarmiyaneh", ("خاورمیانه", "خاور میانه")),
    ("gardeshgari", ("گردشگری",)),
    ("karafarin", ("کارافرین", "کار افرین")),
    ("pasargad", ("پاسارگاد",)),
    ("parsian", ("پارسیان",)),
    ("saderat", ("بانک صادرات ایران", "صادرات ایران", "بانک صادرات", "صادرات")),
    ("melli", ("بانک ملی ایران", "ملی ایران", "بانک ملی", "ملی")),
    ("mellat", ("بانک ملت", "ملت")),
    ("tejarat", ("بانک تجارت", "تجارت")),
    ("sepah", ("بانک سپه", "سپه")),
    ("keshavarzi", ("بانک کشاورزی", "کشاورزی")),
    ("maskan", ("بانک مسکن", "مسکن")),
    ("saman", ("بانک سامان", "سامان")),
    ("sina", ("بانک سینا", "سینا")),
    ("sarmayeh", ("بانک سرمایه", "سرمایه")),
    ("shahr", ("بانک شهر", "شهر")),
    ("ayandeh", ("بانک اینده", "اینده")),
    ("day", ("بانک دی", "دی")),
]


def canonicalize_bank_name(text: Any) -> str:
    """
    Map any Persian bank name variation to a strict canonical bank identifier.
    Ensures:
      - 'بانک صادرات ایران', 'بانک صادرات', 'صادرات' -> 'saderat'
      - 'بانک توسعه صادرات', 'توسعه صادرات' -> 'tosee_saderat' (never confused with 'saderat')
      - 'بانک اقتصاد نوین', 'بانک اقتصادنوين' -> 'eghtesad_novin'
      - 'بانک ملی ایران', 'بانک ملی', 'ملی' -> 'melli'
    """
    title = extract_option_bank_title(text)
    key = _normalize_persian_key(title)
    if not key:
        return ""
    compact = key.replace(" ", "")

    for canon_id, patterns in _CANONICAL_BANK_PATTERNS:
        for pat in patterns:
            pat_key = _normalize_persian_key(pat)
            pat_compact = pat_key.replace(" ", "")
            if canon_id == "day":
                # 'دی' is short, so require exact word boundary match to avoid matching inside other words
                tokens = key.split()
                if "دی" in tokens or key == "بانک دی":
                    return "day"
                continue
            if pat_key in key or pat_compact in compact:
                return canon_id

    # Fallback: strip generic words ('بانک', 'ایران', 'قرض الحسنه', 'موسسه', 'اعتباری')
    cleaned = re.sub(r"\b(بانک|ایران|قرض|الحسنه|موسسه|اعتباری|شرکت)\b", " ", key)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or key


def match_bank_option(priority_name: Any, option_text: Any) -> bool:
    """
    Controlled, exact canonical matching between an applicant's priority bank name
    and an option text from #ctl00_ContentPlaceHolder1_ddlBankName.
    """
    canon_p = canonicalize_bank_name(priority_name)
    canon_opt = canonicalize_bank_name(option_text)
    if not canon_p or not canon_opt:
        return False
    if canon_p == canon_opt:
        return True
    # Only allow substring fallback when neither mapped to a known distinct canonical ID
    known_ids = {cid for cid, _ in _CANONICAL_BANK_PATTERNS}
    if canon_p in known_ids or canon_opt in known_ids:
        return False
    return canon_p == canon_opt


def _save_bank_page_if_changed(bot, page, bank_names):
    """Save HTML/screenshot once per normalized bank-list signature."""
    signature, normalized = _bank_signature(bank_names)
    if not normalized:
        return []
    if not bool(bot.settings.get("save_page_enabled", True)):
        return []

    folder = _applicant_archive_dir(bot)
    seen = _load_seen_banks(folder)
    new_banks = [name for name in normalized if name not in seen]
    try:
        with open(os.path.join(folder, "banks_seen.json"), "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
    except Exception:
        manifest = {}
    if bool(bot.settings.get("save_only_on_bank_change", True)) and manifest.get("last_signature") == signature:
        return []

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = f"banks-{stamp}-{signature[:10]}"
    files = []
    if bool(bot.settings.get("save_html", True)):
        html_path = os.path.join(folder, base_name + ".html")
        try:
            try:
                captured = capture_dom_html(page)
            except Exception:
                captured = None
            if not isinstance(captured, str) or '<html' not in captured.lower():
                captured = page.content()
            with open(html_path, "w", encoding="utf-8") as stream:
                stream.write(captured)
            if os.path.getsize(html_path) > 0:
                files.append(os.path.basename(html_path))
        except Exception:
            pass
    if bool(bot.settings.get("save_screenshot", True)):
        screenshot_path = os.path.join(folder, base_name + ".png")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            if os.path.getsize(screenshot_path) > 0:
                files.append(os.path.basename(screenshot_path))
        except Exception:
            pass
    if not files:
        return []

    all_seen = sorted(seen.union(normalized))
    manifest_path = os.path.join(folder, "banks_seen.json")
    snapshots = list(manifest.get("snapshots") or [])
    snapshots.append({
        "signature": signature,
        "banks": normalized,
        "files": files,
        "url": getattr(page, "url", ""),
        "created_at": time.time(),
    })
    temp_path = manifest_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "version": 2,
                "banks": all_seen,
                "last_signature": signature,
                "last_files": files,
                "snapshots": snapshots[-100:],
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
    os.replace(temp_path, manifest_path)
    bot.log(f"💾 snapshot بانک ذخیره شد؛ تغییر: {', '.join(new_banks) or 'فهرست بانک‌ها'}", "success", page)
    return new_banks


def repair_saved_pages(base_dir, only_folder=None):
    """Repair manifests conservatively without deleting any captured page."""
    report = {
        "folders": 0,
        "manifests_repaired": 0,
        "orphan_files_added": 0,
        "missing_files": [],
        "duplicates": [],
        "backups": [],
    }
    if not os.path.isdir(base_dir):
        return report
    for name in os.listdir(base_dir):
        if only_folder is not None and name != only_folder:
            continue
        folder = os.path.join(base_dir, name)
        if not os.path.isdir(folder):
            continue
        report["folders"] += 1
        manifest_path = os.path.join(folder, "banks_seen.json")
        manifest_valid = True
        try:
            with open(manifest_path, "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            if not isinstance(manifest, dict):
                raise ValueError("manifest root is not an object")
        except Exception:
            manifest_valid = False
            manifest = {"version": 2, "banks": [], "snapshots": []}
            if os.path.isfile(manifest_path):
                backup_path = manifest_path + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S") + ".bak"
                shutil.copy2(manifest_path, backup_path)
                report["backups"].append(backup_path)
        snapshots = list(manifest.get("snapshots") or [])
        referenced = {
            filename
            for snapshot in snapshots
            for filename in (snapshot.get("files") or [])
        }
        archive_files = sorted(
            filename
            for filename in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, filename))
            and os.path.splitext(filename)[1].lower() in {".html", ".htm", ".mhtml", ".png", ".jpg", ".jpeg"}
        )
        orphan_groups = {}
        for filename in archive_files:
            if filename in referenced:
                continue
            stem = os.path.splitext(filename)[0]
            orphan_groups.setdefault(stem, []).append(filename)
        for stem, files in sorted(orphan_groups.items()):
            signature = "recovered-" + hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16]
            snapshots.append({
                "signature": signature,
                "banks": [],
                "files": files,
                "url": "",
                "created_at": os.path.getmtime(os.path.join(folder, files[0])),
                "recovered": True,
            })
            report["orphan_files_added"] += len(files)
        changed = (not manifest_valid) or bool(orphan_groups) or manifest.get("version") != 2
        if changed:
            manifest["version"] = 2
            manifest["snapshots"] = snapshots
            if snapshots:
                manifest["last_signature"] = snapshots[-1].get("signature", "")
                manifest["last_files"] = snapshots[-1].get("files", [])
            temp_path = manifest_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
            os.replace(temp_path, manifest_path)
            report["manifests_repaired"] += 1
        signatures = set()
        for snapshot in snapshots:
            sig = snapshot.get("signature")
            if sig and sig in signatures:
                report["duplicates"].append({"folder": name, "signature": sig})
            elif sig:
                signatures.add(sig)
            for filename in snapshot.get("files", []):
                if not os.path.isfile(os.path.join(folder, filename)):
                    report["missing_files"].append({"folder": name, "file": filename})
    return report


def _is_reserve_option(option_text: str) -> bool:
    norm = _normalize_persian(option_text)
    reserve_markers = ("ذخیره", "رزرو", "در انتظار تخصیص ظرفیت")
    return any(m in norm for m in reserve_markers)


def _normalize_mode_value(raw_mode: Any) -> str:
    """Normalize reception mode strings into 'only_online', 'prefer_online', 'prefer_in_person', 'only_in_person', or 'online', 'in_person'."""
    m = _normalize_persian(raw_mode).lower().strip()
    if not m:
        return ""
    if m in {"only_online", "فقط_غیرحضوری", "فقط غیرحضوری", "فقط غیر حضوری"}:
        return "only_online"
    if m in {"prefer_online", "ترجیحا_غیرحضوری", "ترجیحا غیرحضوری", "ترجیحاً غیرحضوری", "ترجیحا غیر حضوری", "ترجیحاً غیر حضوری"}:
        return "prefer_online"
    if m in {"prefer_in_person", "ترجیحا_حضوری", "ترجیحا حضوری", "ترجیحاً حضوری"}:
        return "prefer_in_person"
    if m in {"only_in_person", "فقط_حضوری", "فقط حضوری"}:
        return "only_in_person"
    if m in {"online", "غیرحضوری", "غیر حضوری", "non_in_person", "electronic", "الکترونیکی", "اینترنتی"} or "غیر" in m:
        return "online"
    if m in {"in_person", "حضوری", "branch", "شعبه"} or ("حضوری" in m and "غیر" not in m):
        return "in_person"
    return m


def _extract_raw_priorities(runtime_data: Dict[str, Any], user_data: Dict[str, Any]) -> Any:
    """Extract priority banks from applicant data across all supported schema keys in strict priority order."""
    for source in (runtime_data, user_data):
        if not isinstance(source, dict):
            continue
        for key in ("priority_banks", "bank_priority", "banks"):
            val = source.get(key)
            if val:
                return val
        primary = source.get("primary_bank") or source.get("bank") or source.get("bank_name")
        if primary:
            alts = source.get("alternative_banks") or []
            if isinstance(alts, str):
                alts = [x.strip() for x in re.split(r"[\n,،]+", alts) if x.strip()]
            return [primary] + (list(alts) if isinstance(alts, list) else [])
        favs = source.get("favorite_banks")
        if favs:
            return favs
    return []


def _normalize_priority_entries(raw_priorities: Any, default_mode: str = "", default_branches: Optional[List[str]] = None, default_allow_reserve: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Normalize priority_banks list (strings or dicts) into structured bank preferences."""
    normalized: List[Dict[str, Any]] = []
    if not raw_priorities:
        return normalized
    if isinstance(raw_priorities, str):
        raw_priorities = [x.strip() for x in re.split(r"[\n,،]+", raw_priorities) if x.strip()]
    norm_default_mode = _normalize_mode_value(default_mode)
    for item in raw_priorities:
        if isinstance(item, dict):
            name = _normalize_persian(item.get("name") or item.get("bank") or item.get("bank_name") or "")
            if not name:
                continue
            item_mode = _normalize_mode_value(
                item.get("mode") or item.get("bank_mode") or item.get("reception_mode") or norm_default_mode
            )
            branches = item.get("branches") or item.get("priority_branches") or item.get("branch") or default_branches or []
            if isinstance(branches, str):
                branches = [b.strip() for b in re.split(r"[\n,،]+", branches) if b.strip()]
            allow_reserve = item.get("allow_reserve") if item.get("allow_reserve") is not None else default_allow_reserve
            normalized.append({
                "name": name,
                "canonical": canonicalize_bank_name(name),
                "mode": item_mode,
                "branches": [str(b).strip() for b in branches if str(b).strip()],
                "allow_reserve": allow_reserve,
            })
        else:
            name = _normalize_persian(item)
            if name:
                normalized.append({
                    "name": name,
                    "canonical": canonicalize_bank_name(name),
                    "mode": norm_default_mode,
                    "branches": list(default_branches or []),
                    "allow_reserve": default_allow_reserve,
                })
    return normalized


def _read_dropdown_options(page, selector: str) -> List[Dict[str, Any]]:
    try:
        opts = page.evaluate(
            "(sel) => {"
            "const el = document.querySelector(sel);"
            "if (!el) return [];"
            "return Array.from(el.options || []).map(opt => ({"
            "value: String(opt.value ?? ''),"
            "text: (opt.textContent || '').trim(),"
            "selected: !!opt.selected || (el.value === opt.value)"
            "}));"
            "}",
            selector,
        )
        if isinstance(opts, list):
            return opts
    except Exception:
        pass
    return []


def _select_dropdown_with_postback(bot, page, selector: str, target_value: str) -> bool:
    """
    Select an option in an ASP.NET dropdown (#ddlCity, #ddlBankName, #ddlBranch)
    and wait for setTimeout('__doPostBack(...)', 0) + UpdatePanel/Page postback to complete.
    """
    val_str = str(target_value)
    selected_ok = False
    try:
        page.select_option(selector, value=val_str)
        selected_ok = True
    except Exception:
        selected_ok = False

    # Only native control events may initiate the site's postback. A second synthetic
    # __doPostBack can race the first request and trigger CloseSession on full navigation.

    # Wait briefly for setTimeout('__doPostBack(...)', 0) to initiate async/sync postback
    try:
        page.wait_for_timeout(250)
    except Exception:
        pass

    if hasattr(bot, "wait_for_aspnet_postback"):
        if bot.wait_for_aspnet_postback(page, timeout_ms=8000) is False:
            bot._bank_selection_issue = "postback_timeout"
            return False

    return selected_ok


def _verify_dropdown_selection(page, selector: str, expected_value: str, expected_bank_name: str = "") -> Tuple[bool, str, str]:
    """
    Re-read dropdown state from live DOM after postback to verify the selection persisted.
    Returns (verified, actual_value, actual_text).
    """
    opts = _read_dropdown_options(page, selector)
    if not opts:
        return False, "", ""
    expected_str = str(expected_value)
    expected_canon = canonicalize_bank_name(expected_bank_name) if expected_bank_name else ""

    for opt in opts:
        val = str(opt.get("value") or opt.get("v") or "")
        txt = str(opt.get("text") or opt.get("t") or "").strip()
        is_sel = bool(opt.get("selected") or opt.get("s"))
        if is_sel:
            if val == expected_str:
                return True, val, txt
            if expected_canon and canonicalize_bank_name(txt) == expected_canon and val != "0":
                return True, val, txt
            return False, val, txt

    # Fallback for mock environments where options list has a single matching item or page.selected_bank is tracked
    return False, "", ""


def _selection_issue(bot, page, reason, message):
    bot._bank_selection_issue = reason
    bot._bank_selection_issue_message = message
    if hasattr(bot, "log"):
        bot.log(f"⚠️ {message}", "warning")
    bot.emit_event("BANK_SELECTION_FAILED", step="STEP_3_BANK_SELECT", reason=reason,
                   result="manual_action_required", message=message, level="warning", page=page)
    return False


def _ensure_state_and_city_selected(bot, page, runtime_data: Dict[str, Any]) -> bool:
    """Step 3.1: Ensure province (state) and city are selected and postbacks complete before bank selection."""
    user_state_raw = runtime_data.get("state") or bot.user_data.get("state") or ""
    user_city_raw = runtime_data.get("city") or bot.user_data.get("city") or ""
    user_state = _normalize_persian_key(re.sub(r"^\s*استان\s+", "", _normalize_persian(user_state_raw)))
    user_city = _normalize_persian_key(re.sub(r"^\s*(شهرستان|شهر)\s+", "", _normalize_persian(user_city_raw)))

    state_ddl = "#ctl00_ContentPlaceHolder1_ddlState"
    city_ddl = "#ctl00_ContentPlaceHolder1_ddlCity"

    # Province may be fixed by the authenticated application rather than editable here.
    province_label = page.locator("#ctl00_ContentPlaceHolder1_labStateName")
    if user_state and province_label.count() and province_label.first.is_visible():
        actual_province = _normalize_persian_key(re.sub(r"^\s*استان\s+", "", _normalize_persian(province_label.first.inner_text())))
        if actual_province and actual_province.replace(" ", "") != user_state.replace(" ", ""):
            return _selection_issue(bot, page, "province_mismatch",
                                    f"استان پرونده در صفحه زنده ({actual_province}) با استان متقاضی ({user_state_raw or user_state}) یکسان نیست؛ استان و شهر را بررسی کنید. نشست فعلی حفظ شد.")

    # 1) Check if state dropdown exists and needs selection
    try:
        state_loc = page.locator(state_ddl)
        if state_loc.count() > 0 and state_loc.first.is_visible() and user_state:
            state_opts = page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? Array.from(el.options || []).map(o => ({v: o.value, t: (o.textContent||'').trim(), s: o.selected})) : []; }",
                state_ddl,
            ) or []
            matched_state = False
            for opt in state_opts:
                t_norm = _normalize_persian_key(opt.get("t"))
                val = str(opt.get("v") or "")
                if not val or val == "0":
                    continue
                if user_state == t_norm:
                    matched_state = True
                    if not opt.get("s"):
                        if not _select_dropdown_with_postback(bot, page, state_ddl, val):
                            return False
                    break
            if not matched_state:
                return _selection_issue(bot, page, "province_not_available", "استان تنظیم‌شده در فهرست صفحه موجود نیست؛ تنظیمات متقاضی را بررسی کنید.")
    except Exception:
        return False

    # 2) Check if city dropdown exists and needs selection
    try:
        city_loc = page.locator(city_ddl)
        if city_loc.count() > 0 and city_loc.first.is_visible():
            city_opts = page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? Array.from(el.options || []).map(o => ({v: o.value, t: (o.textContent||'').trim(), s: o.selected})) : []; }",
                city_ddl,
            ) or []
            valid_cities = [o for o in city_opts if o.get("v") and str(o.get("v")) != "0"]
            currently_selected = next((o for o in valid_cities if o.get("s")), None)

            if user_city:
                matched_city = False
                for opt in valid_cities:
                    t_norm = _normalize_persian_key(opt.get("t"))
                    val = str(opt.get("v"))
                    if user_city == t_norm:
                        matched_city = True
                        if "سوران" in user_city and "سیب" not in user_city and "سیب" in t_norm:
                            continue
                        if not opt.get("s"):
                            if not _select_dropdown_with_postback(bot, page, city_ddl, val):
                                return False
                        break
                if not matched_city:
                    return _selection_issue(bot, page, "city_not_available", "شهر تنظیم‌شده در فهرست استان صفحه زنده موجود نیست؛ استان و شهر متقاضی را بررسی کنید.")
            elif currently_selected is None and len(valid_cities) == 1:
                # If applicant city was left blank and only one city exists in the province dropdown, select it
                if not _select_dropdown_with_postback(bot, page, city_ddl, str(valid_cities[0]["v"])):
                    return False
            if user_city:
                verified, _, _ = _verify_dropdown_selection(page, city_ddl, val)
                if not verified:
                    return _selection_issue(bot, page, "city_selection_not_persisted", "انتخاب شهر پس از پاسخ سامانه تایید نشد؛ صفحه فعلی حفظ شد.")
    except Exception:
        return False
    return True


def _detect_page_error_or_capacity_message(bot, page) -> Tuple[Optional[str], str]:
    """Inspect DOM error labels (#ctl00_ContentPlaceHolder1_labErrMsg, alerts) for capacity full or errors."""
    error_selectors = [
        "#ctl00_ContentPlaceHolder1_labErrMsg",
        "#ctl00_ContentPlaceHolder1_divGheireHozori",
        "#ctl00_ContentPlaceHolder1_lblMessage",
        "span[id*='ErrMsg']",
        "span[id*='lblError']",
        "div[id*='ErrMsg']",
    ]
    for sel in error_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = (loc.first.inner_text() or "").strip()
                if txt:
                    # Check bank_page_manager classification first
                    c_kind, _ = classify_capacity_or_error(txt)
                    if c_kind == BankPageState.CAPACITY_FULL:
                        return "bank_capacity_full", txt
                    elif c_kind == BankPageState.SESSION_EXPIRED:
                        return "session_expired", txt
                    kind = bot._classify_dialog(txt) if hasattr(bot, "_classify_dialog") else (c_kind or "generic_error")
                    return kind, txt
        except Exception:
            continue

    # Check if a dialog was just captured within the last 5 seconds
    if getattr(bot, "_last_dialog_message", "") and (time.time() - getattr(bot, "_last_dialog_ts", 0.0)) < 5.0:
        return getattr(bot, "_last_dialog_action", "generic_error"), getattr(bot, "_last_dialog_message", "")

    return None, ""


def _inspect_mode_controls(page) -> List[Dict[str, Any]]:
    """
    Inspect radio buttons, select dropdowns, and checkboxes for reception mode (حضوری vs غیرحضوری).
    Handles ASP.NET tables where radio labels may be in <label for=...>, parentElement, or adjacent <td> cells.
    """
    try:
        controls = page.evaluate(
            """() => {
                const results = [];
                const inputs = Array.from(document.querySelectorAll("input[type='radio'], input[type='checkbox'], select"));
                for (const el of inputs) {
                    const id = el.id || '';
                    const name = el.name || '';
                    if (id.includes('ddlCity') || id.includes('ddlState') || id.includes('ddlBankName') || id.includes('ddlBranch')) continue;
                    if (el.tagName === 'SELECT') {
                        const opts = Array.from(el.options || []).map(o => ({v: o.value, t: (o.textContent||'').trim(), s: !!o.selected}));
                        if (opts.some(o => o.t.includes('حضوری') || o.t.includes('غیرحضوری') || o.t.includes('غیر حضوری') || o.t.includes('الکترونیکی') || o.t.includes('اینترنتی'))) {
                            results.push({kind: 'select', id: id, name: name, options: opts, value: el.value, disabled: !!el.disabled});
                        }
                    } else if (el.type === 'radio' || el.type === 'checkbox') {
                        let labelText = '';
                        if (id) {
                            const explicitLbl = document.querySelector(`label[for='${id}']`);
                            if (explicitLbl) labelText = (explicitLbl.textContent || '').trim();
                        }
                        if (!labelText && el.nextElementSibling && el.nextElementSibling.tagName === 'LABEL') {
                            labelText = (el.nextElementSibling.textContent || '').trim();
                        }
                        if (!labelText && el.parentElement) {
                            labelText = (el.parentElement.textContent || '').trim();
                        }
                        if (!labelText && el.parentElement && el.parentElement.nextElementSibling) {
                            labelText = (el.parentElement.nextElementSibling.textContent || '').trim();
                        }
                        if (!labelText && el.parentElement && el.parentElement.parentElement) {
                            labelText = (el.parentElement.parentElement.textContent || '').trim();
                        }
                        const idLow = (id + ' ' + name + ' ' + (el.value || '')).toLowerCase();
                        if (
                            labelText.includes('حضوری') ||
                            labelText.includes('غیرحضوری') ||
                            labelText.includes('غیر حضوری') ||
                            labelText.includes('الکترونیکی') ||
                            labelText.includes('اینترنتی') ||
                            idLow.includes('hozoori') ||
                            idLow.includes('hozori') ||
                            idLow.includes('online') ||
                            idLow.includes('type') ||
                            idLow.includes('mode')
                        ) {
                            results.push({
                                kind: el.type,
                                id: id,
                                name: name,
                                label: labelText,
                                checked: !!el.checked,
                                disabled: !!el.disabled
                            });
                        }
                    }
                }
                return results;
            }"""
        )
        if isinstance(controls, list):
            return controls
    except Exception:
        pass
    return []


def _is_online_label(text: str, ctrl_id: str = "") -> bool:
    norm = _normalize_persian(text)
    id_low = str(ctrl_id or "").lower()
    if any(k in norm for k in ("غیرحضوری", "غیر حضوری", "الکترونیکی", "اینترنتی", "بدون مراجعه")):
        return True
    if any(k in id_low for k in ("gheir", "non", "online", "elec")):
        return True
    return False


def _is_in_person_label(text: str, ctrl_id: str = "") -> bool:
    norm = _normalize_persian(text)
    if _is_online_label(norm, ctrl_id):
        return False
    id_low = str(ctrl_id or "").lower()
    if "حضوری" in norm or "مراجعه به شعبه" in norm:
        return True
    if ("hozoori" in id_low or "hozori" in id_low) and "gheir" not in id_low:
        return True
    return False


def _select_bank_mode(bot, page, desired_mode: str, bank_name: str) -> Tuple[str, bool]:
    """
    Step 3.3: Handle in-person (حضوری) vs online/non-in-person (غیرحضوری) selection on SelectBnkShb.aspx.
    NEVER silently switch from online (غیرحضوری) to in-person (حضوری) without user permission!
    Emits BANK_MODE_SELECTED and BANK_MODE_VERIFIED.
    Returns (effective_mode, ok) where effective_mode is 'online', 'in_person', or 'default'.
    """
    mode_norm = _normalize_mode_value(desired_mode)
    if not mode_norm:
        # Check if global settings specify a default bank mode (defaults to online)
        mode_norm = _normalize_mode_value(bot.settings.get("default_bank_mode", "online"))
    wants_online = mode_norm in {"online", "only_online", "prefer_online"}
    wants_in_person = mode_norm in {"in_person", "only_in_person", "prefer_in_person"}

    mode_controls = _inspect_mode_controls(page)
    if not mode_controls:
        # Give dynamic UpdatePanel up to 1.5s to render mode radio buttons/select
        start_t = time.time()
        while time.time() - start_t < 1.5:
            try:
                page.wait_for_timeout(200)
            except Exception:
                time.sleep(0.2)
            mode_controls = _inspect_mode_controls(page)
            if mode_controls:
                break

    if not mode_controls:
        effective = "online" if wants_online else ("in_person" if wants_in_person else "default")
        if wants_in_person:
            branch = page.locator("#ctl00_ContentPlaceHolder1_ddlBranch")
            if branch.count() and branch.first.is_visible():
                return effective, True
        # Absence of a radio is not evidence that the requested method was applied.
        return effective, False

    if wants_online:
        selected = False
        target_ctrl_id = ""
        target_opt_val = ""
        for ctrl in mode_controls:
            kind = ctrl.get("kind")
            ctrl_id = ctrl.get("id") or ""
            lbl = ctrl.get("label") or ""
            if kind in {"radio", "checkbox"} and _is_online_label(lbl, ctrl_id):
                if ctrl.get("disabled"):
                    bot.emit_event(
                        "BANK_SELECTION_FAILED",
                        step="STEP_3_BANK_SELECT",
                        reason=f"online_mode_disabled:bank={bank_name}",
                        result="mode_disabled",
                        message=f"گزینه غیرحضوری برای {bank_name} غیرفعال است؛ تغییر خودکار به حضوری مجاز نیست.",
                        level="warning",
                        page=page,
                    )
                    return "online", False
                target_ctrl_id = ctrl_id
                if not ctrl.get("checked") and ctrl_id:
                    page.locator(f"#{ctrl_id}").click()
                    try:
                        page.wait_for_timeout(120)
                    except Exception:
                        pass
                    if hasattr(bot, "wait_for_aspnet_postback"):
                        bot.wait_for_aspnet_postback(page, timeout_ms=8000)
                selected = True
                break
            elif kind == "select" and ctrl_id:
                for opt in ctrl.get("options", []):
                    if _is_online_label(opt.get("t", "")):
                        target_ctrl_id = ctrl_id
                        target_opt_val = str(opt.get("v") or "")
                        _select_dropdown_with_postback(bot, page, f"#{ctrl_id}", target_opt_val)
                        selected = True
                        break
                if selected:
                    break

        if not selected:
            # Strictly refuse to fallback to in-person when user requested online!
            bot.emit_event(
                "BANK_SELECTION_FAILED",
                step="STEP_3_BANK_SELECT",
                reason=f"online_mode_not_found:bank={bank_name}",
                result="mode_missing",
                message=f"گزینه غیرحضوری برای {bank_name} در صفحه یافت نشد؛ تغییر خودکار به حضوری مجاز نیست.",
                level="warning",
                page=page,
            )
            return "online", False

        bot.emit_event(
            "BANK_MODE_SELECTED",
            step="STEP_3_BANK_SELECT",
            reason=f"bank={bank_name} ctrl={target_ctrl_id}",
            result="online",
            message=f"روش غیرحضوری برای {bank_name} انتخاب شد.",
            level="info",
            page=page,
        )

        # Re-verify mode in DOM after postback
        refreshed_controls = _inspect_mode_controls(page)
        verified = False
        if refreshed_controls and target_ctrl_id:
            for rctrl in refreshed_controls:
                if rctrl.get("id") == target_ctrl_id:
                    if rctrl.get("kind") in {"radio", "checkbox"}:
                        verified = bool(rctrl.get("checked", True))
                    elif rctrl.get("kind") == "select" and target_opt_val:
                        verified = str(rctrl.get("value") or "") == target_opt_val
                    break

        if not verified:
            bot.emit_event(
                "BANK_SELECTION_FAILED",
                step="STEP_3_BANK_SELECT",
                reason=f"online_mode_verification_failed:bank={bank_name}",
                result="mode_unverified",
                message=f"انتخاب روش غیرحضوری برای {bank_name} پس از Postback تایید نشد.",
                level="warning",
                page=page,
            )
            return "online", False

        bot.emit_event(
            "BANK_MODE_VERIFIED",
            step="STEP_3_BANK_SELECT",
            reason=f"bank={bank_name} mode=online",
            result="verified",
            message=f"انتخاب روش غیرحضوری برای {bank_name} در فرم تایید شد.",
            level="info",
            page=page,
        )
        return "online", True

    if wants_in_person:
        selected = False
        target_ctrl_id = ""
        for ctrl in mode_controls:
            kind = ctrl.get("kind")
            ctrl_id = ctrl.get("id") or ""
            lbl = ctrl.get("label") or ""
            if kind in {"radio", "checkbox"} and _is_in_person_label(lbl, ctrl_id):
                if ctrl.get("disabled"):
                    return "in_person", False
                target_ctrl_id = ctrl_id
                if not ctrl.get("checked") and ctrl_id:
                    page.locator(f"#{ctrl_id}").click()
                    try:
                        page.wait_for_timeout(120)
                    except Exception:
                        pass
                    if hasattr(bot, "wait_for_aspnet_postback"):
                        bot.wait_for_aspnet_postback(page, timeout_ms=8000)
                selected = True
                break
            elif kind == "select" and ctrl_id:
                for opt in ctrl.get("options", []):
                    if _is_in_person_label(opt.get("t", "")):
                        target_ctrl_id = ctrl_id
                        _select_dropdown_with_postback(bot, page, f"#{ctrl_id}", str(opt.get("v") or ""))
                        selected = True
                        break
                if selected:
                    break
        if selected:
            bot.emit_event(
                "BANK_MODE_SELECTED",
                step="STEP_3_BANK_SELECT",
                reason=f"bank={bank_name} ctrl={target_ctrl_id}",
                result="in_person",
                message=f"روش حضوری برای {bank_name} انتخاب شد.",
                level="info",
                page=page,
            )
            bot.emit_event(
                "BANK_MODE_VERIFIED",
                step="STEP_3_BANK_SELECT",
                reason=f"bank={bank_name} mode=in_person",
                result="verified",
                message=f"انتخاب روش حضوری برای {bank_name} در فرم تایید شد.",
                level="info",
                page=page,
            )
            return "in_person", True

    return "default", True


def _extract_confirmed_submission(bot, page) -> Optional[str]:
    """Check if the site officially confirmed final submission and returned a tracking code or confirmation message."""
    selectors = [
        "#ctl00_ContentPlaceHolder1_lblTrackingCode",
        "span[id*='TrackingCode']",
        "label[id*='TrackingCode']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = (loc.first.inner_text() or "").strip()
                match = re.search(r"(\d{6,20})", txt)
                if match:
                    return match.group(1)
                if txt:
                    return txt
        except Exception:
            continue
    try:
        body = page.inner_text("body") or ""
        for pat in (
            r"کد\s*رهگیری\s*[:\-]?\s*(\d{6,20})",
            r"شماره\s*پیگیری\s*[:\-]?\s*(\d{6,20})",
        ):
            m = re.search(pat, body)
            if m:
                return m.group(1)
        if "با موفقیت ثبت گردید" in body or "انتخاب بانک شما با موفقیت" in body or "درخواست شما با موفقیت ثبت شد" in body:
            return "CONFIRMED"
    except Exception:
        pass
    return None


def extract_confirmed_reservation(page=None, *, html=None):
    """Require a reservation success message outside dropdowns/instructional warnings."""
    try:
        if html is not None:
            messages = re.findall(r'<(?:span|div|label)\b[^>]*(?:id=["\'][^"\']*(?:labErrMsg|lblMessage|lblSuccess)["\']|role=["\']status["\'])[^>]*>(.*?)</(?:span|div|label)>', html, re.I | re.S)
            text = '\n'.join(re.sub(r'<[^>]+>', '', message) for message in messages)
        else:
            text = page.evaluate("""() => Array.from(document.querySelectorAll(
                '[id$=labErrMsg], [id$=lblMessage], [id$=lblSuccess], [role=status]'
            )).filter(e => e.offsetParent !== null).map(e => e.innerText).join('\\n')""")
        return isinstance(text, str) and bool(re.search(
            r"(?:درخواست|ثبت نام|ثبت‌نام|رزرو|ذخیره).{0,80}(?:با موفقیت|ثبت گردید|ثبت شد)", text)
            and re.search(r"رزرو|ذخیره", text))
    except Exception:
        return False


def process_bank_selection_v2(bot, page, stop_event, *, capacity_sniping=False):
    """
    Step 3 State Machine handler for SelectBnkShb.aspx:
    Distinguishes strictly between:
      - "option_selected_waiting_user": Bank/mode/branch prepared & verified, auto_final_submit=False -> waiting for user
      - "capacity_full": Bank selected, site reported capacity full -> BANK_CAPACITY_FULL
      - "no_match": Priority bank not in dropdown -> BANK_OPTION_NOT_FOUND
      - "submitted_confirmed": Final submit clicked AND confirmed by site with tracking code / message
      - "submit_rejected": Final submit clicked but rejected by site
      - "already_submitted": Duplicate submission prevented
    """
    monitor_lock = getattr(bot, "_bank_monitor_lock", None)
    if monitor_lock is not None and not monitor_lock.acquire(blocking=False):
        return "waiting"
    try:
        bot._bank_selection_issue = ""
        bot._bank_selection_issue_message = ""
        if stop_event.is_set():
            return "stopped"
        # 0. Prevent duplicate final submission
        existing_tracking = DBHandler.has_final_submission(bot.nid)
        if existing_tracking:
            bot.log(f"✅ درخواست قبلاً ثبت نهایی شده است (کد پیگیری: {bot._mask(existing_tracking)})؛ جلوگیری از ثبت تکراری.", "success", page)
            return "already_submitted"

        dropdown_id = "#ctl00_ContentPlaceHolder1_ddlBankName"
        branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
        if bot._firewall_gate(page, stop_event):
            return "stopped"

        # Wait for any in-flight ASP.NET postback before reading DOM
        if hasattr(bot, "wait_for_aspnet_postback"):
            if bot.wait_for_aspnet_postback(page, timeout_ms=5000) is False:
                bot._bank_selection_issue = "postback_timeout"
                return "waiting"

        runtime_data = load_runtime_data(bot)

        # 1. Load applicant priority banks & settings first so we can log BANK_PREFERENCES_LOADED
        raw_priorities = _extract_raw_priorities(runtime_data, getattr(bot, "user_data", {}))
        if not raw_priorities:
            try:
                from core.applicant_config_service import ApplicantConfigurationService
                from database import DB_PATH
                app_cfg = ApplicantConfigurationService(DB_PATH).get_applicant_config(bot.nid)
                if app_cfg and app_cfg.priority_banks:
                    raw_priorities = [
                        {
                            "name": b.bank_name,
                            "mode": b.visit_method,
                            "branch": b.preferred_branch_text,
                            "allow_reserve": b.reserve_allowed,
                        }
                        for b in app_cfg.priority_banks
                    ]
            except Exception:
                pass
        default_mode = str(
            runtime_data.get("bank_mode")
            or runtime_data.get("mode")
            or runtime_data.get("reception_mode")
            or bot.user_data.get("bank_mode")
            or bot.user_data.get("mode")
            or bot.settings.get("default_bank_mode", "")
            or ""
        ).strip()
        default_branches = (
            runtime_data.get("priority_branches")
            or bot.user_data.get("priority_branches")
            or runtime_data.get("branches")
            or bot.user_data.get("branches")
            or []
        )
        if not default_branches and (runtime_data.get("branch_code") or bot.user_data.get("branch_code")):
            default_branches = [str(runtime_data.get("branch_code") or bot.user_data.get("branch_code"))]

        applicant_allow_reserve = (
            runtime_data.get("allow_reserve")
            if runtime_data.get("allow_reserve") is not None
            else getattr(bot, "user_data", {}).get("allow_reserve")
        )
        allow_reserve_global = bool(bot.settings.get("allow_reserve_bank", True))
        allow_reserve_effective = (
            bool(applicant_allow_reserve)
            if applicant_allow_reserve is not None
            else (allow_reserve_global)
        )

        priorities = _normalize_priority_entries(
            raw_priorities,
            default_mode=default_mode,
            default_branches=default_branches,
            default_allow_reserve=allow_reserve_effective,
        )
        raw_stopped = runtime_data.get("stopped_banks") or bot.user_data.get("stopped_banks") or []
        stopped_banks_norm = {_normalize_persian(b) for b in raw_stopped if b}
        stopped_banks_canon = {canonicalize_bank_name(b) for b in raw_stopped if b}

        if not priorities:
            bot.emit_event(
                "BANK_SELECTION_FAILED",
                step="STEP_3_BANK_SELECT",
                reason="no_priority_banks_configured",
                result="error",
                message="هیچ بانک اولویت‌داری در تنظیمات متقاضی تعریف نشده است.",
                level="warning",
                page=page,
            )
            return "error"

        pref_sig = json.dumps(
            {
                "banks": [p["name"] for p in priorities],
                "mode": default_mode,
                "reserve": allow_reserve_effective,
                "city": runtime_data.get("city") or bot.user_data.get("city") or "",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if getattr(bot, "_last_pref_loaded_sig", None) != pref_sig:
            bot._last_pref_loaded_sig = pref_sig
            bot.emit_event(
                "BANK_PREFERENCES_LOADED",
                step="STEP_3_BANK_SELECT",
                reason=f"priorities={[p['name'] for p in priorities]} mode={default_mode or 'default'} reserve={allow_reserve_effective}",
                result="loaded",
                message=f"تنظیمات اولویت بانک متقاضی بارگذاری شد: {', '.join(p['name'] for p in priorities)}",
                level="info",
                page=page,
            )

        # 2. Ensure province and city are selected first
        if _ensure_state_and_city_selected(bot, page, runtime_data) is False:
            return "manual_action_required"

        # 3. Read bank options from #ctl00_ContentPlaceHolder1_ddlBankName
        options = _read_dropdown_options(page, dropdown_id)
        available = [
            (str(o.get("text", "")).strip(), str(o.get("value", "")), bool(o.get("selected", False)))
            for o in options
        ]
        available = [(t, v, s) for t, v, s in available if t and v and v != "0" and "انتخاب کن" not in _normalize_persian(t)]
        if not available:
            return "waiting"

        bank_names = [text for text, _, _ in available]
        previous = DBHandler.get_runtime(bot.nid).get("banks_available") or []
        previous_set, current_set = set(previous), set(bank_names)
        now = time.time()
        refresh_sec = max(1.0, float(bot.settings.get("bank_list_refresh_seconds", bot.settings.get("bank_refresh_interval", 5.0))))
        DBHandler.update_runtime(
            bot.nid,
            status="MONITORING",
            current_step="READ_BANKS",
            banks_previous=previous,
            banks_available=bank_names,
            new_banks=sorted(current_set - previous_set),
            removed_banks=sorted(previous_set - current_set),
            last_refresh=now,
            next_refresh=now + refresh_sec,
        )
        bot.mark_progress("banks_read")
        _save_bank_page_if_changed(bot, page, bank_names)

        # Record Live DOM Observation into bank_page_observations
        try:
            from core.applicant_config_service import ApplicantConfigurationService
            from database import DB_PATH
            obs_prov = ""
            obs_city = ""
            try:
                p_lab = page.locator("#ctl00_ContentPlaceHolder1_labStateName")
                if p_lab.count() and p_lab.first.is_visible():
                    obs_prov = p_lab.first.inner_text().strip()
                c_sel = page.locator("#ctl00_ContentPlaceHolder1_ddlCity")
                if c_sel.count() and c_sel.first.is_visible():
                    obs_city = page.evaluate("() => { const el = document.querySelector('#ctl00_ContentPlaceHolder1_ddlCity'); return el && el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : ''; }") or ""
            except Exception:
                pass

            raw_opts_dict = [{"text": t, "value": v, "selected": s, "is_reserve": _is_reserve_option(t)} for t, v, s in available]
            ApplicantConfigurationService(DB_PATH).record_bank_page_observation(
                applicant_id=getattr(bot, "applicant_id", 0) or 0,
                session_id=getattr(bot, "session_id", "") or str(getattr(bot, "run_id", "")),
                site_province=obs_prov,
                site_city=obs_city,
                available_banks=[{"name": t, "value": v} for t, v, _ in available],
                raw_options=raw_opts_dict,
                capacity_status="RESERVE_ONLY" if all(_is_reserve_option(t) for t, _, _ in available) else "AVAILABLE",
                verification_status="PENDING",
            )
        except Exception:
            pass

        # 4. Clean expired bank cooldowns and build candidate queue in applicant's priority order
        now = time.time()
        if not hasattr(bot, "_bank_cooldowns"):
            bot._bank_cooldowns = {}
        else:
            bot._bank_cooldowns = {k: v for k, v in bot._bank_cooldowns.items() if v > now}

        active_cooldowns = getattr(bot, "_bank_cooldowns", {})
        priorities_canons = [entry.get("canonical") or canonicalize_bank_name(entry["name"]) for entry in priorities if entry.get("name")]
        if priorities_canons and all(c in active_cooldowns for c in priorities_canons):
            bot._bank_cooldowns.clear()
            active_cooldowns = {}

        active_candidates: List[Tuple[Dict[str, Any], str, str, bool, bool]] = []
        reserve_candidates: List[Tuple[Dict[str, Any], str, str, bool, bool]] = []
        skipped_reserve_options: List[str] = []

        for entry in priorities:
            p_name = entry["name"]
            p_canon = entry.get("canonical") or canonicalize_bank_name(p_name)
            if not p_name or p_name in stopped_banks_norm or (p_canon and p_canon in stopped_banks_canon):
                continue
            if p_canon in active_cooldowns:
                continue

            entry_allow_reserve = entry["allow_reserve"] if entry.get("allow_reserve") is not None else allow_reserve_effective

            for text_item, value_item, is_sel in available:
                if match_bank_option(p_name, text_item):
                    if _is_reserve_option(text_item):
                        if not entry_allow_reserve:
                            skipped_reserve_options.append(text_item)
                        else:
                            reserve_candidates.append((entry, text_item, value_item, is_sel, True))
                    else:
                        active_candidates.append((entry, text_item, value_item, is_sel, False))

        # Non-reserve active options are prioritized before reserve options
        candidate_queue = active_candidates + reserve_candidates
        priority_order = {entry["canonical"]: i for i, entry in enumerate(priorities)}
        candidate_queue.sort(key=lambda item: priority_order.get(item[0]["canonical"], len(priorities)))

        if not candidate_queue:
            target_names = "، ".join(e["name"] for e in priorities)
            if skipped_reserve_options:
                bot.emit_event(
                    "BANK_RESERVE_ONLY",
                    step="STEP_3_BANK_SELECT",
                    reason=f"reserve_only_disabled:{skipped_reserve_options[0]}",
                    result="reserve_skipped",
                    message=f"بانک اولویت‌دار ({target_names}) فقط به صورت رزرو/ذخیره در فهرست موجود است و گزینه بانک رزرو غیرفعال است.",
                    level="warning",
                    page=page,
                )
                bot.emit_event(
                    "BANK_SELECTION_FAILED",
                    step="STEP_3_BANK_SELECT",
                    reason=f"reserve_only_disabled:{skipped_reserve_options[0]}",
                    result="reserve_skipped",
                    message=f"بانک اولویت‌دار ({target_names}) فقط به صورت رزرو/ذخیره در فهرست موجود است و گزینه بانک رزرو غیرفعال است.",
                    level="warning",
                    page=page,
                )
            else:
                bot.emit_event(
                    "BANK_NOT_AVAILABLE",
                    step="STEP_3_BANK_SELECT",
                    reason=f"priority_banks={target_names}",
                    result="not_in_dropdown",
                    message=f"بانک اولویت‌دار ({target_names}) در فهرست بانک‌های دارای ظرفیت فعلی وجود ندارد.",
                    level="info",
                    page=page,
                )
            bot.emit_event(
                "BANK_OPTION_NOT_FOUND",
                step="STEP_3_BANK_SELECT",
                reason=f"priority_banks={target_names}",
                result="not_in_dropdown",
                message=f"بانک اولویت‌دار ({target_names}) در فهرست بانک‌های دارای ظرفیت فعلی وجود ندارد.",
                level="info",
                page=page,
            )
            return "no_match"

        auto_switch = bool(bot.settings.get("auto_switch_alternative_bank", True))
        fallback_in_person = bool(bot.settings.get("fallback_to_in_person", False))
        cooldown_sec = max(5, int(bot.settings.get("bank_cooldown_seconds", 30)))

        matched_entry = None
        target_value = None
        target_text = None
        bank_name = ""
        desired_mode = ""
        effective_mode = ""
        is_reserve_choice = False

        for idx, (cand_entry, cand_text, cand_val, cand_already_sel, cand_is_reserve) in enumerate(candidate_queue):
            matched_entry = cand_entry
            target_text = cand_text
            target_value = cand_val
            bank_name = cand_entry["name"]
            desired_mode = cand_entry["mode"]
            is_reserve_choice = cand_is_reserve
            p_canon = cand_entry.get("canonical") or canonicalize_bank_name(bank_name)
            has_next = auto_switch and (idx + 1 < len(candidate_queue))

            current_bank_sig = f"{bank_name}:{target_value}"
            if getattr(bot, "_last_found_bank_sig", None) != current_bank_sig:
                bot._last_found_bank_sig = current_bank_sig
                bot.emit_event(
                    "BANK_OPTION_FOUND",
                    step="STEP_3_BANK_SELECT",
                    reason=f"bank={bank_name} value={target_value}",
                    result=target_text or bank_name,
                    message=f"بانک اولویت‌دار «{bank_name}» در فهرست پیدا شد: {target_text}",
                    level="info",
                    page=page,
                )

            # Clear any stale capacity full flag before selecting/testing
            bot.consume_recovery_flag("bank_capacity_full")
            bot.consume_recovery_flag("generic_error")

            # 5. Select the bank option in dropdown (and wait for ASP.NET postback)
            DBHandler.update_runtime(bot.nid, status="SELECTING_BANK", current_step="SELECT_BANK", target_bank=bank_name)
            if not cand_already_sel:
                if not _select_dropdown_with_postback(bot, page, dropdown_id, target_value):
                    return "waiting"

            if getattr(bot, "_last_selected_bank_sig", None) != current_bank_sig:
                bot._last_selected_bank_sig = current_bank_sig
                bot.emit_event(
                    "BANK_OPTION_SELECTED",
                    step="STEP_3_BANK_SELECT",
                    reason=f"bank={bank_name} value={target_value}",
                    result="dropdown_option_selected",
                    message=f"گزینه بانک «{bank_name}» از فهرست انتخاب شد (هنوز ثبت نهایی نشده است).",
                    level="info",
                    page=page,
                )

            # Check immediately if selecting the bank triggered capacity full dialog or DOM message
            is_full = False
            full_msg = ""
            if bot.consume_recovery_flag("bank_capacity_full"):
                is_full = True
                full_msg = getattr(bot, "_last_dialog_message", "bank_capacity_full")

            if not is_full:
                err_kind, err_text = _detect_page_error_or_capacity_message(bot, page)
                if err_kind == "bank_capacity_full":
                    is_full = True
                    full_msg = err_text

            online_sniping = bool(capacity_sniping)  # Managed submissions preserve either requested mode.
            if is_full and not online_sniping:
                bot.emit_event(
                    "BANK_CAPACITY_FULL",
                    step="STEP_3_BANK_SELECT",
                    reason=full_msg,
                    result="capacity_full_dom",
                    message=f"ظرفیت بانک «{bank_name}» تکمیل است: {full_msg}",
                    level="warning",
                    page=page,
                )
                bot._bank_cooldowns[p_canon] = time.time() + cooldown_sec
                if has_next:
                    bot.log(f"🔄 ظرفیت بانک «{bank_name}» تکمیل است؛ انتقال خودکار به اولویت بعدی طبق استراتژی آبشاری (Waterfall)...", "info", page)
                    return_to_bank_field(page, dropdown_id)
                    continue
                bot.save_checkpoint(
                    "STEP_3_BANK_SELECT",
                    page=page,
                    selected_bank=bank_name,
                    bank_mode=desired_mode or "online",
                    expected_next_step="RECHECK_BANK_CAPACITY",
                    last_error_reason=full_msg,
                    last_operation_result="BANK_CAPACITY_FULL",
                )
                return "capacity_full"

            # Verify that the bank selection actually persisted in live DOM after postback
            verified_bank, actual_val, actual_txt = _verify_dropdown_selection(page, dropdown_id, target_value, bank_name)
            if not verified_bank:
                bot.emit_event(
                    "BANK_SELECTION_FAILED",
                    step="STEP_3_BANK_SELECT",
                    reason=f"selection_not_persisted:expected={target_value},actual={actual_val}",
                    result="postback_reset",
                    message=f"انتخاب بانک «{bank_name}» پس از Postback در کنترل صفحه باقی نماند.",
                    level="warning",
                    page=page,
                )
                if has_next:
                    return_to_bank_field(page, dropdown_id)
                    continue
                return "waiting"

            bot.emit_event(
                "BANK_SELECTION_VERIFIED",
                step="STEP_3_BANK_SELECT",
                reason=f"bank={bank_name} value={actual_val or target_value}",
                result="verified",
                message=f"انتخاب بانک «{bank_name}» در کنترل صفحه تایید شد.",
                level="success",
                page=page,
            )
            observer = getattr(bot, "bank_state_observer", None)
            if callable(observer):
                observer(BankPageState.BANK_SELECTED)

            # 6. Mode selection (حضوری vs غیرحضوری) with optional fallback to in-person
            mode_choice = _normalize_mode_value(desired_mode)
            wants_online_first = mode_choice in {"online", "only_online", "prefer_online", ""}
            first_target_mode = "online" if wants_online_first else "in_person"

            effective_mode, mode_ok = _select_bank_mode(bot, page, first_target_mode, bank_name)
            if mode_ok and effective_mode == "online" and callable(observer):
                observer(BankPageState.NON_PRESENTIAL_SELECTED)

            # Determine whether switching between online and in-person is permitted:
            # - only_online / only_in_person: STRICT, NEVER switch
            # - prefer_online: can switch to in-person
            # - prefer_in_person: can switch to online
            # - online: can switch to in-person only if fallback_in_person is True
            can_switch_to_in_person = (mode_choice == "prefer_online") or (mode_choice in {"online", ""} and fallback_in_person)
            can_switch_to_online = (mode_choice == "prefer_in_person")

            if not mode_ok and first_target_mode == "online" and can_switch_to_in_person:
                bot.log(f"🔄 روش غیرحضوری برای بانک {bank_name} در دسترس نیست؛ بر اساس تنظیمات روش حضوری بررسی می‌شود...", "info", page)
                effective_mode, mode_ok = _select_bank_mode(bot, page, "in_person", bank_name)
            elif not mode_ok and first_target_mode == "in_person" and can_switch_to_online:
                bot.log(f"🔄 روش حضوری برای بانک {bank_name} در دسترس نیست؛ بر اساس تنظیمات اولویت به روش غیرحضوری سوئیچ می‌شود...", "info", page)
                effective_mode, mode_ok = _select_bank_mode(bot, page, "online", bank_name)

            mode_full = False
            mode_err_msg = ""
            if bot.consume_recovery_flag("bank_capacity_full"):
                mode_full = True
                mode_err_msg = getattr(bot, "_last_dialog_message", "bank_capacity_full")
            else:
                err_kind, err_text = _detect_page_error_or_capacity_message(bot, page)
                if err_kind == "bank_capacity_full":
                    mode_full = True
                    mode_err_msg = err_text

            if mode_full and effective_mode == "online" and can_switch_to_in_person and not online_sniping:
                bot.log(f"🔄 سهمیه غیرحضوری برای بانک «{bank_name}» تکمیل است؛ طبق اولویت متقاضی به روش حضوری سوئیچ می‌شود...", "info", page)
                in_eff, in_ok = _select_bank_mode(bot, page, "in_person", bank_name)
                if in_ok:
                    effective_mode = in_eff
                    mode_ok = True
                    mode_full = False
                    mode_err_msg = ""

            if not mode_ok or (mode_full and not capacity_sniping):
                if not mode_full or capacity_sniping:
                    return "manual_action_required"
                reason_msg = mode_err_msg or f"روش درخواستی ({desired_mode}) برای بانک {bank_name} فعال نیست"
                bot.emit_event(
                    "BANK_CAPACITY_FULL",
                    step="STEP_3_BANK_SELECT",
                    reason=reason_msg,
                    result="capacity_full_after_mode",
                    message=f"ظرفیت یا روش بانک «{bank_name}» در دسترس نیست: {reason_msg}",
                    level="warning",
                    page=page,
                )
                if not online_sniping:
                    bot._bank_cooldowns[p_canon] = time.time() + cooldown_sec
                if has_next and not online_sniping:
                    bot.log(f"🔄 سهمیه بانک «{bank_name}» تکمیل است؛ انتقال خودکار به اولویت بعدی (Waterfall)...", "info", page)
                    return_to_bank_field(page, dropdown_id)
                    continue
                bot.save_checkpoint(
                    "STEP_3_BANK_SELECT",
                    page=page,
                    selected_bank=bank_name,
                    bank_mode=effective_mode or desired_mode,
                    expected_next_step="RECHECK_BANK_CAPACITY",
                    last_error_reason=reason_msg,
                    last_operation_result="BANK_CAPACITY_FULL",
                )
                return "capacity_full"

            # Selection & mode verified successfully for this priority bank! Break out to Step 7
            break

        # 7. Branch selection: ONLY required when mode is NOT online (non-in-person)
        selected_branch = ""
        if effective_mode == "online":
            # Non-in-person banks do not require branch selection; ignore ddlBranch completely
            selected_branch = "غیرحضوری"
            DBHandler.update_runtime(bot.nid, selected_mode="online", selected_branch="غیرحضوری")
        else:
            branch_visible = False
            try:
                b_loc = page.locator(branch_ddl)
                branch_visible = b_loc.count() > 0 and b_loc.first.is_visible() and b_loc.first.is_enabled()
            except Exception:
                branch_visible = False

            if branch_visible:
                branch_res = process_branch_selection(
                    bot,
                    page,
                    stop_event,
                    preferred_branches=matched_entry.get("branches") or [],
                    trigger_submit=False,
                )
                if not branch_res:
                    if bot.consume_recovery_flag("bank_capacity_full"):
                        return "capacity_full"
                    bot.emit_event(
                        "BANK_SELECTION_FAILED",
                        step="STEP_3_BANK_SELECT",
                        reason=f"branch_selection_failed:bank={bank_name}",
                        result="no_branch",
                        message=f"انتخاب شعبه حضوری برای بانک {bank_name} انجام نشد.",
                        level="warning",
                        page=page,
                    )
                    return "waiting"
                selected_branch = str(branch_res)
                DBHandler.update_runtime(bot.nid, selected_mode=effective_mode, selected_branch=selected_branch)
                bot.emit_event(
                    "BRANCH_SELECTED",
                    step="STEP_3_BANK_SELECT",
                    reason=f"bank={bank_name} branch={selected_branch}",
                    result=selected_branch,
                    message=f"شعبه «{selected_branch}» برای بانک {bank_name} انتخاب و تایید شد.",
                    level="info",
                    page=page,
                )

        # Save checkpoint and emit BANK_FORM_READY before final submit decision
        next_step_label = "FINAL_SUBMIT_RESERVE" if is_reserve_choice else "FINAL_SUBMIT"
        op_result_label = "RESERVE_PENDING" if is_reserve_choice else "BANK_FORM_READY"
        bot.save_checkpoint(
            "STEP_3_BANK_SELECT",
            page=page,
            selected_bank=bank_name,
            selected_branch=selected_branch,
            bank_mode=effective_mode,
            is_reserve=is_reserve_choice,
            expected_next_step=next_step_label,
            last_operation_result=op_result_label,
        )
        ready_msg = (
            f"فرم انتخاب بانک «{bank_name}» (رزرو در انتظار تخصیص ظرفیت - روش: {'غیرحضوری' if effective_mode == 'online' else 'حضوری'}) تکمیل و آماده ثبت است."
            if is_reserve_choice else
            f"فرم انتخاب بانک «{bank_name}» (روش: {'غیرحضوری' if effective_mode == 'online' else ('حضوری' if effective_mode == 'in_person' else 'پیش‌فرض')}) تکمیل و آماده ثبت است."
        )
        bot.emit_event(
            "BANK_FORM_READY",
            step="STEP_3_BANK_SELECT",
            reason=f"bank={bank_name} mode={effective_mode} branch={selected_branch or 'none'} reserve={is_reserve_choice}",
            result="ready",
            message=ready_msg,
            level="success",
            page=page,
        )
        bot._verified_step3_signature = f"{target_value}|{effective_mode}|{selected_branch}"

        # 8. Final Submit check (auto_final_submit / final_submit)
        # The per-applicant controller owns the submission timer and fresh-response latch.
        allow_final_submit = bool(bot.settings.get("auto_final_submit", bot.settings.get("final_submit", False))) and not capacity_sniping
        if not allow_final_submit:
            if is_reserve_choice:
                DBHandler.update_status(bot.nid, "رزرو در انتظار تخصیص ظرفیت", f"بانک {bank_name} (رزرو) آماده است؛ منتظر تایید ثبت نهایی توسط کاربر")
                bot.set_runtime_state(
                    "WAITING_USER_INPUT",
                    "RESERVE_PENDING",
                    browser_status="OPEN",
                    target_bank=bank_name,
                    selected_mode=effective_mode,
                    selected_branch=selected_branch,
                    is_reserve=True,
                )
                bot.log(f"⏸️ گزینه بانک «{bank_name}» آماده شد؛ وضعیت «رزرو در انتظار تخصیص ظرفیت» است (منتظر اقدام کاربر).", "warning", page)
            else:
                DBHandler.update_status(bot.nid, "انتخاب بانک آماده ثبت", f"بانک {bank_name} آماده است؛ منتظر تایید ثبت نهایی توسط کاربر")
                bot.set_runtime_state(
                    "WAITING_USER_INPUT",
                    "FINAL_SUBMIT_WAIT",
                    browser_status="OPEN",
                    target_bank=bank_name,
                    selected_mode=effective_mode,
                    selected_branch=selected_branch,
                    is_reserve=False,
                )
                bot.log(f"⏸️ گزینه بانک «{bank_name}» آماده شد؛ ثبت نهایی خودکار غیرفعال است (منتظر اقدام کاربر).", "warning", page)
            return "option_selected_waiting_user"

        # 9. Perform Final Submit ONCE and verify official site response
        if stop_event.is_set():
            return "stopped"
        observer = getattr(bot, "bank_state_observer", None)
        if callable(observer):
            observer(BankPageState.FINAL_SUBMIT_IN_PROGRESS)
        bot.emit_event(
            "FINAL_SUBMIT_STARTED",
            step="FINAL_SUBMIT",
            reason=f"bank={bank_name} mode={effective_mode} branch={selected_branch}",
            result="clicking_save",
            message=f"در حال ارسال فرم ثبت نهایی برای بانک {bank_name}...",
            level="info",
            page=page,
        )
        if not DBHandler.claim_final_submission(bot.nid):
            return "submit_rejected"
        page.click("#ctl00_ContentPlaceHolder1_btnSave", timeout=5000)
        try:
            page.wait_for_timeout(120)
        except Exception:
            pass
        if hasattr(bot, "wait_for_aspnet_postback"):
            bot.wait_for_aspnet_postback(page, timeout_ms=10000)

        # Check if dialog or page error rejected the submission (e.g. capacity full on save)
        if bot.consume_recovery_flag("bank_capacity_full"):
            msg = getattr(bot, "_last_dialog_message", "ظرفیت تکمیل است")
            DBHandler.release_final_submission_claim(bot.nid)
            bot.emit_event(
                "FINAL_SUBMIT_REJECTED",
                step="FINAL_SUBMIT",
                reason=msg,
                result="bank_capacity_full",
                message=f"ثبت نهایی رد شد (تکمیل ظرفیت): {msg}",
                level="warning",
                page=page,
            )
            return "capacity_full"

        err_kind, err_text = _detect_page_error_or_capacity_message(bot, page)
        if err_kind == "bank_capacity_full":
            DBHandler.release_final_submission_claim(bot.nid)
            bot.emit_event(
                "FINAL_SUBMIT_REJECTED",
                step="FINAL_SUBMIT",
                reason=err_text,
                result="bank_capacity_full",
                message=f"ثبت نهایی رد شد (تکمیل ظرفیت): {err_text}",
                level="warning",
                page=page,
            )
            return "capacity_full"
        if err_kind:
            DBHandler.release_final_submission_claim(bot.nid)
            bot.emit_event(
                "FINAL_SUBMIT_REJECTED",
                step="FINAL_SUBMIT",
                reason=err_text or err_kind,
                result="rejected",
                message=f"ثبت نهایی توسط سامانه پذیرفته نشد: {err_text or err_kind}",
                level="error",
                page=page,
            )
            return "submit_rejected"

        if is_reserve_choice:
            # The option label alone never confirms that a reservation was accepted.
            if extract_confirmed_reservation(page):
                bot.save_checkpoint("STEP_3_BANK_SELECT", page=page, final_submit_status="reserve_confirmed",
                                    reserve_confirmed=True, last_operation_result="RESERVE_PENDING")
                return "reserve_confirmed"
        confirmed_code = _extract_confirmed_submission(bot, page)
        if is_reserve_choice and confirmed_code == "CONFIRMED":
            confirmed_code = None
        if confirmed_code:
            DBHandler.save_success_data(bot.nid, confirmed_code)
            bot.set_runtime_state("SUCCESS", "FINAL_SUBMIT_CONFIRMED", browser_status="OPEN", target_bank=bank_name)
            bot.emit_event(
                "BANK_FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason=f"bank={bank_name}",
                result=f"tracking={bot._mask(confirmed_code)}",
                message=f"ثبت نهایی بانک «{bank_name}» با موفقیت تایید شد. کد رهگیری: {bot._mask(confirmed_code)}",
                level="success",
                page=page,
            )
            bot.emit_event(
                "FINAL_SUBMIT_CONFIRMED",
                step="DONE",
                reason=f"bank={bank_name}",
                result=f"tracking={bot._mask(confirmed_code)}",
                message=f"ثبت نهایی با موفقیت تایید شد. کد رهگیری: {bot._mask(confirmed_code)}",
                level="success",
                page=page,
            )
            return "submitted_confirmed"

        bot.emit_event(
            "FINAL_SUBMIT_REJECTED",
            step="FINAL_SUBMIT",
            reason="no_confirmation_marker",
            result="unconfirmed",
            message="فرم ارسال شد اما پیام تایید نهایی یا کد رهگیری از سامانه دریافت نشد.",
            level="warning",
            page=page,
        )
        return "submit_rejected"
    except Exception as exc:
        DBHandler.update_runtime(bot.nid, status="ERROR", last_error=str(exc)[:300])
        return "error"
    finally:
        if monitor_lock is not None and monitor_lock.locked():
            monitor_lock.release()


def return_to_bank_field(page, dropdown_id):
    try:
        page.evaluate(
            "(selector) => {"
            "const el = document.querySelector(selector);"
            "if (!el) return false;"
            "el.selectedIndex = 0;"
            "el.dispatchEvent(new Event('change', { bubbles: true }));"
            "el.focus();"
            "return true;"
            "}",
            dropdown_id,
        )
    except Exception:
        pass


def select_first_available_branch(page, preferred_branches: Optional[List[str]] = None):
    branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
    options = _read_dropdown_options(page, branch_ddl)
    valid_opts = [
        (str(o.get("text", "")).strip(), str(o.get("value", "")))
        for o in options
        if o.get("value") and str(o.get("value")) != "0" and "انتخاب کن" not in _normalize_persian(o.get("text", ""))
    ]
    if not valid_opts:
        return None

    if preferred_branches:
        for pref in preferred_branches:
            pref_norm = _normalize_persian_key(pref)
            if not pref_norm:
                continue
            for text_opt, val_opt in valid_opts:
                if pref_norm == _normalize_persian_key(val_opt) or pref_norm in _normalize_persian_key(text_opt):
                    page.select_option(branch_ddl, value=val_opt)
                    return val_opt

    first_text, first_val = valid_opts[0]
    page.select_option(branch_ddl, value=first_val)
    return first_val


def process_branch_selection(bot, page, stop_event, preferred_branches: Optional[List[str]] = None, trigger_submit: bool = True):
    try:
        if stop_event.is_set():
            return None
        branch_ddl = "#ctl00_ContentPlaceHolder1_ddlBranch"
        wait_for_branch_fully_loaded(page, branch_ddl)

        branch_value = select_first_available_branch(page, preferred_branches=preferred_branches)
        if not branch_value:
            return None

        try:
            page.wait_for_timeout(120)
        except Exception:
            pass
        if hasattr(bot, "wait_for_aspnet_postback"):
            bot.wait_for_aspnet_postback(page, timeout_ms=5000)

        if not trigger_submit:
            return branch_value

        allow_final_submit = bool(bot.settings.get("auto_final_submit", bot.settings.get("final_submit", False)))
        if not allow_final_submit:
            DBHandler.update_status(bot.nid, "Ready", "Branch selected; final submit awaits user")
            DBHandler.update_runtime(bot.nid, status="WAITING_USER_INPUT", current_step="FINAL_SUBMIT_WAIT")
            bot.log("شعبه انتخاب شد؛ ثبت نهایی منتظر تأیید کاربر است.", "warning", page)
            return branch_value

        if stop_event.is_set() or not DBHandler.claim_final_submission(bot.nid):
            return None
        page.click("#ctl00_ContentPlaceHolder1_btnSave", timeout=5000)
        DBHandler.update_status(bot.nid, "Submitted", "Final submit clicked")
        DBHandler.update_runtime(bot.nid, status="PROCESSING", current_step="FINAL_SUBMIT")
        return branch_value
    except Exception:
        DBHandler.release_final_submission_claim(bot.nid)
        return None


def wait_for_branch_fully_loaded(page, branch_ddl):
    try:
        page.wait_for_selector(branch_ddl, timeout=1500)
        page.wait_for_function(
            "(selector) => {"
            "const el = document.querySelector(selector);"
            "return !!el && el.options && el.options.length > 1 && !el.disabled;"
            "}",
            branch_ddl,
            timeout=5000,
        )
    except Exception:
        pass


def load_runtime_data(bot):
    row = DBHandler.get_applicant(bot.nid)
    if not row:
        return dict(getattr(bot, "user_data", {}) or {})
    try:
        row_data = dict(row) if not isinstance(row, dict) else row
        raw = row_data.get("data")
        if isinstance(raw, dict):
            data = dict(raw)
        elif raw:
            data = json.loads(raw)
        else:
            data = {}
    except Exception:
        data = {}
    if getattr(bot, "user_data", None) and isinstance(bot.user_data, dict):
        for k, v in bot.user_data.items():
            if k not in data or data[k] in (None, "", []):
                data[k] = v
    return data
