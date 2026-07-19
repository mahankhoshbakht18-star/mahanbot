from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from bank_archive import BANK_ARCHIVE_STORE
from database import DBHandler
from event_logger import EVENT_BROADCASTER, build_event


_INSTALLED = False
FINAL_SUBMIT_KEY = "bank_final_submit_enabled"


def _bank_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    return str(value or "").strip()


def _selected_text(page: Any, selector: str) -> str:
    try:
        return str(
            page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el || el.selectedIndex < 0) return '';
                    return (el.options[el.selectedIndex]?.textContent || '').trim();
                }""",
                selector,
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _available_banks(page: Any) -> List[Tuple[str, str]]:
    selector = "#ctl00_ContentPlaceHolder1_ddlBankName"
    try:
        options = page.evaluate(
            """(selector) => {
                const el = document.querySelector(selector);
                if (!el) return [];
                return Array.from(el.options || []).map(opt => ({
                    value: String(opt.value || ''),
                    text: (opt.textContent || '').trim()
                }));
            }""",
            selector,
        ) or []
    except Exception:
        options = []
    result: List[Tuple[str, str]] = []
    for item in options:
        text = str((item or {}).get("text") or "").strip()
        value = str((item or {}).get("value") or "").strip()
        if text and value and value != "0":
            result.append((text, value))
    return result


def _user_banks(bot: Any) -> Tuple[List[str], List[str]]:
    try:
        runtime_data = bot._load_runtime_data()
    except Exception:
        runtime_data = {}
    priorities = (
        runtime_data.get("priority_banks")
        or getattr(bot, "user_data", {}).get("priority_banks")
        or runtime_data.get("banks")
        or []
    )
    favorites = (
        runtime_data.get("favorite_banks")
        or getattr(bot, "user_data", {}).get("favorite_banks")
        or []
    )
    return (
        [name for name in (_bank_name(item) for item in priorities) if name],
        [name for name in (_bank_name(item) for item in favorites) if name],
    )


def _applicant_data(nid: str) -> Dict[str, Any]:
    row = DBHandler.get_applicant(nid)
    if not row:
        return {}
    try:
        row_data = dict(row) if not isinstance(row, dict) else row
        value = row_data.get("data")
        if isinstance(value, dict):
            return dict(value)
        return json.loads(value or "{}")
    except Exception:
        return {}


def _final_submit_enabled(nid: str) -> bool:
    return bool(_applicant_data(nid).get(FINAL_SUBMIT_KEY, False))


def _revoke_final_submit(nid: str) -> None:
    DBHandler.update_applicant_data(nid, {FINAL_SUBMIT_KEY: False})


def _emit_bank_matches(bot: Any, page: Any) -> None:
    available = _available_banks(page)
    if not available:
        return
    priorities, favorites = _user_banks(bot)
    watched = []
    for name in priorities + favorites:
        if name and name not in watched:
            watched.append(name)

    notified = getattr(bot, "_operation_bank_notified", None)
    if not isinstance(notified, set):
        notified = set()
        setattr(bot, "_operation_bank_notified", notified)

    available_labels = [text for text, _ in available]
    for requested in watched:
        match = next((text for text, _ in available if requested in text), None)
        if not match or requested in notified:
            continue
        notified.add(requested)
        archive: Optional[Dict[str, Any]] = None
        try:
            archive = BANK_ARCHIVE_STORE.capture(
                page,
                bot.nid,
                requested,
                stage="available",
                available_banks=available_labels,
                metadata={
                    "matched_option": match,
                    "priority": requested in priorities,
                    "favorite": requested in favorites,
                },
            )
        except Exception as exc:
            try:
                bot.log(f"Bank archive failed for {requested}: {exc}", "warning", page)
            except Exception:
                pass

        EVENT_BROADCASTER.emit_event(
            build_event(
                "bank_match_found",
                nid=bot.nid,
                bank=requested,
                detail=match,
                priority=requested in priorities,
                favorite=requested in favorites,
                archive=archive,
            )
        )
        try:
            bot.log(f"🔔 بانک منتخب موجود شد: {requested} ({match})", "success", page)
        except Exception:
            pass


def _wait_for_final_permission(bot: Any, page: Any, stop_event: Any, bank: str, branch: str) -> bool:
    announced = False
    while not stop_event.is_set():
        if _final_submit_enabled(bot.nid):
            return True
        if not announced:
            announced = True
            DBHandler.update_status(bot.nid, "Awaiting Final Submit", f"Bank: {bank}; Branch: {branch}")
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "final_submit_required",
                    nid=bot.nid,
                    bank=bank,
                    branch=branch,
                    message="بانک و شعبه آماده است؛ ثبت نهایی برای این متقاضی مجوز ندارد.",
                )
            )
            try:
                bot.log(
                    f"🔔 بانک {bank} و شعبه {branch} آماده است؛ ثبت نهایی غیرفعال است و دکمه ذخیره زده نشد.",
                    "warning",
                    page,
                )
            except Exception:
                pass
        try:
            if page.is_closed():
                return False
        except Exception:
            return False
        time.sleep(0.8)
    return False


def install_operation_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from bot_select import BankSelectionBot

    original_process_bank = BankSelectionBot._process_bank_selection_v2

    def process_bank_with_notifications(self: Any, page: Any, stop_event: Any):
        _emit_bank_matches(self, page)
        return original_process_bank(self, page, stop_event)

    def process_branch_with_checkpoint(self: Any, page: Any, stop_event: Any):
        branch_selector = "#ctl00_ContentPlaceHolder1_ddlBranch"
        bank_selector = "#ctl00_ContentPlaceHolder1_ddlBankName"
        try:
            self._wait_for_branch_fully_loaded(page, branch_selector)
            branch_value = page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const options = Array.from(el.options || []);
                    const first = options.find(item => item.value && item.value !== '0');
                    if (!first) return null;
                    el.value = first.value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return first.value;
                }""",
                branch_selector,
            )
            if not branch_value:
                return "waiting"

            bank_name = _selected_text(page, bank_selector) or "بانک"
            branch_name = _selected_text(page, branch_selector) or str(branch_value)
            archive = None
            try:
                archive = BANK_ARCHIVE_STORE.capture(
                    page,
                    self.nid,
                    bank_name,
                    stage="branch-ready",
                    available_banks=[text for text, _ in _available_banks(page)],
                    metadata={"branch": branch_name},
                )
            except Exception as exc:
                try:
                    self.log(f"Bank branch archive failed: {exc}", "warning", page)
                except Exception:
                    pass

            EVENT_BROADCASTER.emit_event(
                build_event(
                    "branch_ready",
                    nid=self.nid,
                    bank=bank_name,
                    branch=branch_name,
                    archive=archive,
                )
            )

            if not _wait_for_final_permission(self, page, stop_event, bank_name, branch_name):
                return "waiting"

            try:
                page.click("#ctl00_ContentPlaceHolder1_btnSave")
            finally:
                # Permission is one-shot even if the site rejects or interrupts the click.
                _revoke_final_submit(self.nid)

            DBHandler.update_status(self.nid, "Submitted", "Final submit clicked with one-shot applicant permission")
            EVENT_BROADCASTER.emit_event(
                build_event(
                    "final_submit_clicked",
                    nid=self.nid,
                    bank=bank_name,
                    branch=branch_name,
                )
            )
            self.log("✅ ثبت نهایی با مجوز یک‌بارمصرف همین متقاضی انجام شد.", "success", page)
            try:
                BANK_ARCHIVE_STORE.capture(
                    page,
                    self.nid,
                    bank_name,
                    stage="submitted",
                    metadata={"branch": branch_name},
                )
            except Exception:
                pass
            return "submitted"
        except Exception as exc:
            try:
                self.log(f"Branch/final-submit integration error: {exc}", "error", page)
            except Exception:
                pass
            return "error"

    BankSelectionBot._process_bank_selection_v2 = process_bank_with_notifications
    BankSelectionBot._process_branch_selection = process_branch_with_checkpoint
