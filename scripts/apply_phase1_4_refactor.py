from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> bool:
    text = _read(path)
    if new in text:
        return False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one exact match, found {count}: {old[:80]!r}")
    _write(path, text.replace(old, new, 1))
    return True


def regex_once(path: str, pattern: str, replacement: str, *, flags: int = 0) -> bool:
    text = _read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count == 0 and replacement in text:
        return False
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, found {count}: {pattern[:100]!r}")
    _write(path, updated)
    return True


def patch_browser_launcher() -> None:
    regex_once(
        "browser_launcher.py",
        r"\n        # ✅ Required: prevent soft blocks / reduce automation fingerprint\n"
        r"        if normalized\[\"browser\"\] == \"chromium\":\n"
        r"            args = launch_options\.setdefault\(\"args\", \[\]\)\n"
        r"            if \"--disable-blink-features=AutomationControlled\" not in args:\n"
        r"                args\.append\(\"--disable-blink-features=AutomationControlled\"\)\n",
        "\n        # Keep Playwright browser defaults; do not alter identity or anti-automation signals.\n",
    )


def patch_database() -> None:
    replace_once(
        "database.py",
        "import sqlite3\nimport json\nimport os\nimport sys\nimport time\nfrom contextlib import contextmanager\nfrom typing import Any, Dict, Optional\n",
        "import sqlite3\nimport json\nimport os\nimport shutil\nimport sys\nimport time\nfrom contextlib import contextmanager\nfrom pathlib import Path\nfrom typing import Any, Dict, Optional\n\nfrom runtime_config import mask_secret\n",
    )
    replace_once(
        "database.py",
        'DB_PATH = resource_path("cbi_ultimate.db")\n',
        '''LEGACY_DB_PATH = Path(resource_path("cbi_ultimate.db"))\n\n\ndef _resolve_database_path() -> str:\n    configured = str(os.getenv("MAHANBOT_DB_PATH") or "").strip()\n    if configured:\n        target = Path(configured).expanduser()\n    elif sys.platform == "win32":\n        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))\n        target = base / "MahanBot" / "cbi_ultimate.db"\n    else:\n        base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))\n        target = base / "mahanbot" / "cbi_ultimate.db"\n\n    target.parent.mkdir(parents=True, exist_ok=True)\n    try:\n        legacy = LEGACY_DB_PATH.resolve()\n        resolved_target = target.resolve()\n        if not target.exists() and legacy.exists() and legacy != resolved_target:\n            shutil.copy2(legacy, target)\n    except OSError:\n        pass\n    return str(target)\n\n\nDB_PATH = _resolve_database_path()\n''',
    )
    replace_once("database.py", '"retry_count": 1000,', '"retry_count": 50,')
    replace_once("database.py", '"captcha_mode": "robot",', '"captcha_mode": "manual",')
    replace_once(
        "database.py",
        '                data.pop("otp_ts", None)\n',
        '                data.pop("otp_ts", None)\n                data.pop("otp_ts_ms", None)\n',
    )
    replace_once(
        "database.py",
        '                    "predicted_text": predicted_text,',
        '                    "predicted_text": mask_secret(predicted_text),',
    )


def patch_captcha_service() -> None:
    replace_once(
        "captcha_service.py",
        "import os\n",
        "import os\n\nfrom runtime_config import legacy_captcha_model_enabled, mask_secret\n",
    )
    replace_once(
        "captcha_service.py",
        '''    def __init__(self, model=None, ocr_firewall=None):\n        if model is None or ocr_firewall is None:\n            model, ocr_firewall = load_captcha_resources()\n        self._model = model\n        self._ocr_firewall = ocr_firewall\n''',
        '''    def __init__(self, model=None, ocr_firewall=None, enabled=None):\n        self._enabled = legacy_captcha_model_enabled() if enabled is None else bool(enabled)\n        if self._enabled and (model is None or ocr_firewall is None):\n            model, ocr_firewall = load_captcha_resources()\n        self._model = model if self._enabled else None\n        self._ocr_firewall = ocr_firewall if self._enabled else None\n''',
    )
    replace_once(
        "captcha_service.py",
        "        image_bytes = self._normalize_image_bytes(image_bytes)\n",
        "        if not self._enabled:\n            return None\n        image_bytes = self._normalize_image_bytes(image_bytes)\n",
    )
    replace_once(
        "captcha_service.py",
        '        print(f"🤖 مدل خواند: {text}")\n',
        '        print(f"🤖 مدل کپچا پاسخ تولید کرد: {mask_secret(text)}")\n',
    )
    replace_once(
        "captcha_service.py",
        '            print(f"🛡️ فایروال حل شد: {res}")\n',
        '            print("🛡️ پاسخ OCR فایروال تولید شد؛ ارسال خودکار غیرفعال است.")\n',
    )
    replace_once(
        "captcha_service.py",
        '''    def _solve_firewall_local(self, image_bytes):\n        """حل فایروال (کپچای متفاوت)"""\n        try:\n''',
        '''    def _solve_firewall_local(self, image_bytes):\n        """Legacy OCR helper. WAF submission remains operator-driven."""\n        if self._ocr_firewall is None:\n            return None\n        try:\n''',
    )
    replace_once("captcha_service.py", "        except:\n            return None\n", "        except Exception:\n            return None\n")


def patch_bot_core() -> None:
    replace_once(
        "bot_core.py",
        "from browser_actions import BrowserActions\n",
        "from browser_actions import BrowserActions\nfrom runtime_config import api_headers, env_int\n",
    )
    replace_once(
        "bot_core.py",
        '''        self.retry_limit = int(self.settings.get('retry_count', 1000))\n        self.api_base_url = "https://python-ke7tg2.chbk.dev"\n        self.local_api_base = os.getenv("MAHANBOT_LOCAL_API", "http://127.0.0.1:8000")\n''',
        '''        try:\n            configured_retry = int(self.settings.get("retry_count", 50))\n        except (TypeError, ValueError):\n            configured_retry = 50\n        self.retry_limit = env_int("MAHANBOT_RETRY_LIMIT", configured_retry, minimum=1, maximum=500)\n        self.api_base_url = str(os.getenv("MAHANBOT_REMOTE_API") or "").strip().rstrip("/")\n        self.local_api_base = os.getenv("MAHANBOT_LOCAL_API", "http://127.0.0.1:8000").rstrip("/")\n        self._api_headers = api_headers()\n''',
    )
    replace_once(
        "bot_core.py",
        '                self.log(f"✅ OTP received from /wait_otp: {code}", "success")\n',
        '                self.log("✅ OTP تازه از سرویس محلی دریافت شد.", "success")\n',
    )
    replace_once(
        "bot_core.py",
        '            response = requests.get(f"{self.api_base_url}/get_otp/{self.nid}", timeout=3)\n',
        '            if not self.api_base_url:\n                raise RuntimeError("Remote OTP API is disabled")\n            response = requests.get(\n                f"{self.api_base_url}/get_otp/{self.nid}",\n                headers=self._api_headers,\n                timeout=3,\n            )\n',
    )
    replace_once(
        "bot_core.py",
        '                    self.log(f"[NID: {self.nid}] ✅ OTP from API synced to DB: {code}", "success")\n',
        '                    self.log("✅ OTP از API پشتیبان دریافت و همگام شد.", "success")\n',
    )
    replace_once(
        "bot_core.py",
        '''                params={"timeout": timeout, "min_ts": min_ts_value},\n                timeout=timeout + 5,\n''',
        '''                params={"timeout": timeout, "min_ts": min_ts_value},\n                headers=self._api_headers,\n                timeout=timeout + 5,\n''',
    )
    regex_once(
        "bot_core.py",
        r"    def clear_otp_backend\(self\):\n.*?(?=    def clear_remote_otp)",
        '''    def clear_otp_backend(self):\n        try:\n            response = requests.post(\n                f"{self.local_api_base}/otp/clear/{self.nid}",\n                headers=self._api_headers,\n                timeout=3,\n            )\n            if response.ok:\n                self._otp_entry_time = 0.0\n                return\n        except Exception:\n            pass\n        try:\n            DBHandler.clear_otp(self.nid)\n        finally:\n            self._otp_entry_time = 0.0\n\n''',
        flags=re.S,
    )
    regex_once(
        "bot_core.py",
        r"    def solve_firewall\(self, page\):\n.*?(?=    def mark_progress)",
        '''    def solve_firewall(self, page):\n        """Do not submit WAF/firewall challenges automatically."""\n        if not self.is_firewall_challenge(page):\n            return False\n        self.log(\n            "🛡️ چالش امنیتی/WAF شناسایی شد؛ ادامه نیازمند اقدام دستی اپراتور است.",\n            "warning",\n            page,\n        )\n        DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", "Manual firewall challenge")\n        return False\n\n''',
        flags=re.S,
    )


def patch_server() -> None:
    replace_once(
        "server.py",
        "    log_event,\n    set_main_loop,\n)",
        "    log_event,\n    redact_sensitive_payload,\n    set_main_loop,\n)",
    )
    replace_once(
        "server.py",
        "from captcha_service import CaptchaService, load_captcha_resources\n",
        "from captcha_service import CaptchaService\nfrom runtime_config import api_key_is_valid, configured_api_key, cors_origins, dev_mode_enabled\n",
    )
    replace_once(
        "server.py",
        '''app.add_middleware(\n    CORSMiddleware,\n    allow_origins=["*"],\n    allow_methods=["*"],\n    allow_headers=["*"],\n)\n''',
        '''app.add_middleware(\n    CORSMiddleware,\n    allow_origins=cors_origins(),\n    allow_credentials=False,\n    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],\n    allow_headers=["Content-Type", "X-API-KEY"],\n)\n''',
    )
    regex_once(
        "server.py",
        r"def require_api_key\(x_api_key: Optional\[str\] = Header\(None, alias=\"X-API-KEY\"\)\):\n.*?    return True\n\n",
        '''def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-KEY")):\n    if api_key_is_valid(x_api_key):\n        return True\n    if not configured_api_key() and not dev_mode_enabled():\n        raise HTTPException(\n            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,\n            detail="MAHANBOT_API_KEY is required outside development mode.",\n        )\n    raise HTTPException(\n        status_code=status.HTTP_401_UNAUTHORIZED,\n        detail=get_message("responses", "api_key_invalid", RESPONSES["api_key_invalid"]),\n    )\n\n''',
        flags=re.S,
    )
    replace_once(
        "server.py",
        '''    s = s.replace("-", "").replace(" ", "")\n    return s\n\n\n@app.on_event("startup")\n''',
        '''    s = s.replace("-", "").replace(" ", "")\n    return s\n\n\ndef _normalize_otp(value: Optional[str]) -> str:\n    if value is None:\n        return ""\n    code = str(value).strip().translate(\n        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")\n    )\n    if not code.isdigit() or not 4 <= len(code) <= 8:\n        return ""\n    return code\n\n\n@app.on_event("startup")\n''',
    )
    replace_once(
        "server.py",
        '''    model, ocr_firewall = load_captcha_resources()\n    CAPTCHA_SERVICE = CaptchaService(model=model, ocr_firewall=ocr_firewall)\n''',
        '''    CAPTCHA_SERVICE = CaptchaService()\n''',
    )
    replace_once(
        "server.py",
        '                    record["data"] = json.loads(record["data"]) if record.get("data") else {}\n',
        '                    record["data"] = redact_sensitive_payload(\n                        json.loads(record["data"]) if record.get("data") else {}\n                    )\n',
    )
    replace_once(
        "server.py",
        '    log_event(nid, None, f"OTP received: {code}", "success")\n',
        '    log_event(nid, None, "OTP received and stored.", "success")\n',
    )
    replace_once("server.py", "def receive_sms(req: SMSRequest):", "def receive_sms(req: SMSRequest, _: bool = Depends(require_api_key)):")
    replace_once("server.py", "def manual_otp(req: SMSRequest):", "def manual_otp(req: SMSRequest, _: bool = Depends(require_api_key)):")
    replace_once("server.py", '    code = str(req.code or "").strip()\n', '    code = _normalize_otp(req.code)\n')
    replace_once(
        "server.py",
        '    log_event(final_nid, None, f"OTP received manually: {code}", "success")\n',
        '    log_event(final_nid, None, "OTP received manually and stored.", "success")\n',
    )
    replace_once(
        "server.py",
        "async def wait_otp(nid: str, timeout: int = 120, min_ts: float = 0.0):",
        "async def wait_otp(\n    nid: str,\n    timeout: int = 120,\n    min_ts: float = 0.0,\n    _: bool = Depends(require_api_key),\n):",
    )
    old_wait_loop = '''    while True:\n        record = DBHandler.get_otp_record(nid or raw_nid)\n        if not record and raw_nid and raw_nid != nid:\n            record = DBHandler.get_otp_record(raw_nid)\n        record_ts = 0.0\n        if record:\n            try:\n                record_ts = float(record.get("ts") or 0.0)\n            except Exception:\n                record_ts = 0.0\n        if record and record.get("otp") and record_ts > min_ts:\n            logger.info("wait_otp released for %s ts=%s > min_ts=%s", nid, record_ts, min_ts)\n            return {\n                "nid": nid,\n                "otp": record["otp"],\n                "source": "fresh",\n                "ts": record_ts or time.time(),\n            }\n\n        remaining = deadline - time.monotonic()\n        if remaining <= 0:\n            logger.info("wait_otp timeout for %s", nid)\n            return Response(status_code=204)\n\n        event = await _get_otp_event(nid)\n        event.clear()\n        try:\n            await asyncio.wait_for(event.wait(), timeout=remaining)\n        except asyncio.TimeoutError:\n            logger.info("wait_otp timeout for %s", nid)\n            return Response(status_code=204)\n        finally:\n            event.clear()\n'''
    new_wait_loop = '''    event = await _get_otp_event(nid or raw_nid)\n    while True:\n        # Clear before reading the DB. If a sender sets the event after this\n        # point, wait() observes it; if it arrived earlier, the DB read sees it.\n        event.clear()\n        record = DBHandler.get_otp_record(nid or raw_nid)\n        if not record and raw_nid and raw_nid != nid:\n            record = DBHandler.get_otp_record(raw_nid)\n        record_ts = 0.0\n        if record:\n            try:\n                record_ts = float(record.get("ts") or 0.0)\n            except Exception:\n                record_ts = 0.0\n        if record and record.get("otp") and record_ts > min_ts:\n            logger.info("wait_otp released for %s ts=%s > min_ts=%s", nid, record_ts, min_ts)\n            return {\n                "nid": nid,\n                "otp": record["otp"],\n                "source": "fresh",\n                "ts": record_ts or time.time(),\n            }\n\n        remaining = deadline - time.monotonic()\n        if remaining <= 0:\n            logger.info("wait_otp timeout for %s", nid)\n            return Response(status_code=204)\n\n        try:\n            await asyncio.wait_for(event.wait(), timeout=remaining)\n        except asyncio.TimeoutError:\n            logger.info("wait_otp timeout for %s", nid)\n            return Response(status_code=204)\n'''
    replace_once("server.py", old_wait_loop, new_wait_loop)
    replace_once("server.py", "def clear_otp(nid: str):", "def clear_otp(nid: str, _: bool = Depends(require_api_key)):")
    replace_once("server.py", "async def save_applicant(req: ApplicantModel):", "async def save_applicant(req: ApplicantModel, _: bool = Depends(require_api_key)):")
    replace_once(
        "server.py",
        '    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)\n',
        '''    host = os.getenv("MAHANBOT_HOST", "127.0.0.1")\n    port = int(os.getenv("MAHANBOT_PORT", "8000"))\n    uvicorn.run("server:app", host=host, port=port, reload=False)\n''',
    )


def patch_bot_register() -> None:
    replace_once(
        "bot_register.py",
        "from event_logger import EVENT_BROADCASTER, build_event\n",
        "from event_logger import EVENT_BROADCASTER, build_event\nfrom runtime_config import legacy_captcha_model_enabled\n",
    )
    replace_once("bot_register.py", 'captcha_mode = self.settings.get("captcha_mode", "human")', 'captcha_mode = self.settings.get("captcha_mode", "manual")')
    otp_pattern = re.compile(
        r'''                        self\.log\(f"✅ کد پیامک دریافت شد: \{otp\}", "success", page\)\n'''
        r'''                        otp_selector = "#ctl00_ContentPlaceHolder1_tbMobileConfCode, input\[name='ctl00\$ContentPlaceHolder1\$tbMobileConfCode'\]"\n'''
        r'''                        otp_value = str\(otp\)\.strip\(\)\n'''
        r'''                        try:\n'''
        r'''                            page\.evaluate\(.*?'''
        r'''                        except Exception:\n                            pass\n'''
        r'''                        try:\n                            page\.wait_for_timeout\(300\)\n                        except Exception:\n                            pass\n''',
        re.S,
    )
    replacement = '''                        self.log("✅ کد پیامک تازه دریافت شد.", "success", page)\n                        otp_locator = self._find_locator(\n                            page,\n                            [\n                                "#ctl00_ContentPlaceHolder1_tbMobileConfCode",\n                                "input[name='ctl00$ContentPlaceHolder1$tbMobileConfCode']",\n                            ],\n                        )\n                        if not BrowserActions.fill_text(otp_locator, str(otp).strip()):\n                            self.log("❌ ورود کد پیامک در فیلد ناموفق بود.", "error", page)\n                            continue\n'''
    text = _read("bot_register.py")
    updated, count = otp_pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"bot_register.py: OTP injection block match count={count}")
    _write("bot_register.py", updated)
    regex_once(
        "bot_register.py",
        r"    def _fill_text\(self, page, selectors, value\):\n.*?(?=    def _select_option)",
        '''    def _fill_text(self, page, selectors, value):\n        if value is None or value == "":\n            return False\n        locator = self._find_locator(page, selectors)\n        return BrowserActions.fill_text(locator, value)\n\n''',
        flags=re.S,
    )
    regex_once(
        "bot_register.py",
        r"    def _select_option\(self, page, selector, value=None\):\n.*?(?=    def _get_birth_parts)",
        '''    def _select_option(self, page, selector, value=None):\n        if not value:\n            return False\n        locator = BrowserActions.resolve_locator(page, selector)\n        return BrowserActions.select_option(locator, value=str(value))\n\n''',
        flags=re.S,
    )
    regex_once(
        "bot_register.py",
        r"    def _solve_captcha_wrapper\(self, page, input_sel, btn_sel, mode\):\n.*?(?=    def _normalize_text)",
        '''    def _solve_captcha_wrapper(self, page, input_sel, btn_sel, mode):\n        selected_mode = str(mode or "manual").strip().lower()\n        if selected_mode in {"model", "robot"} and legacy_captcha_model_enabled():\n            return self._handle_captcha_model(page, input_sel, btn_sel)\n        return self._handle_captcha_manual(page, input_sel, btn_sel)\n\n    def _handle_captcha_manual(self, page, input_selector, btn_selector):\n        input_locator = BrowserActions.resolve_locator(page, input_selector)\n        button_locator = BrowserActions.resolve_locator(page, btn_selector)\n        if input_locator is None or button_locator is None:\n            return False\n        timeout_seconds = max(15, min(int(self.settings.get("captcha_manual_timeout", 180)), 600))\n        self.suspend_watchdog(timeout_seconds + 10)\n        self.log("🧩 لطفاً کپچا را در مرورگر وارد کنید؛ پس از ورود، ارسال ادامه می‌یابد.", "warning", page)\n        EVENT_BROADCASTER.emit_event(\n            build_event("human_required", nid=self.nid, reason="captcha_manual")\n        )\n        deadline = time.time() + timeout_seconds\n        while time.time() < deadline:\n            try:\n                code = input_locator.input_value().strip()\n            except Exception:\n                code = ""\n            if len(code) >= 4:\n                return BrowserActions.click_when_ready(button_locator)\n            time.sleep(0.25)\n        self.log("⌛ زمان ورود دستی کپچا پایان یافت.", "error", page)\n        return False\n\n    def _handle_captcha_model(self, page, input_selector, btn_selector):\n        try:\n            captcha_img = page.locator(".BDC_CaptchaImage").first\n            if not captcha_img.is_visible():\n                return False\n            code = (self.captcha_service.solve(captcha_img.screenshot(), mode="general") or "").strip()\n            if len(code) < 4:\n                reload_link = BrowserActions.resolve_locator(page, ".BDC_ReloadLink")\n                BrowserActions.click_when_ready(reload_link)\n                return False\n            input_locator = BrowserActions.resolve_locator(page, input_selector)\n            button_locator = BrowserActions.resolve_locator(page, btn_selector)\n            if not BrowserActions.fill_text(input_locator, code):\n                return False\n            if not BrowserActions.click_when_ready(button_locator):\n                return False\n            time.sleep(1)\n            result = "failed" if self._detect_captcha_failure(page) else "submitted"\n            DBHandler.append_captcha_attempt(self.nid, code, result, source="register")\n            return result != "failed"\n        except Exception:\n            return False\n\n''',
        flags=re.S,
    )


def patch_bot_select() -> None:
    replace_once(
        "bot_select.py",
        "from event_logger import EVENT_BROADCASTER, build_event\n",
        "from event_logger import EVENT_BROADCASTER, build_event\nfrom runtime_config import legacy_captcha_model_enabled\n",
    )
    replace_once(
        "bot_select.py",
        '''    def fetch_otp_fast(self, stop_event=None, timeout: int = 120, min_ts: float = 0.0):\n        """Fetch OTP from wait endpoint without min_ts filtering for immediate pickup."""\n        otp_code = self.wait_for_otp(stop_event=stop_event, timeout=timeout, min_ts=0.0)\n''',
        '''    def fetch_otp_fast(self, stop_event=None, timeout: int = 120, min_ts: float = None):\n        """Fetch only an OTP newer than the current OTP form entry time."""\n        effective_min_ts = self._otp_entry_time if min_ts is None else float(min_ts)\n        otp_code = self.wait_for_otp(\n            stop_event=stop_event,\n            timeout=timeout,\n            min_ts=effective_min_ts,\n        )\n''',
    )
    replace_once("bot_select.py", 'captcha_mode = self.settings.get("captcha_mode", "human")', 'captcha_mode = self.settings.get("captcha_mode", "manual")')
    replace_once(
        "bot_select.py",
        '''                    # Firewall-first priority (bot5 behavior): when #ans is visible, do not run other states.\n                    if self._firewall_input_visible(page):\n                        solved = self.solve_firewall(page)\n                        if solved:\n                            firewall_solved_count += 1\n                            DBHandler.update_status(self.nid, "Ready", "Firewall challenge cleared")\n                        if firewall_solved_count >= max_firewall_solves:\n                            self.log("⛔ Firewall solve limit reached; stopping job.", "error", page)\n                            DBHandler.update_status(self.nid, "Stopped", "Firewall solve limit reached")\n                            stop_event.set()\n                            break\n                        if sleep_with_stop(stop_event, 0.5):\n                            break\n                        continue\n''',
        '''                    # WAF/firewall challenges always pause for an operator.\n                    if self._firewall_input_visible(page):\n                        if not self.handle_firewall_challenge(page, stop_event):\n                            break\n                        DBHandler.update_status(self.nid, "Ready", "Firewall challenge cleared")\n                        continue\n''',
    )
    regex_once(
        "bot_select.py",
        r"    def handle_firewall_challenge\(self, page, stop_event\):\n.*?(?=    def _firewall_gate)",
        '''    def handle_firewall_challenge(self, page, stop_event):\n        self.log("🛡️ چالش امنیتی/WAF شناسایی شد؛ منتظر اقدام دستی اپراتور.", "warning", page)\n        timeout_seconds = max(30, min(int(self.settings.get("firewall_manual_timeout", 300)), 900))\n        deadline = time.time() + timeout_seconds\n        DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", "Manual firewall challenge")\n        EVENT_BROADCASTER.emit_event(\n            build_event("waf_manual_required", nid=self.nid, reason="firewall_challenge")\n        )\n        next_wait_log_at = 0.0\n        while not stop_event.is_set():\n            if not self.is_firewall_challenge(page):\n                self.log("✅ چالش امنیتی توسط اپراتور برطرف شد؛ ادامه عملیات.", "success", page)\n                return True\n            if time.time() > deadline:\n                self.log("⛔ مهلت اقدام دستی روی چالش امنیتی پایان یافت.", "error", page)\n                DBHandler.update_status(self.nid, "BLOCKED_FIREWALL_MANUAL", "Firewall timeout")\n                return False\n            now = time.time()\n            if now >= next_wait_log_at:\n                self.log("در انتظار رفع دستی چالش امنیتی...", "warning", page)\n                next_wait_log_at = now + 15.0\n            if sleep_with_stop(stop_event, 1.0):\n                return False\n        return False\n\n''',
        flags=re.S,
    )
    replace_once(
        "bot_select.py",
        '                    field.first.fill(self.nid)\n                    self._nid_filled = True\n',
        '                    if BrowserActions.fill_text(field.first, self.nid):\n                        self._nid_filled = True\n',
    )
    old_otp = '''        otp_code = None\n        try:\n            while not stop_event.is_set():\n                if self._firewall_gate(page, stop_event):\n                    if stop_event.is_set():\n                        return None\n                    continue\n\n                if self._detect_otp_failure(page) or self.last_dialog_indicates_otp_invalid():\n                    if not self.register_otp_failure("page_otp_invalid"):\n                        self.log("OTP retry limit reached; stopping job.", "error", page)\n                        DBHandler.update_status(self.nid, "Stopped", "OTP retry limit reached")\n                        stop_event.set()\n                        return None\n                    self.clear_otp_backend()\n                    self.mark_otp_entry_time()\n                    continue\n\n                otp_code = DBHandler.get_otp(self.nid)\n                if otp_code and re.fullmatch(r"\\d{6}", str(otp_code).strip()):\n                    otp_code = str(otp_code).strip()\n                    self.log(f"OTP received: {otp_code}", "success", page)\n                    break\n\n                if sleep_with_stop(stop_event, 0.3):\n                    return None\n\n            if stop_event.is_set() or not otp_code:\n                return None\n\n            page.fill(otp_selector, otp_code)\n'''
    new_otp = '''        try:\n            otp_code = self.fetch_otp_fast(\n                stop_event=stop_event,\n                timeout=120,\n                min_ts=self._otp_entry_time,\n            )\n            if stop_event.is_set() or not otp_code:\n                return None\n            otp_code = str(otp_code).strip()\n            if not re.fullmatch(r"\\d{4,8}", otp_code):\n                self.log("OTP دریافت‌شده قالب معتبر ندارد.", "error", page)\n                self.clear_otp_backend()\n                return None\n            self.log("OTP تازه دریافت شد و آماده ورود است.", "success", page)\n            otp_locator = BrowserActions.resolve_locator(page, otp_selector)\n            if not BrowserActions.fill_text(otp_locator, otp_code):\n                self.log("ورود OTP در فرم ناموفق بود.", "error", page)\n                return None\n'''
    replace_once("bot_select.py", old_otp, new_otp)
    replace_once(
        "bot_select.py",
        '''                self.log("Entered bank selection step.", "success", page)\n                return self._process_bank_selection_v2(page, stop_event)\n''',
        '''                self.log("Entered bank selection step.", "success", page)\n                self.clear_otp_backend()\n                return self._process_bank_selection_v2(page, stop_event)\n''',
    )
    regex_once(
        "bot_select.py",
        r"    def _fill_input_with_verification\(self, page, locator, value, label\):\n.*?(?=    def _collect_selector_debug)",
        '''    def _fill_input_with_verification(self, page, locator, value, label):\n        success = BrowserActions.fill_text(locator, value)\n        if success:\n            self.log(f"✅ Filled {label} successfully.", "success", page)\n            return True\n        self.log(f"❌ Failed to fill {label}.", "error", page)\n        return False\n\n''',
        flags=re.S,
    )
    replace_once(
        "bot_select.py",
        '            page.select_option(dropdown_id, value=target_value)\n',
        '            dropdown = BrowserActions.resolve_locator(page, dropdown_id)\n            if not BrowserActions.select_option(dropdown, value=target_value):\n                return "error"\n',
    )
    replace_once(
        "bot_select.py",
        '                    page.locator(branch_ddl).select_option(value=val)\n                    return True\n',
        '                    return BrowserActions.select_option(page.locator(branch_ddl).first, value=val)\n',
    )
    replace_once(
        "bot_select.py",
        '''            page.click("#ctl00_ContentPlaceHolder1_btnSave")\n            DBHandler.update_status(self.nid, "Submitted", "Final submit clicked")\n            self.log("✅ ثبت نهایی انجام شد.", "success", page)\n''',
        '''            if not self.settings.get("final_submit", False):\n                DBHandler.update_status(self.nid, "READY_FOR_FINAL_CONFIRMATION", "Awaiting operator confirmation")\n                self.log("🛑 بانک و شعبه آماده‌اند؛ ثبت نهایی برای تأیید اپراتور متوقف شد.", "warning", page)\n                EVENT_BROADCASTER.emit_event(\n                    build_event("human_required", nid=self.nid, reason="final_submit")\n                )\n                return\n            save_button = BrowserActions.resolve_locator(page, "#ctl00_ContentPlaceHolder1_btnSave")\n            if not BrowserActions.click_when_ready(save_button):\n                self.log("❌ کلیک ثبت نهایی ناموفق بود.", "error", page)\n                return\n            DBHandler.update_status(self.nid, "Submitted", "Final submit clicked")\n            self.log("✅ ثبت نهایی انجام شد.", "success", page)\n''',
    )
    regex_once(
        "bot_select.py",
        r"    def solve_captcha_step\(self, page, input_sel: str, btn_sel: str, source: str = \"general\"\) -> bool:\n.*?(?=    def _perform_login_standard)",
        '''    def solve_captcha_step(self, page, input_sel: str, btn_sel: str, source: str = "general") -> bool:\n        if not legacy_captcha_model_enabled():\n            return self._wait_for_manual_captcha(page, input_sel, btn_sel)\n        try:\n            page.wait_for_selector(".BDC_CaptchaImage", timeout=5000, state="visible")\n            captcha_img = page.locator(".BDC_CaptchaImage").first\n            if not captcha_img.is_visible():\n                return False\n            code = (self.captcha_service.solve(captcha_img.screenshot(), mode="general") or "").strip()\n            if len(code) < 4:\n                self.log("مدل کپچا پاسخ معتبر نداد؛ تصویر تازه درخواست می‌شود.", "warning", page)\n                self._refresh_captcha_or_reload(page)\n                return False\n            input_locator = BrowserActions.resolve_locator(page, input_sel)\n            button_locator = BrowserActions.resolve_locator(page, btn_sel)\n            if not BrowserActions.fill_text(input_locator, code):\n                return False\n            if not BrowserActions.click_when_ready(button_locator):\n                return False\n            DBHandler.append_captcha_attempt(self.nid, code, "submitted", source=source)\n            self.suspend_watchdog(20)\n            return True\n        except Exception:\n            self._refresh_captcha_or_reload(page)\n            return False\n\n    def _wait_for_manual_captcha(self, page, input_sel: str, btn_sel: str) -> bool:\n        input_locator = BrowserActions.resolve_locator(page, input_sel)\n        button_locator = BrowserActions.resolve_locator(page, btn_sel)\n        if input_locator is None or button_locator is None:\n            return False\n        timeout_seconds = max(15, min(int(self.settings.get("captcha_manual_timeout", 180)), 600))\n        self.suspend_watchdog(timeout_seconds + 10)\n        self.log("🧩 کپچا را در مرورگر وارد کنید؛ پس از ورود، ادامه خودکار انجام می‌شود.", "warning", page)\n        EVENT_BROADCASTER.emit_event(\n            build_event("human_required", nid=self.nid, reason="captcha_manual")\n        )\n        deadline = time.time() + timeout_seconds\n        while time.time() < deadline:\n            try:\n                code = input_locator.input_value().strip()\n            except Exception:\n                code = ""\n            if len(code) >= 4:\n                return BrowserActions.click_when_ready(button_locator)\n            time.sleep(0.25)\n        self.log("⌛ زمان ورود دستی کپچا پایان یافت.", "error", page)\n        return False\n\n''',
        flags=re.S,
    )
    replace_once(
        "bot_select.py",
        '                page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").fill(self.nid)\n',
        '                BrowserActions.fill_text(\n                    page.locator("#ctl00_ContentPlaceHolder1_tbIDNo").first,\n                    self.nid,\n                )\n',
    )


def main() -> None:
    patch_browser_launcher()
    patch_database()
    patch_captcha_service()
    patch_bot_core()
    patch_server()
    patch_bot_register()
    patch_bot_select()
    print("Phase 1-4 refactor applied successfully.")


if __name__ == "__main__":
    main()
