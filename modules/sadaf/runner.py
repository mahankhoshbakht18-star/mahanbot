# -*- coding: utf-8 -*-
"""One Sadaf run at a time, on its own Playwright driver and browser profile."""
import csv
import importlib
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import page_js, sms
from .customers import customer_for_run, now, request_pairs, runnable_customers
from .db import connect, ensure_schema
from .paths import BROWSER, CAPTCHA_TRAIN, DATA, REPORTS
from .settings import urls

_LOCK = threading.Lock()
_RUNNER: Optional["Runner"] = None
_OCR = None

SNAPSHOTS = DATA / "snapshots"
SMS_WAIT = 240
FINAL_ROUNDS = 12
SMS_EXPIRED = re.compile(r"منقضی|انقضا|اعتبار کد|(زمان|مهلت).{0,25}(تمام|پایان|گذشته)")
SMS_REJECTED = re.compile(r"کد\s*(تایید|پیامک|ارسال|ارسالی|دریافتی).{0,25}(اشتباه|نادرست|صحیح نیست|نامعتبر|معتبر نیست)")
SUCCESS_WORDS = ("با موفقیت", "ثبت گردید", "کد پیگیری", "کد رهگیری")
STAGE_ONE = {
    "بخش مصرف", "گروه کاربرد", "کاربرد", "کاربرد مورد نظر", "آمار تولید دوره کشت قبل",
    "نوع کشت", "سطح زیر کشت فعال", "شروع دوره کشت", "مدت زمان کشت",
}
SNAPSHOT_JS = r"""() => {
  const live = [...document.querySelectorAll('input,select,textarea')];
  const copy = document.documentElement.cloneNode(true);
  const twins = [...copy.querySelectorAll('input,select,textarea')];
  live.forEach((el, i) => {
    const twin = twins[i]; if (!twin) return;
    const secret = el.type === 'password' || el.hasAttribute('data-sadaf-sms') || /^__(VIEWSTATE|EVENTVALIDATION)/.test(el.id || el.name || '');
    if (secret) { twin.setAttribute('value', ''); return; }
    if (el.tagName === 'SELECT') twin.setAttribute('data-live-selected', (el.options[el.selectedIndex] || {}).text || '');
    else if (el.type === 'checkbox' || el.type === 'radio') twin.setAttribute('data-live-checked', el.checked ? '1' : '0');
    else twin.setAttribute('data-live-value', el.value || '');
  });
  copy.querySelectorAll('script').forEach((s) => { if (!s.src) s.textContent = ''; });
  return '<!-- ' + location.href + ' -->\n' + copy.outerHTML;
}"""
_JS = {"mtime": None, "code": ""}


def _helpers() -> str:
    """Page script, reloaded when page_js.py changes so selectors can be fixed during a run."""
    global page_js
    try:
        stamp = Path(page_js.__file__).stat().st_mtime
    except OSError:
        stamp = _JS["mtime"]
    if stamp != _JS["mtime"] or not _JS["code"]:
        if _JS["mtime"] is not None:
            page_js = importlib.reload(page_js)
        _JS["mtime"] = stamp
        _JS["code"] = page_js.HELPERS_JS.replace("__SADAF_VERSION__", str(stamp))
    return _JS["code"]


def _sadaf_engine():
    global _OCR
    if _OCR is None:
        import ddddocr
        # The plain model reads these Latin images as Chinese. The beta model reads the letters.
        _OCR = ddddocr.DdddOcr(show_ad=False, beta=True)
    return _OCR


def _sadaf_code(raw: bytes) -> str:
    """Read the original Lanap image. A sharpened copy is used only if the original is not 4 letters."""
    if not raw:
        return ""
    engine = _sadaf_engine()
    blobs = [raw]
    try:
        import io
        from PIL import Image
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        big = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        big.save(buffer, format="PNG")
        blobs.append(buffer.getvalue())
    except Exception:
        pass
    for blob in blobs:
        try:
            text = engine.classification(blob)
        except Exception:
            continue
        code = "".join(ch for ch in str(text or "") if ch.isalnum()).lower()
        if len(code) == 4:
            return code
    return ""


def get_runner() -> "Runner":
    global _RUNNER
    with _LOCK:
        if _RUNNER is None:
            _RUNNER = Runner()
        return _RUNNER


class StopRun(Exception):
    pass


class Runner:
    def __init__(self):
        self.thread: Optional[threading.Thread] = None
        self.cmds: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.captcha_ticket = 0
        self.state: Dict[str, Any] = {
            "active": False,
            "run_id": None,
            "status": "idle",
            "stage": "",
            "message": "عملیاتی در جریان نیست.",
            "browser": "closed",
            "uncertain": False,
            "current": None,
            "progress": 0,
        }

    def snapshot(self) -> Dict[str, Any]:
        with _LOCK:
            return dict(self.state)

    def start(self, start_row: int, force: bool, customer_id: Optional[int] = None) -> Dict[str, Any]:
        with _LOCK:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("یک عملیات سدف در حال اجراست.")
            if customer_id:
                person = customer_for_run(int(customer_id))
                if person.get("last_status") == "success" and not force:
                    raise RuntimeError("این مشتری قبلاً ثبت موفق دارد. برای اجرای دوباره تأیید لازم است.")
                people = [person]
                start_row = 1
            else:
                people = runnable_customers(force)
                if not people:
                    raise RuntimeError("مشتری فعالی برای اجرا نیست. ثبت‌شده‌ها را فقط با تأیید دوباره اجرا کنید.")
                start_row = max(1, min(int(start_row or 1), len(people)))
            ensure_schema()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report = REPORTS / f"نتیجه-{stamp}.csv"
            with connect() as conn:
                cur = conn.execute(
                    "INSERT INTO runs(started_at, status, report_path) VALUES(?, 'running', ?)",
                    (now(), str(report)),
                )
                run_id = int(cur.lastrowid)
            self.cmds = queue.Queue()
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.state = {
                "active": True,
                "run_id": run_id,
                "status": "running",
                "stage": "starting",
                "message": "در حال باز کردن مرورگر سدف…",
                "browser": "opening",
                "uncertain": False,
                "current": None,
                "progress": 0,
                "total": len(people),
                "done": 0,
            }
            self.thread = threading.Thread(
                target=self._run,
                args=(people, start_row - 1, run_id, report),
                name="sadaf-run",
                daemon=True,
            )
            self.thread.start()
            return dict(self.state)

    def open_site(self, customer_id: Optional[int] = None) -> Dict[str, Any]:
        """Open the live SADAF login page. A saved customer is filled and left at the captcha."""
        with _LOCK:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("مرورگر سدف همین حالا باز است.")
            people = [customer_for_run(int(customer_id))] if customer_id else []
            ensure_schema()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            report = REPORTS / f"نتیجه-{stamp}.csv"
            with connect() as conn:
                cur = conn.execute(
                    "INSERT INTO runs(started_at, status, report_path) VALUES(?, 'running', ?)",
                    (now(), str(report)),
                )
                run_id = int(cur.lastrowid)
            self.cmds = queue.Queue()
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.state = {
                "active": True,
                "run_id": run_id,
                "status": "running",
                "stage": "starting",
                "message": "در حال باز کردن صفحهٔ ورود سامانه سدف…",
                "browser": "opening",
                "uncertain": False,
                "current": None,
                "progress": 0,
                "total": len(people),
                "done": 0,
            }
            self.thread = threading.Thread(
                target=self._run,
                args=(people, 0, run_id, report),
                name="sadaf-open",
                daemon=True,
            )
            self.thread.start()
            return dict(self.state)

    def command(self, name: str) -> Dict[str, Any]:
        if name not in {"pause", "resume", "stop", "next", "accept_uncertain", "skip", "refill"}:
            raise ValueError("دستور ناشناخته است.")
        if not (self.thread and self.thread.is_alive()):
            raise RuntimeError("عملیاتی در جریان نیست.")
        if name == "pause":
            self.pause_event.set()
            self._say("توقف موقت. مرورگر سدف باز می‌ماند.", "paused")
        elif name == "resume":
            self.pause_event.clear()
            self._say("ادامهٔ عملیات.", "running")
        elif name == "stop":
            self.pause_event.clear()
            self.stop_event.set()
        self.cmds.put(name)
        return self.snapshot()

    def submit_code(self, code: str) -> Dict[str, Any]:
        text = "".join(ch for ch in str(code or "").lower() if ch.isalnum())
        if len(text) != 4:
            raise ValueError("کد امنیتی باید ۴ حرف یا رقم باشد.")
        if not (self.thread and self.thread.is_alive()):
            raise RuntimeError("اول صفحهٔ ورود را باز کنید، بعد کد را بفرستید.")
        with _LOCK:
            ticket = self.captcha_ticket
        self.cmds.put(("code", text, ticket))
        self._say("کد دستی گرفته شد. رمز پر می‌شود و دکمهٔ ورود زده می‌شود.", "running", stage="logging_in")
        return self.snapshot()

    def submit_sms(self, text: str) -> str:
        """Operator paste of the 7-digit code or a whole NIOPDC SMS. The code itself is never logged."""
        kind = sms.manual(text)
        if kind == "secret":
            raise ValueError("این پیامک رمز عبور یا کارت بانکی است. ذخیره نشد.")
        if not kind:
            raise ValueError("این متن کد ۷ رقمی یا پیامک شناخته‌شدهٔ NIOPDC نیست.")
        if kind == "stale":
            raise ValueError("این کد قدیمی است.")
        return kind

    def _say(self, message: str, status: Optional[str] = None, **extra) -> None:
        with _LOCK:
            self.state["message"] = message
            if status:
                self.state["status"] = status
            self.state.update(extra)
        run_id = self.state.get("run_id")
        customer = self.state.get("current") or {}
        ensure_schema()
        with connect() as conn:
            conn.execute(
                "INSERT INTO events(created_at, level, message, customer_id, run_id) VALUES(?, 'info', ?, ?, ?)",
                (now(), message[:500], customer.get("id"), run_id),
            )

    def _run(self, people: List[dict], start_index: int, run_id: int, report) -> None:
        from playwright.sync_api import Error, sync_playwright

        target = urls()
        channel = "msedge" if target["browser"] == "edge" else "chrome"
        done = 0
        finished = False
        try:
            with sync_playwright() as playwright:
                context = self._browser(playwright, channel)
                page = context.pages[0] if context.pages else context.new_page()
                self._last_dialog = ""

                def _take_dialog(dialog):
                    self._last_dialog = dialog.message or ""
                    dialog.accept()

                page.on("dialog", _take_dialog)
                self._say("مرورگر سدف باز شد.", "running", browser="open")
                page.goto(target["login"], wait_until="load")
                if not people:
                    self._say(
                        "صفحهٔ ورود سامانه سدف باز شد. مشتری ذخیره‌شده‌ای نیست، برای همین نام کاربری خودکار پر نمی‌شود. کد امنیتی با شماست.",
                        "needs_operator",
                        stage="wait_captcha",
                        browser="open",
                    )
                    while not self.stop_event.is_set():
                        self._poll(0.5)
                    self._say("مرورگر سدف بسته شد.", "done", browser="closed")
                    self._checkpoint(run_id, "done")
                    return
                for index in range(start_index, len(people)):
                    self._checkpoint(run_id, "running")
                    person = people[index]
                    self._hold()
                    outcome = self._one(page, context, person, index, len(people), target)
                    done += 1
                    self._finish_item(run_id, person, outcome, report, index + 1)
                    with _LOCK:
                        self.state["done"] = done
                        self.state["progress"] = round(done / len(people) * 100)
                self._say("همهٔ مشتری‌های این دور تمام شدند. برای بستن مرورگر، توقف کامل را بزنید.", "done", browser="open", current=None)
                self._checkpoint(run_id, "done")
                finished = True
                while not self.stop_event.is_set():
                    self._poll(1)
        except StopRun:
            if finished:
                self._say("مرورگر سدف بسته شد.", "done", browser="closed")
                self._checkpoint(run_id, "done")
            else:
                self._say("عملیات متوقف شد.", "stopped", browser="closed")
                self._checkpoint(run_id, "stopped")
        except Error as exc:
            self._say(self._safe_error(exc), "failed", browser="closed")
            self._checkpoint(run_id, "failed")
        except Exception as exc:
            self._say(self._safe_error(exc), "failed", browser="closed")
            self._checkpoint(run_id, "failed")
        finally:
            with _LOCK:
                self.state["active"] = False
                if self.state.get("browser") != "closed":
                    self.state["browser"] = "closed"

    def _browser(self, playwright, channel: str):
        args = [
            "--window-position=24,360",
            "--window-size=1180,680",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
        ]
        try:
            return playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER),
                channel=channel,
                headless=False,
                locale="fa-IR",
                no_viewport=True,
                args=args,
            )
        except Exception:
            other = "msedge" if channel == "chrome" else "chrome"
            return playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER),
                channel=other,
                headless=False,
                locale="fa-IR",
                no_viewport=True,
                args=args,
            )

    def _one(self, page, context, person, index, total, target) -> Dict[str, str]:
        try:
            person = customer_for_run(int(person["id"]))
        except ValueError as exc:
            self._say(str(exc), "failed", stage="load")
            return {"status": "failed", "stage": "load", "attempts": 1, "error": str(exc)}
        label = person.get("full_name") or person["username"]
        with _LOCK:
            self.state["current"] = {
                "id": person["id"],
                "username": person["username"],
                "name": person.get("full_name") or "",
                "index": index + 1,
                "total": total,
            }
            self.state["uncertain"] = False
        attempts = 1
        if not self._login(page, context, person, label, target["login"]):
            return {"status": "skipped", "stage": "login", "attempts": attempts, "error": "اپراتور این مشتری را رد کرد."}
        while True:
            self._hold()
            result = self._fill(page, person, label, target["form"], fresh=attempts > 1)
            if result["status"] == "success":
                self._say(f"{label}: ثبت شد. {result['message']}", "running", stage="done")
                return {"status": "success", "stage": "confirm", "attempts": attempts, "error": result["message"]}
            if result["status"] == "needs_review":
                with _LOCK:
                    self.state["uncertain"] = True
                self._say(f"{label}: نیازمند بررسی. {result['message']}", "needs_operator", stage="needs_review")
                return {"status": "needs_review", "stage": "confirm", "attempts": attempts, "error": result["message"]}
            error = result["message"]
            self._say(f"{label}: {error}", "needs_operator", stage="needs_operator")
            decision = self._wait_decision()
            if decision == "refill":
                attempts += 1
                continue
            if decision == "skip":
                return {"status": "skipped", "stage": "form", "attempts": attempts, "error": error}
            # «ثبت را تأیید می‌کنم» without a success sentence on the page is still only a review case.
            return {"status": "needs_review", "stage": "confirm", "attempts": attempts,
                    "error": "اپراتور ادامه داد؛ صفحه نشانهٔ ثبت نشان نداد. " + error}

    def _login(self, page, context, person, label, login_url) -> bool:
        self._say(f"{label}: در حال باز کردن صفحهٔ ورود.", "running", stage="logging_in")
        context.clear_cookies()
        page.goto(login_url, wait_until="load")
        filled = False
        for _ in range(25):
            cmd = self._poll(0.2)
            if cmd == "skip":
                return False
            if cmd == "stop":
                raise StopRun()
            result = self._js(page, "a => window.__sadaf.fillLogin(a[0], a[1], false)", [person["username"], person["password"]])
            if result in {"ok", "kept"}:
                filled = True
                break
        if not filled:
            self._say(f"{label}: صفحهٔ ورود آماده نشد. کد و دکمهٔ ورود با شماست.", "needs_operator", stage="wait_captcha")
        else:
            outcome = self._attempt_login(page, person, label)
            if outcome == "skip":
                return False
            if outcome == "ok":
                return True
            if outcome != "wait":
                self._say(f"{label}: ورود خودکار پذیرفته نشد. کد را در بات بنویسید و «ورود با این کد» را بزنید.", "needs_operator", stage="wait_captcha")
        while True:
            cmd = self._poll(0.1)
            if cmd == "skip":
                return False
            if cmd == "stop":
                raise StopRun()
            if isinstance(cmd, tuple) and cmd[0] == "code":
                if not self._code_current(cmd):
                    self._say(f"{label}: این کد برای تصویر قبلی بود. کد تصویر فعلی را بنویسید.", "needs_operator", stage="wait_captcha")
                    continue
                if self._apply_manual(page, person, label, cmd[1]) == "ok":
                    return True
                continue
            on_login = self._js(page, "() => window.__sadaf.isLoginPage()")
            if on_login is None:
                continue
            if not on_login:
                self._say(f"{label}: ورود انجام شد.", "running", stage="logged_in")
                return True

    def _bump_captcha(self) -> None:
        with _LOCK:
            self.captcha_ticket += 1

    def _code_current(self, cmd) -> bool:
        ticket = cmd[2] if len(cmd) > 2 else self.captcha_ticket
        return ticket == self.captcha_ticket

    def _queued_code(self):
        chosen = None
        stale = False
        while True:
            cmd = self._poll(0)
            if cmd is None:
                break
            if cmd == "skip":
                return "skip"
            if isinstance(cmd, tuple) and cmd[0] == "code":
                if self._code_current(cmd):
                    chosen = cmd
                    stale = False
                elif chosen is None:
                    stale = True
        if chosen:
            return chosen
        if stale:
            return "stale"
        return None

    def _attempt_login(self, page, person, label: str) -> str:
        clicks = 0
        reads = 0
        fresh = True
        while clicks < 4 and reads < 8:
            pending = self._queued_code()
            if pending == "skip":
                return "skip"
            if pending == "stale":
                self._say(f"{label}: این کد برای تصویر قبلی بود. کد تصویر فعلی را بنویسید.", "needs_operator", stage="wait_captcha")
            elif isinstance(pending, tuple):
                outcome = self._apply_manual(page, person, label, pending[1])
                if outcome == "ok":
                    return "ok"
                if outcome == "stop":
                    return "failed"
                fresh = True
                continue
            reads += 1
            if not fresh:
                self._refresh_captcha(page)
            fresh = False
            if not self._fill_credentials(page, person):
                self._say(f"{label}: کادر رمز خالی ماند. ورود زده نشد.", "needs_operator", stage="wait_captcha")
                return "failed"
            if not self._type_captcha(page):
                self._say(f"{label}: تصویر کپچا مبهم بود. بدون زدن ورود، تصویر تازه گرفته می‌شود.", "needs_operator", stage="wait_captcha")
                continue
            if not self._password_ready(page, person):
                if not self._fill_credentials(page, person):
                    self._say(f"{label}: کادر رمز خالی ماند. ورود زده نشد.", "needs_operator", stage="wait_captcha")
                    return "failed"
            clicks += 1
            self._say(f"{label}: کد پر شد. دکمهٔ ورود زده می‌شود. تلاش {clicks} از ۴.", "running", stage="logging_in")
            if self._submit_login(page) == "left" or self._js(page, "() => window.__sadaf.isLoginPage()") is False:
                self._say(f"{label}: ورود انجام شد.", "running", stage="logged_in")
                return "ok"
            problem = self._js(page, "() => window.__sadaf.loginProblem()")
            if problem == "credentials":
                detail = self._js(page, "() => window.__sadaf.loginDetail()") or "کلمه عبور یا نام کاربری نامعتبر است"
                self._say(f"{label}: سایت نام کاربری یا رمز را نپذیرفت. {detail}", "needs_operator", stage="wait_captcha")
                return "failed"
            fresh = True
            self._say(f"{label}: کد پذیرفته نشد. همان لحظه تصویر تازه خوانده می‌شود.", "running", stage="logging_in")
        return "failed"

    def _fill_credentials(self, page, person) -> bool:
        try:
            user = page.locator("#userName")
            password = page.locator("#Password")
            if user.count() == 0 or password.count() == 0:
                return False
            if user.input_value() != person["username"]:
                user.fill(person["username"], timeout=5000)
            if password.input_value() != person["password"]:
                password.fill(person["password"], timeout=5000)
            return self._password_ready(page, person) and user.input_value() == person["username"]
        except Exception:
            return False

    def _password_ready(self, page, person) -> bool:
        try:
            return page.locator("#Password").input_value() == person["password"]
        except Exception:
            return False

    def _apply_manual(self, page, person, label: str, code: str) -> str:
        image = self._captcha_image(page)
        if not self._fill_credentials(page, person):
            self._say(f"{label}: کادر رمز خالی ماند. ورود زده نشد.", "needs_operator", stage="wait_captcha")
            return "stop"
        if not self._type_given(page, code):
            self._say(f"{label}: کد در کادر ننشست. دوباره بنویسید.", "needs_operator", stage="wait_captcha")
            return "stop"
        self._say(f"{label}: کد دستی پر شد. دکمهٔ ورود زده می‌شود.", "running", stage="logging_in")
        landed = self._submit_login(page)
        if landed == "left" or self._js(page, "() => window.__sadaf.isLoginPage()") is False:
            self._keep_sample(image, code)
            self._say(f"{label}: ورود انجام شد.", "running", stage="logged_in")
            return "ok"
        problem = self._js(page, "() => window.__sadaf.loginProblem()")
        if problem == "credentials":
            detail = self._js(page, "() => window.__sadaf.loginDetail()") or "کلمه عبور یا نام کاربری نامعتبر است"
            self._say(f"{label}: سایت نام کاربری یا رمز را نپذیرفت. {detail}", "needs_operator", stage="wait_captcha")
            return "stop"
        self._say(f"{label}: این کد پذیرفته نشد. کد تصویر تازه را بنویسید.", "needs_operator", stage="wait_captcha")
        return "captcha"

    def _keep_sample(self, raw: bytes, code: str) -> None:
        if not raw or len(code) != 4:
            return
        try:
            CAPTCHA_TRAIN.mkdir(parents=True, exist_ok=True)
            suffix = ".jpg" if raw[:2] == b"\xff\xd8" else ".png"
            stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            (CAPTCHA_TRAIN / f"{code}_{stamp}{suffix}").write_bytes(raw)
        except Exception:
            pass

    def _type_captcha(self, page) -> bool:
        return self._type_given(page, _sadaf_code(self._captcha_image(page)))

    def _type_given(self, page, code: str) -> bool:
        if len(code) != 4:
            return False
        try:
            box = page.locator("#txtCode")
            if box.count() == 0:
                return False
            box.click(timeout=2000)
            box.fill(code, timeout=2000)
            return (box.input_value() or "").strip().lower() == code
        except Exception:
            return False

    def _refresh_captcha(self, page) -> None:
        try:
            previous = page.locator("img[id*='CaptchaImage' i]").first.get_attribute("src") or ""
        except Exception:
            previous = ""
        self._js(page, "() => window.__sadaf.freshCaptcha()")
        for _ in range(40):
            page.wait_for_timeout(50)
            try:
                current = page.locator("img[id*='CaptchaImage' i]").first.get_attribute("src") or ""
            except Exception:
                current = ""
            if current and current != previous:
                self._bump_captcha()
                return

    def _submit_login(self, page) -> str:
        """Click ورود and return as soon as the postback finishes.

        A wrong captcha stays on this page. Waiting for a full navigation
        held the next try for about twenty seconds and made a hand-typed
        code land on the next image.
        """
        button = page.locator("#LoginButton")
        if button.count() == 0:
            return "unknown"
        before = self._captcha_src(page)
        try:
            button.click(timeout=3000)
        except Exception:
            return "unknown"
        saw_busy = False
        for step in range(25):
            page.wait_for_timeout(80)
            on_login = self._js(page, "() => window.__sadaf.isLoginPage()")
            if on_login is None:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                on_login = self._js(page, "() => window.__sadaf.isLoginPage()")
                return "left" if on_login is False else "unknown"
            if on_login is False:
                return "left"
            busy = self._js(page, "() => window.__sadaf.busy()")
            src = self._captcha_src(page)
            if busy:
                saw_busy = True
            elif saw_busy or (step >= 4 and src and src != before):
                self._bump_captcha()
                return "stay"
        self._bump_captcha()
        return "stay"

    def _captcha_src(self, page) -> str:
        try:
            image = page.locator("#login_captcha1_CaptchaImage")
            if image.count() == 0:
                image = page.locator("img[id*='CaptchaImage' i]")
            if image.count() == 0:
                return ""
            return image.first.get_attribute("src") or ""
        except Exception:
            return ""

    def _captcha_image(self, page) -> bytes:
        try:
            images = page.locator("#login_captcha1_CaptchaImage")
            if images.count() == 0:
                images = page.locator("img[id*='CaptchaImage' i]")
            if images.count() == 0:
                return b""
            src = images.first.get_attribute("src") or ""
            if src:
                from urllib.parse import urljoin
                response = page.request.get(urljoin(page.url, src), timeout=15000)
                if response.ok and response.body():
                    return response.body()
            return images.first.screenshot(type="png")
        except Exception:
            return b""

    # ------------------------------------------------------------------ form
    def _fill(self, page, person, label, form_url, fresh: bool = False) -> Dict[str, str]:
        """Fill and submit the request form.

        Returns {"status": "success" | "needs_review" | "error", "message": ...}.
        success is returned only when the page itself (or the «ثبت درخواست» SMS) says so.
        """
        if fresh or "frmrequest.aspx" not in page.url.lower():
            page.goto(form_url, wait_until="load")
        self._settle(page)
        try:
            person = customer_for_run(int(person["id"]))
        except ValueError as exc:
            return self._error(str(exc))
        if self._js(page, "u => window.__sadaf.pageHasUser(u)", person["username"]) is False:
            return self._error(f"کاربر واردشده با {person['username']} یکی نیست.")
        pairs = request_pairs(person)
        if not pairs:
            return self._error("برای این مشتری هیچ فیلد درخواستی ذخیره نشده است.")
        self._snap(page, "form")
        self._say(f"{label}: در حال پر کردن مرحلهٔ ۱.", "running", stage="filling")
        self._absent_fields = set()
        first = [pair for pair in pairs if pair[0] in STAGE_ONE]
        error, _missing = self._choose_and_type(page, first)
        if error:
            return self._error(error)
        wrong = self._js(page, "p => window.__sadaf.wrongFields(p)", first) or []
        if wrong:
            self._snap(page, "step1-wrong")
            return self._error("مرحلهٔ ۱ درست ننشست: " + "، ".join(wrong))
        if not self._js(page, "() => window.__sadaf.markNext()"):
            self._snap(page, "no-next")
            return self._error("دکمهٔ «ادامه» زیر مدت کشت پیدا نشد.")
        self._say(f"{label}: مرحلهٔ ۱ پر شد. دکمهٔ «ادامه» زده می‌شود.", "running", stage="filling")
        if not self._click(page, "next"):
            return self._error("دکمهٔ «ادامه» زده نشد.")
        self._snap(page, "after-next")
        if self._js(page, "() => window.__sadaf.isLoginPage()"):
            return self._error("بعد از ادامه، سایت به صفحهٔ ورود برگشت. دوباره وارد شوید.")

        error = self._choose_seller(page, pairs, label)
        if error:
            return self._error(error)
        error = self._refill_after_postback(page, pairs, label)
        if error:
            return self._error(error)

        state = self._js(page, "() => window.__sadaf.markTerms()") or ""
        if not state:
            self._snap(page, "no-terms")
            return self._error("تیک تعهد ماده ۴۴ پیدا نشد.")
        if state != "checked":
            if not self._click(page, "terms"):
                return self._error("تیک تعهد زده نشد.")
            if not self._js(page, "() => window.__sadaf.termsChecked()"):
                self._js(page, "() => window.__sadaf.markTerms()")
                if not self._js(page, "() => window.__sadaf.termsChecked()"):
                    return self._error("تیک تعهد زده شد ولی تیک‌خورده نماند.")
        self._say(f"{label}: تیک تعهد ماده ۴۴ زده شد.", "running", stage="filling")
        # The checkbox may post back; make sure nothing was cleared.
        error = self._refill_after_postback(page, pairs, label, quiet=True)
        if error:
            return self._error(error)
        self._snap(page, "ready")
        return self._finish_request(page, person, label, pairs)

    def _error(self, message: str) -> Dict[str, str]:
        return {"status": "error", "message": message}

    def _refill_after_postback(self, page, pairs, label: str, quiet: bool = False) -> str:
        second = [pair for pair in pairs if pair[0] not in STAGE_ONE]
        wrong: List[str] = []
        for attempt in range(3):
            error, missing = self._choose_and_type(page, second)
            if error:
                return error
            wrong = self._js(page, "p => window.__sadaf.wrongFields(p)", second) or []
            if not wrong:
                if missing and not quiet:
                    self._say(f"{label}: این خانه‌ها در صفحه پیدا نشدند: " + "، ".join(missing), "running", stage="filling")
                break
            self._settle(page)
        if wrong:
            self._snap(page, "step2-wrong")
            return "بعد از پست‌بک این خانه‌ها درست ننشستند: " + "، ".join(wrong)
        if not quiet:
            self._say(f"{label}: نوع مصرف، تاریخ‌ها و مقدار نفتگاز بعد از پست‌بک دوباره نوشته شدند.", "running", stage="filling")
        name = next((value for key, value in pairs if key == "نام فروشندگی" and str(value).strip()), "")
        if name:
            shown = self._js(page, "() => window.__sadaf.chosenSeller()") or ""
            if not self._same_name(shown, name):
                self._snap(page, "seller-lost")
                return f"فروشندگی روی فرم «{shown or 'خالی'}» است، نه «{name}»."
        return ""

    def _same_name(self, left: str, right: str) -> bool:
        def fold(value: str) -> str:
            text = str(value or "").replace("ي", "ی").replace("ك", "ک").replace("ئ", "ی").replace("ـ", "")
            return "".join(text.split())
        return bool(fold(left)) and fold(left) == fold(right)

    def _choose_and_type(self, page, pairs):
        empty_waits = 0
        for _ in range(30):
            result = self._js(page, "p => window.__sadaf.selectStep(p)", pairs)
            if result is None:
                self._settle(page)
                continue
            if result.get("error"):
                if result.get("empty"):
                    empty_waits += 1
                    if empty_waits >= 8:
                        return f"لیست «{result['error']}» هنوز گزینه‌ای ندارد.", []
                    self._settle(page)
                    continue
                options = "، ".join(result.get("options") or [])
                return f"گزینهٔ فیلد «{result['error']}» پیدا نشد. گزینه‌های موجود: {options}", []
            empty_waits = 0
            if result.get("done"):
                break
            self._settle(page)
        else:
            return "انتخاب لیست‌ها تمام نشد.", []
        missing = []
        absent = getattr(self, "_absent_fields", set())
        for attempt in range(8):
            result = self._js(page, "p => window.__sadaf.fillTexts(p)", pairs)
            if result is None:
                self._settle(page)
                continue
            missing = result.get("missing") or []
            # A field this form never shows (e.g. «نوع مصرف سوخت») is not waited for again.
            if not missing or set(missing) <= absent or attempt >= 1:
                break
            self._settle(page)
        self._absent_fields = absent | set(missing)
        return "", missing

    def _choose_seller(self, page, pairs, label: str) -> str:
        name = next((value for key, value in pairs if key == "نام فروشندگی" and str(value).strip()), "")
        if not name:
            return ""
        opened = False
        for _ in range(6):
            state = self._js(page, "() => window.__sadaf.markOpenSellers()") or ""
            if state == "open":
                opened = True
                break
            if state == "marked":
                self._click(page, "sellers")
                continue
            self._settle(page)
        if not opened:
            self._snap(page, "no-seller-dialog")
            return "پنجرهٔ «انتخاب فروشندگی» باز نشد."
        self._snap(page, "seller-dialog")
        self._keep_seller_names(page)
        self._say(f"{label}: پنجرهٔ انتخاب فروشندگی باز شد. «{name}» جستجو می‌شود.", "running", stage="filling")
        search = self._js(page, "n => window.__sadaf.searchSeller(n)", name) or ""
        if not search:
            return "جعبهٔ نام فروشندگی در پنجره پیدا نشد."
        if search == "marked":
            self._click(page, "search")
        else:
            self._press(page, "sellerbox", "Enter")
        result = None
        for _ in range(12):
            result = self._js(page, "n => window.__sadaf.chooseSeller(n)", name)
            if result and not result.get("waiting") and (result.get("picked") or result.get("names")):
                break
            self._settle(page)
        self._snap(page, "seller-search")
        if not result or not result.get("picked"):
            names = "، ".join((result or {}).get("names") or [])
            if result and result.get("many"):
                return f"چند ردیف با «{name}» پیدا شد: {names}"
            return f"ردیف «{name}» در نتیجهٔ جستجو نبود. ردیف‌ها: {names or 'هیچ'}"
        picked = result["picked"]
        if not self._same_name(picked, name):
            return f"ردیف پیدا‌شده «{picked}» است، نه «{name}»."
        self._say(f"{label}: ردیف «{picked}» پیدا شد. «انتخاب» همان ردیف زده می‌شود.", "running", stage="filling")
        if not self._click(page, "pick"):
            return "دکمهٔ «انتخاب» ردیف زده نشد."
        self._snap(page, "seller-picked")
        chosen_row = self._js(page, "() => window.__sadaf.sellerHighlighted()") or ""
        if chosen_row and not self._same_name(chosen_row, name):
            # The site answers «انتخاب» by row number in its full, unfiltered list, so a row picked
            # from a search result selects whatever seller sits at that position on page 1.
            self._say(
                f"{label}: سایت ردیف «{chosen_row}» را انتخاب کرد، نه «{name}». ردیف در صفحه‌های فهرست پیدا می‌شود.",
                "running", stage="filling",
            )
            note = self._seller_by_pages(page, name, label)
            if note:
                return note
            self._snap(page, "seller-picked-paged")
        confirmed = False
        for _ in range(5):
            if not self._js(page, "() => window.__sadaf.sellerOpen()"):
                break
            if self._js(page, "() => window.__sadaf.markConfirmSeller()"):
                self._say(f"{label}: دکمهٔ «تایید» کنار «انصراف» زده می‌شود.", "running", stage="filling")
                confirmed = self._click(page, "sellerok") or confirmed
            else:
                self._settle(page)
        self._snap(page, "seller-confirmed")
        if self._js(page, "() => window.__sadaf.sellerOpen()"):
            return "دکمهٔ «تایید» پنجرهٔ فروشندگی زده شد ولی پنجره بسته نشد." if confirmed else "دکمهٔ «تایید» پنجرهٔ فروشندگی پیدا نشد."
        shown = self._js(page, "() => window.__sadaf.chosenSeller()") or ""
        if not self._same_name(shown, picked):
            return f"بعد از تایید، فرم «{shown or 'خالی'}» را نشان می‌دهد، نه «{picked}»."
        self._say(f"{label}: روی فرم نوشته شده «{shown}».", "running", stage="filling")
        return ""

    def _seller_by_pages(self, page, name: str, label: str) -> str:
        """Walk the unfiltered seller list page by page and press «انتخاب» on the row's own page."""
        # Paging must show the full list; a leftover search text could filter it on some postbacks.
        self._js(page, "() => window.__sadaf.clearSellerSearch()")
        seen = set()
        for _ in range(60):
            current = self._js(page, "() => window.__sadaf.sellerCurrentPage()") or 1
            seen.add(current)
            result = self._js(page, "n => window.__sadaf.chooseSeller(n)", name) or {}
            if result.get("waiting"):
                self._settle(page)
                continue
            if result.get("many"):
                return f"چند ردیف با «{name}» در صفحهٔ {current} فهرست هست: " + "، ".join(result.get("names") or [])
            picked = result.get("picked")
            if picked:
                if not self._same_name(picked, name):
                    return f"ردیف پیدا‌شده «{picked}» است، نه «{name}»."
                self._say(f"{label}: «{picked}» در صفحهٔ {current} فهرست پیدا شد. «انتخاب» زده می‌شود.", "running", stage="filling")
                if not self._click(page, "pick"):
                    return "دکمهٔ «انتخاب» ردیف زده نشد."
                chosen_row = self._js(page, "() => window.__sadaf.sellerHighlighted()") or ""
                if chosen_row and not self._same_name(chosen_row, name):
                    self._snap(page, "seller-wrong-page-pick")
                    return f"در صفحهٔ {current} هم سایت ردیف «{chosen_row}» را انتخاب کرد، نه «{name}»."
                return ""
            targets = self._js(page, "() => window.__sadaf.sellerPageTargets()") or []
            following = [n for n in targets if n not in seen]
            if not following:
                self._snap(page, "seller-not-in-pages")
                return f"ردیف «{name}» در هیچ صفحهٔ فهرست فروشندگی ({len(seen)} صفحه) نبود."
            target = min((n for n in following if n > current), default=min(following))
            seen.add(target)
            if not self._js(page, "n => window.__sadaf.markSellerPage(n)", target) or not self._click(page, "sellerpage"):
                return f"صفحهٔ {target} فهرست فروشندگی باز نشد."
        return f"پیدا کردن «{name}» در فهرست فروشندگی تمام نشد."

    def _keep_seller_names(self, page) -> None:
        """Write the seller names the site lists, so the catalog can be checked against the real text."""
        try:
            names = self._js(page, "() => (window.__sadaf.sellerRows() || []).map((row) => row.label)") or []
            if names:
                (DATA / "seller-names.txt").write_text(chr(10).join(names), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------- final step
    def _finish_request(self, page, person, label: str, pairs) -> Dict[str, str]:
        """«ثبت مشخصات» is pressed at least twice.

        The first press (with the captcha) sends the SMS code and opens the «کد پيامك شده» box.
        The next press (code + fresh captcha) registers the request. After any error the code and a
        fresh captcha go in again and the button is pressed again, until the request is registered.
        An expired or rejected code is renewed with «ارسال مجدد پيامك».
        """
        code_text = ""
        sent_at = 0.0
        said: List[str] = []
        pressed_with_code = False
        for _round in range(FINAL_ROUNDS):
            self._hold()
            panel = self._js(page, "() => window.__sadaf.smsPanel()") or {}
            had_panel = bool(panel.get("open"))
            if had_panel and not code_text:
                code_text = self._wait_sms(page, label, sent_at or time.time() - 60)
                if not code_text:
                    return self._final_note(pressed_with_code, "کد ۷ رقمی پیامک نرسید. «ثبت مشخصات» دوباره زده نشد.")
            # Every postback may clear fields and always changes the captcha image.
            error = self._refill_after_postback(page, pairs, label, quiet=True)
            if error:
                return self._final_note(pressed_with_code, error)
            if not self._js(page, "() => window.__sadaf.termsChecked()"):
                self._js(page, "() => window.__sadaf.markTerms()")
                self._click(page, "terms")
            if had_panel:
                if not self._js(page, "c => window.__sadaf.fillSms(c)", code_text):
                    self._snap(page, "no-sms-box")
                    return self._final_note(pressed_with_code, "کادر «کد پیامک شده» پیدا نشد.")
                self._say(f"{label}: کد پیامک در فرم نوشته شد.", "running", stage="filling")
            note = self._fill_form_captcha(page)
            if note:
                return self._final_note(pressed_with_code, note)
            self._say(f"{label}: کپچای صفحهٔ آخر پر شد.", "running", stage="filling")
            if not self._js(page, "() => window.__sadaf.markConfirmRequest()"):
                self._snap(page, "no-final")
                return self._final_note(pressed_with_code, "دکمهٔ «ثبت مشخصات» پیدا نشد.")
            self._snap(page, "before-final")
            self._js(page, "l => window.__sadaf.clearMessages(l)", said)
            before = self._lines(page)
            if not had_panel:
                sent_at = time.time()
            submitted = now()
            sms.note_submit(person)
            if had_panel:
                self._say(f"{label}: «ثبت مشخصات» با کد پیامک زده می‌شود.", "running", stage="confirming")
            else:
                self._say(f"{label}: «ثبت مشخصات» زده می‌شود تا کد پیامک ارسال شود.", "running", stage="confirming")
            self._click(page, "final")
            pressed_with_code = pressed_with_code or had_panel
            said = self._page_says(page, label, before, code_text, wait=8)
            self._snap(page, "after-final")
            joined = sms.normalize(" ".join(said))
            if any(word in joined for word in SUCCESS_WORDS):
                self._keep_tracking(person, said)
                return {"status": "success", "message": "صفحه: " + self._quote(said)}
            if self._captcha_wrong(said):
                self._say(f"{label}: کپچا اشتباه بود. کپچای تازه حل می‌شود.", "running", stage="confirming")
                continue
            if had_panel and (SMS_EXPIRED.search(joined) or SMS_REJECTED.search(joined)):
                why = "منقضی شد" if SMS_EXPIRED.search(joined) else "پذیرفته نشد"
                self._say(f"{label}: کد پیامک {why}. «ارسال مجدد پیامک» زده می‌شود.", "running", stage="confirming")
                note, sent_at = self._resend_sms(page, label)
                if note:
                    return self._final_note(pressed_with_code, note)
                code_text = ""
                continue
            now_panel = bool((self._js(page, "() => window.__sadaf.smsPanel()") or {}).get("open"))
            if not had_panel:
                if now_panel:
                    self._say(f"{label}: کد پیامک ارسال شد.", "running", stage="confirming")
                elif said:
                    self._say(f"{label}: کد پیامک هنوز ارسال نشده. کپچای تازه و «ثبت مشخصات» دوباره.", "running", stage="confirming")
                continue
            if said:
                self._say(f"{label}: درخواست ثبت نشد. کد پیامک و کپچای تازه دوباره زده می‌شود.", "running", stage="confirming")
                continue
            # Pressed with the code and the page said nothing: wait for proof instead of pressing again.
            return self._await_evidence(page, person, label, before, submitted, said)
        return self._final_note(pressed_with_code, f"بعد از {FINAL_ROUNDS} بار «ثبت مشخصات»، درخواست ثبت نشد.")

    def _final_note(self, pressed_with_code: bool, note: str) -> Dict[str, str]:
        """Once a press carried the SMS code the request may already exist, so a person must look."""
        return {"status": "needs_review", "message": note} if pressed_with_code else self._error(note)

    def _resend_sms(self, page, label: str):
        if not self._js(page, "() => window.__sadaf.markResendSms()"):
            self._snap(page, "no-resend")
            return "لینک «ارسال مجدد پیامک» پیدا نشد.", 0.0
        before = self._lines(page)
        sent_at = time.time()
        if not self._click(page, "resend"):
            return "لینک «ارسال مجدد پیامک» زده نشد.", sent_at
        self._page_says(page, label, before, wait=4)
        return "", sent_at

    def _await_evidence(self, page, person, label, before, submitted, said) -> Dict[str, str]:
        self._say(f"{label}: صفحه هنوز پیام ثبت نداده. تا ۲ دقیقه منتظر صفحه و پیامک «ثبت درخواست» می‌مانم.", "running", stage="confirming")
        seen = list(said)
        deadline = time.time() + 120
        while time.time() < deadline:
            self._hold()
            page.wait_for_timeout(1500)
            fresh = self._new_lines(page, before)
            for line in fresh:
                if line not in seen:
                    seen.append(line)
            if any(word in " ".join(seen) for word in SUCCESS_WORDS):
                self._keep_tracking(person, seen)
                return {"status": "success", "message": "صفحه: " + self._quote(seen)}
            codes = sms.tracking_for(int(person["id"]), submitted)
            if codes:
                return {"status": "success", "message": f"پیامک «ثبت درخواست» با کد رهگیری {codes[0]} رسید."}
        text = self._quote(seen) if seen else "هیچ پیام تازه‌ای"
        return {"status": "needs_review", "message": f"بعد از تایید نهایی، صفحه نشانهٔ ثبت نشان نداد ({text})."}

    def _keep_tracking(self, person, lines: List[str]) -> None:
        for line in lines:
            match = re.search(r"کد\s*(?:پیگیری|رهگیری)\D{0,20}?(\d{5,12})", sms.normalize(line))
            if match:
                amount = next((value for key, value in request_pairs(person) if key.startswith("نفتگاز|") and "یارانه" in key and "غیر" not in key), "")
                sms.record_page_tracking(match.group(1), person, "نفتگاز", amount)
                self._say(f"کد رهگیری {match.group(1)} از صفحه در فایل جهاد ذخیره شد.", "running")
                return

    def _captcha_wrong(self, said: List[str]) -> bool:
        joined = sms.normalize(" ".join(said))
        return bool(re.search(r"(کد\s*(امنیتی|مقابل|تصویر)|کپچا).{0,30}(اشتباه|نادرست|صحیح نیست|نامعتبر)|(اشتباه|نادرست).{0,20}کد\s*(امنیتی|مقابل|تصویر)", joined))

    def _quote(self, lines: List[str]) -> str:
        return "«" + " / ".join(lines[:4])[:300] + "»"

    def _lines(self, page) -> List[str]:
        return self._js(page, "() => window.__sadaf.lines()") or []

    def _new_lines(self, page, before: List[str]) -> List[str]:
        old = set(before or [])
        fresh = [line for line in self._lines(page) if line not in old]
        dialog = getattr(self, "_last_dialog", "") or ""
        self._last_dialog = ""
        if dialog:
            fresh.insert(0, dialog.replace("\n", " ").strip())
        return fresh

    def _page_says(self, page, label: str, before: List[str], secret: str = "", wait: float = 0) -> List[str]:
        """Collect the sentences the page added since before, and log them without the SMS code."""
        fresh: List[str] = []
        deadline = time.time() + wait
        while True:
            fresh = [line for line in self._new_lines(page, before) if len(line) <= 200]
            if fresh or time.time() >= deadline:
                break
            page.wait_for_timeout(500)
        if fresh and before:
            shown = [line.replace(secret, "•••") if secret else line for line in fresh]
            self._say(f"{label}: صفحه: {self._quote(shown)}", "running")
        return fresh

    def _wait_sms(self, page, label: str, since: float) -> str:
        self._say(
            f"{label}: منتظر پیامک «کد تایید» ۷ رقمی. اگر اپ پیامک وصل نیست، کد را در کادر پیامک داشبورد بنویسید.",
            "needs_operator", stage="wait_sms",
        )
        deadline = time.time() + SMS_WAIT
        while time.time() < deadline:
            self._hold()
            item = sms.take_code(since)
            if item:
                self._say(f"{label}: کد پیامک رسید.", "running", stage="filling")
                return item["code"]
            cmd = self._poll(0.5)
            if cmd == "skip":
                return ""
            try:
                page.wait_for_timeout(500)
            except Exception:
                return ""
        return ""

    def _fill_form_captcha(self, page) -> str:
        info = None
        for _ in range(6):
            info = self._js(page, "() => window.__sadaf.formCaptcha()")
            if info:
                break
            self._settle(page)
        if not info:
            self._snap(page, "no-form-captcha")
            return "کپچای کنار «کد مقابل را در قسمت زیر وارد نمایید» پیدا نشد."
        if not info.get("box"):
            return "کادر کپچای صفحهٔ آخر پیدا نشد."
        raw = self._form_captcha_bytes(page, info.get("src") or "")
        length = int(info.get("max") or 0)
        code = ""
        if length in (0, 4):
            code = _sadaf_code(raw)
        if not code and length:
            code = self._read_code(raw, length)
        if not code and not length:
            for size in (5, 6, 3):
                code = self._read_code(raw, size)
                if code:
                    break
        if not code:
            return "کپچای صفحهٔ آخر خوانده نشد."
        if not self._js(page, "c => window.__sadaf.fillFormCaptcha(c)", code):
            return "کپچای صفحهٔ آخر در کادر ننشست."
        return ""

    def _form_captcha_bytes(self, page, src: str) -> bytes:
        try:
            data = page.evaluate(
                """() => { const img = document.querySelector('[data-sadaf-cap="1"]');
                    if (!img || !img.naturalWidth) return '';
                    const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
                    c.getContext('2d').drawImage(img, 0, 0); return c.toDataURL('image/png'); }"""
            )
            if data and data.startswith("data:image/png;base64,"):
                import base64
                return base64.b64decode(data.split(",", 1)[1])
        except Exception:
            pass
        if src and not src.startswith("data:"):
            try:
                from urllib.parse import urljoin
                response = page.request.get(urljoin(page.url, src))
                if response.ok:
                    return response.body()
            except Exception:
                pass
        try:
            return page.locator("[data-sadaf-cap='1']").screenshot(type="png")
        except Exception:
            return b""

    def _read_code(self, raw: bytes, length: int) -> str:
        if not raw:
            return ""
        try:
            text = _sadaf_engine().classification(raw)
        except Exception:
            return ""
        code = "".join(ch for ch in str(text or "") if ch.isalnum()).lower()
        return code if len(code) == length else ""

    # ------------------------------------------------------- page actions
    def _click(self, page, tag: str) -> bool:
        """Click a marked element with the mouse after the page is idle, then wait for the postback."""
        self._settle(page, pause=0)
        target = page.locator(f'[data-sadaf-click="{tag}"]').first
        try:
            if target.count() == 0:
                return False
            target.scroll_into_view_if_needed(timeout=3000)
            target.click(timeout=8000)
        except Exception:
            try:
                if not page.evaluate(
                    "t => { const el = document.querySelector('[data-sadaf-click=\"' + t + '\"]'); if (!el) return false; el.click(); return true; }",
                    tag,
                ):
                    return False
            except Exception:
                return False
        self._settle(page)
        return True

    def _press(self, page, tag: str, key: str) -> None:
        try:
            page.locator(f'[data-sadaf-click="{tag}"]').first.press(key, timeout=3000)
        except Exception:
            pass
        self._settle(page)

    def _snap(self, page, tag: str) -> None:
        """Save the page HTML for diagnosis. Password values and ViewState are removed."""
        try:
            html = page.evaluate(SNAPSHOT_JS)
        except Exception:
            return
        try:
            SNAPSHOTS.mkdir(parents=True, exist_ok=True)
            self._snap_count = getattr(self, "_snap_count", 0) + 1
            run_id = self.state.get("run_id") or 0
            path = SNAPSHOTS / f"run{run_id}-{self._snap_count:02d}-{tag}.html"
            path.write_text(html, encoding="utf-8")
            old = sorted(SNAPSHOTS.glob("*.html"), key=lambda item: item.stat().st_mtime)
            for item in old[:-120]:
                item.unlink(missing_ok=True)
        except Exception:
            pass

    def _wait_decision(self) -> str:
        while True:
            cmd = self._poll(0.4)
            if cmd in {"next", "accept_uncertain", "skip", "refill"}:
                return cmd
            if cmd == "stop":
                raise StopRun()

    def _poll(self, seconds: float):
        self._hold()
        try:
            cmd = self.cmds.get(timeout=seconds)
        except queue.Empty:
            return None
        if cmd == "stop":
            raise StopRun()
        if cmd in {"pause", "resume"}:
            return None
        return cmd

    def _hold(self) -> None:
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise StopRun()
            self.stop_event.wait(0.2)
        if self.stop_event.is_set():
            raise StopRun()

    def _js(self, page, expression: str, arg=None):
        from playwright.sync_api import Error
        try:
            page.evaluate(_helpers())
            return page.evaluate(expression, arg)
        except Error as exc:
            text = str(exc)
            if "context was destroyed" in text or "navigat" in text or "Cannot find context" in text:
                return None
            raise

    def _settle(self, page, pause: int = 800) -> None:
        if pause:
            page.wait_for_timeout(pause)
        try:
            page.wait_for_load_state("load", timeout=60000)
        except Exception:
            pass
        for _ in range(40):
            if self._js(page, "() => window.__sadaf.busy()") is False:
                return
            page.wait_for_timeout(250)

    def _finish_item(self, run_id: int, person: dict, outcome: dict, report, row_number: int) -> None:
        stamp = now()
        ensure_schema()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO run_items(run_id, customer_id, status, stage, attempts, error, started_at, finished_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, person["id"], outcome["status"], outcome["stage"], outcome["attempts"], outcome["error"][:500], stamp, stamp),
            )
        new_file = not report.exists()
        with report.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(["ردیف", "شناسه", "نام", "نام کاربری", "زمان", "وضعیت", "مرحله", "تلاش", "علت"])
            writer.writerow([
                row_number, person["id"], person.get("full_name") or "", person["username"], stamp,
                outcome["status"], outcome["stage"], outcome["attempts"], outcome["error"],
            ])

    def _checkpoint(self, run_id: int, status: str) -> None:
        ensure_schema()
        finished = now() if status in {"done", "stopped", "failed"} else None
        with connect() as conn:
            conn.execute("UPDATE runs SET status = ?, finished_at = COALESCE(?, finished_at) WHERE id = ?", (status, finished, run_id))

    def _safe_error(self, exc: Exception) -> str:
        text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        if "closed" in text.lower():
            return "مرورگر سدف بسته شد. برای ادامه، عملیات را از نو شروع کنید."
        return "خطای سدف: " + text[:240]
