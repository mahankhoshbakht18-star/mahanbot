"""
core/step3_bank_page.py — Clean DOM Controller for Page 3 (SelectBnkShb.aspx)
Handles:
1. Neutralizing CloseSession / onbeforeunload logout traps.
2. Managing Sys.WebForms.PageRequestManager AsyncPostBack on UpdatePanel2.
3. Reading ddlBankName and archiving banks_seen.json non-blockingly.
4. Fast bank selection and tracking code / error extraction.
"""
import json
import logging
import os
import re
import time
import threading
from typing import Any, Dict, List, Optional

from core.bank_matcher import BankOptionInfo, PersianBankMatcher

logger = logging.getLogger("mahanbot.step3_page")


class Step3BankPage:
    """Encapsulates all DOM interactions with CBI Step 3 (SelectBnkShb.aspx)."""

    CITY_SELECTOR = "#ctl00_ContentPlaceHolder1_ddlCity"
    BANK_SELECTOR = "#ctl00_ContentPlaceHolder1_ddlBankName"
    SUBMIT_BTN_SELECTOR = "#ctl00_ContentPlaceHolder1_btnSave"
    ERR_MSG_SELECTOR = "#ctl00_ContentPlaceHolder1_labErrMsg"
    PROGRESS_SELECTOR = "#ctl00_ContentPlaceHolder1_updateProgress"

    def __init__(self):
        self._last_archived_sig = ""

    def disable_logout_trap(self, page) -> bool:
        """
        Disarm the dangerous unload trap in Line 47 of SelectBnkShb.aspx:
        'function CloseSession() { location.href = 'LogOut.aspx'; } window.onbeforeunload = CloseSession;'
        Prevents session destruction on refresh or navigation back to OTP page.
        """
        try:
            page.evaluate("""() => {
                window.onbeforeunload = null;
                window.CloseSession = function() {};
                if (typeof window.onunload !== 'undefined') window.onunload = null;
                return true;
            }""")
            return True
        except Exception as e:
            logger.warning(f"Could not disable logout trap: {e}")
            return False

    def wait_for_update_panel(self, page, timeout_ms: int = 4000) -> bool:
        """
        Wait until Sys.WebForms.PageRequestManager finishes async postback
        and updateProgress indicator is hidden.
        """
        script = f"""() => {{
            return new Promise((resolve) => {{
                const start = Date.now();
                const check = () => {{
                    try {{
                        const prm = (window.Sys && window.Sys.WebForms && window.Sys.WebForms.PageRequestManager)
                            ? window.Sys.WebForms.PageRequestManager.getInstance()
                            : null;
                        const inPostBack = prm ? prm.get_isInAsyncPostBack() : false;
                        const prog = document.querySelector('{self.PROGRESS_SELECTOR}');
                        const progVisible = prog ? (prog.style.display !== 'none' && prog.offsetParent !== null) : false;
                        if (!inPostBack && !progVisible) {{
                            return resolve(true);
                        }}
                    }} catch (e) {{
                        return resolve(true);
                    }}
                    if (Date.now() - start > {timeout_ms}) {{
                        return resolve(false);
                    }}
                    setTimeout(check, 50);
                }};
                check();
            }});
        }}"""
        try:
            return bool(page.evaluate(script))
        except Exception:
            try:
                page.wait_for_timeout(400)
            except Exception:
                pass
            return True

    def ensure_city_selected(self, page, target_city: str) -> bool:
        """Verify or select city dropdown and wait for postback."""
        if not target_city:
            return True
        norm_city = PersianBankMatcher.normalize(target_city)
        try:
            script = f"""(normCity) => {{
                const ddl = document.querySelector('{self.CITY_SELECTOR}');
                if (!ddl) return 'missing';
                const currentVal = ddl.value;
                const currentText = ddl.options[ddl.selectedIndex] ? ddl.options[ddl.selectedIndex].text : '';
                if (currentText.includes(normCity) && currentVal && currentVal !== '0') {{
                    return 'already_selected';
                }}
                for (let i = 0; i < ddl.options.length; i++) {{
                    if (ddl.options[i].text.includes(normCity) && ddl.options[i].value && ddl.options[i].value !== '0') {{
                        ddl.selectedIndex = i;
                        if (typeof window.__doPostBack === 'function') {{
                            window.__doPostBack('{self.CITY_SELECTOR}'.replace('#ctl00_ContentPlaceHolder1_', 'ctl00$ContentPlaceHolder1$'), '');
                        }} else {{
                            ddl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                        return 'selected';
                    }}
                }}
                return 'not_found';
            }}"""
            result = page.evaluate(script, norm_city)
            if result == "selected":
                self.wait_for_update_panel(page)
            return result in {"already_selected", "selected"}
        except Exception as e:
            logger.warning(f"Error ensuring city selected: {e}")
            return False

    def read_live_bank_options(self, page) -> List[BankOptionInfo]:
        """Read all available bank options from ddlBankName."""
        script = f"""() => {{
            const ddl = document.querySelector('{self.BANK_SELECTOR}');
            if (!ddl) return [];
            const results = [];
            for (let i = 0; i < ddl.options.length; i++) {{
                const opt = ddl.options[i];
                const val = (opt.value || '').trim();
                const txt = (opt.text || '').trim();
                if (!val || val === '0' || txt.includes('-- انتخاب') || txt.includes('--انتخاب')) {{
                    continue;
                }}
                results.push({{ value: val, text: txt }});
            }}
            return results;
        }}"""
        try:
            raw_options = page.evaluate(script)
            parsed_list = []
            for item in raw_options:
                val = str(item.get("value", "")).strip()
                txt = str(item.get("text", "")).strip()
                if not val or val == "0" or "انتخاب" in txt:
                    continue
                opt_info = PersianBankMatcher.parse_option_text(txt)
                opt_info.value = val
                parsed_list.append(opt_info)
            return parsed_list
        except Exception as e:
            logger.warning(f"Failed to read live bank options: {e}")
            return []

    def inject_saved_bank_option(self, page, bank_code: str, bank_name: str) -> bool:
        """
        Inject a previously seen or static bank option into ddlBankName.
        If the server database has open quota for this bank code at this moment,
        submitting btnSave will succeed.
        """
        script = f"""(code, name) => {{
            const ddl = document.querySelector('{self.BANK_SELECTOR}');
            if (!ddl) return false;
            // Check if already exists
            for (let i = 0; i < ddl.options.length; i++) {{
                if (String(ddl.options[i].value).trim() === String(code).trim()) {{
                    ddl.selectedIndex = i;
                    return true;
                }}
            }}
            // Inject as active capacity option
            const opt = document.createElement('option');
            opt.value = code;
            opt.text = name + ' [دارای ظرفیت ثبت نام]';
            opt.selected = true;
            ddl.appendChild(opt);
            ddl.value = code;
            ddl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}"""
        try:
            return bool(page.evaluate(script, (bank_code, bank_name)))
        except Exception as e:
            logger.warning(f"Failed to inject saved bank option {bank_code}: {e}")
            return False

    def select_bank_option(self, page, bank_value: str) -> bool:
        """Select bank in ddlBankName and trigger native ASP.NET postback."""
        script = f"""(bankVal) => {{
            const ddl = document.querySelector('{self.BANK_SELECTOR}');
            if (!ddl) return false;
            let found = false;
            for (let i = 0; i < ddl.options.length; i++) {{
                if (String(ddl.options[i].value).trim() === String(bankVal).trim()) {{
                    ddl.selectedIndex = i;
                    found = true;
                    break;
                }}
            }}
            if (!found) return false;
            if (typeof window.__doPostBack === 'function') {{
                window.__doPostBack('{self.BANK_SELECTOR}'.replace('#ctl00_ContentPlaceHolder1_', 'ctl00$ContentPlaceHolder1$'), '');
            }} else {{
                ddl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            return true;
        }}"""
        try:
            ok = page.evaluate(script, bank_value)
            if ok:
                self.wait_for_update_panel(page)
            return bool(ok)
        except Exception as e:
            logger.warning(f"Failed to select bank option {bank_value}: {e}")
            return False

    def click_submit_btn(self, page) -> bool:
        """Click final submit button btnSave."""
        try:
            btn = page.locator(self.SUBMIT_BTN_SELECTOR)
            if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
                btn.first.click()
                return True
            # Fallback evaluate click
            return bool(page.evaluate(f"""() => {{
                const btn = document.querySelector('{self.SUBMIT_BTN_SELECTOR}');
                if (btn && !btn.disabled) {{
                    btn.click();
                    return true;
                }}
                return false;
            }}"""))
        except Exception as e:
            logger.warning(f"Failed to click submit button: {e}")
            return False

    def extract_tracking_or_error(self, page) -> Dict[str, Any]:
        """
        Check whether registration succeeded with tracking code,
        or failed with error banner.
        """
        script = f"""() => {{
            const bodyText = document.body ? document.body.innerText : '';
            const errEl = document.querySelector('{self.ERR_MSG_SELECTOR}');
            const errText = errEl ? (errEl.innerText || '').trim() : '';

            // Check for tracking code patterns
            const trackMatch = bodyText.match(/(?:کد\\s*رهگیری|شماره\\s*پیگیری|کد\\s*پیگیری)\\s*[:=]?\\s*([0-9]{{5,15}})/);
            if (trackMatch && trackMatch[1]) {{
                return {{
                    is_success: true,
                    tracking_code: trackMatch[1],
                    error_msg: ''
                }};
            }}

            if (errText) {{
                return {{
                    is_success: false,
                    tracking_code: null,
                    error_msg: errText
                }};
            }}

            return {{
                is_success: false,
                tracking_code: null,
                error_msg: ''
            }};
        }}"""
        try:
            res = page.evaluate(script)
            return res or {"is_success": False, "tracking_code": None, "error_msg": ""}
        except Exception as e:
            logger.warning(f"Failed to extract tracking or error: {e}")
            return {"is_success": False, "tracking_code": None, "error_msg": str(e)}

    def archive_banks_seen_nonblocking(self, bot, options: List[BankOptionInfo], page=None):
        """Asynchronously archive banks_seen.json and snapshot without blocking execution."""
        if not options:
            return
        names = [opt.clean_title or opt.text for opt in options]
        sig = "|".join(sorted(names))
        if sig == self._last_archived_sig:
            return
        self._last_archived_sig = sig

        def _do_archive():
            try:
                base_dir = getattr(bot, "data_dir", os.path.join("static", "saved_pages"))
                os.makedirs(base_dir, exist_ok=True)
                json_path = os.path.join(base_dir, "banks_seen.json")
                seen_data = {}
                if os.path.exists(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            seen_data = json.load(f)
                    except Exception:
                        pass
                seen_data["last_update"] = time.time()
                seen_data["signature"] = sig
                seen_data["banks"] = [{"value": o.value, "text": o.text, "active": o.has_active_quota} for o in options]
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(seen_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        threading.Thread(target=_do_archive, daemon=True).start()
