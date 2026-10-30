import io
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback when torch is not installed
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]
    TORCH_AVAILABLE = False

from database import DBHandler


IMAGE_WIDTH = 160
IMAGE_HEIGHT = 60
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BLANK_LABEL = 0
IDX2CHAR = {index + 1: char for index, char in enumerate(CHARS)}

CAPTCHA_IMAGE_SELECTORS: Tuple[str, ...] = (
    "img.BDC_CaptchaImage",
    "img[id*='CaptchaImage' i]",
    "img[src*='BotDetectCaptcha' i]",
    ".BDC_CaptchaImage img",
    ".BDC_CaptchaImage",
    "#captchaImage",
    "#captchaImg",
    "img[src*='captcha' i]",
)

CAPTCHA_RELOAD_SELECTORS: Tuple[str, ...] = (
    ".BDC_ReloadLink",
    "a[id*='ReloadLink' i]",
    "img[id*='ReloadIcon' i]",
    "a[title*='تغییر' i]",
    "a[title*='کد امنیتی' i]",
    "a[href*='ReloadImage' i]",
)


if TORCH_AVAILABLE:
    class CRNN(nn.Module):
        """CRNN architecture used to train ``my_captcha_model.pth``."""

        def __init__(self, num_chars=len(CHARS), hidden_size=256):
            super().__init__()
            self.cnn = nn.Sequential(
                nn.Conv2d(1, 64, 3, 1, 1),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.MaxPool2d(2, 2),
                nn.Conv2d(64, 128, 3, 1, 1),
                nn.BatchNorm2d(128),
                nn.ReLU(),
                nn.MaxPool2d(2, 2),
                nn.Conv2d(128, 256, 3, 1, 1),
                nn.BatchNorm2d(256),
                nn.ReLU(),
                nn.MaxPool2d((2, 1)),
                nn.Conv2d(256, 512, 3, 1, 1),
                nn.BatchNorm2d(512),
                nn.ReLU(),
                nn.MaxPool2d((2, 1)),
            )
            with torch.no_grad():
                sample = self.cnn(torch.zeros(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH))
            recurrent_input_size = sample.shape[1] * sample.shape[2]
            self.rnn = nn.LSTM(
                input_size=recurrent_input_size,
                hidden_size=hidden_size,
                bidirectional=True,
                num_layers=2,
                batch_first=True,
            )
            self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

        def forward(self, image):
            batch_size = image.size(0)
            features = self.cnn(image)
            features = features.permute(0, 3, 1, 2)
            features = features.reshape(batch_size, features.size(1), -1)
            features, _ = self.rnn(features)
            logits = self.fc(features)
            return logits.permute(1, 0, 2)
else:
    class CRNN:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("PyTorch is required to instantiate CRNN")


def _resolve_captcha_model_path(model_path: Optional[str] = None) -> str:
    """Resolve ``my_captcha_model.pth`` across env, current folder, parent folders, and Desktop."""
    if model_path:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(model_path)))
    env_path = os.getenv("CAPTCHA_MODEL_PATH")
    if env_path:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(env_path)))

    here = Path(__file__).resolve().parent
    candidates: List[Path] = [
        here / "my_captcha_model.pth",
        here.parent / "my_captcha_model.pth",
        here.parent.parent / "my_captcha_model.pth",
        Path.cwd() / "my_captcha_model.pth",
        Path.cwd().parent / "my_captcha_model.pth",
        Path.home() / "Desktop" / "my_captcha_model.pth",
        Path.home() / "Desktop" / "MAHANBOT" / "my_captcha_model.pth",
        Path.home() / "Desktop" / "Mahanbot-fixed-20260808" / "my_captcha_model.pth",
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand.resolve())
        except Exception:
            continue
    return str((here / "my_captcha_model.pth").resolve())


class ModelCaptchaSolver:
    """Load the local CAPTCHA model and decode CAPTCHA images with PIL."""

    CONFIDENCE_THRESHOLD = 0.45
    MAX_RETRY_ATTEMPTS = 2
    _SHARED_MODEL = None
    _SHARED_LOCK = threading.RLock()
    _SHARED_DDDDOCR = None

    def __init__(self, model_path=None, device=None, model=None):
        self.model_path = _resolve_captcha_model_path(model_path)
        # Default to CPU: deterministic, rock-solid, zero VRAM contention or device mismatch
        if TORCH_AVAILABLE:
            self.device = torch.device(device or "cpu")
        else:
            self.device = device or "cpu"
        self._model = model
        self._lock = threading.RLock()
        self.last_error: Optional[str] = None
        self._ddddocr = None

        if self._model is not None and TORCH_AVAILABLE and hasattr(self._model, "to"):
            self._model.to(self.device)
            self._model.eval()
        else:
            try:
                self.load_model()
            except Exception:
                pass

    @property
    def is_loaded(self):
        return self._model is not None or ModelCaptchaSolver._SHARED_MODEL is not None

    def _get_ddddocr(self):
        if self._ddddocr is not None:
            return self._ddddocr
        with ModelCaptchaSolver._SHARED_LOCK:
            if ModelCaptchaSolver._SHARED_DDDDOCR is not None:
                self._ddddocr = ModelCaptchaSolver._SHARED_DDDDOCR
                return self._ddddocr
            try:
                import ddddocr
                ocr = ddddocr.DdddOcr(show_ad=False)
                ModelCaptchaSolver._SHARED_DDDDOCR = ocr
                self._ddddocr = ocr
                return self._ddddocr
            except Exception:
                return None

    def load_model(self):
        """Load and cache model weights on the configured local device."""
        if self._model is not None:
            return self._model

        with ModelCaptchaSolver._SHARED_LOCK:
            if ModelCaptchaSolver._SHARED_MODEL is not None and str(self.device) == "cpu":
                self._model = ModelCaptchaSolver._SHARED_MODEL
                return self._model

            if not TORCH_AVAILABLE:
                self.last_error = "کتابخانه PyTorch (torch) نصب نیست"
                raise FileNotFoundError(self.last_error)
            if not os.path.isfile(self.model_path):
                resolved = _resolve_captcha_model_path()
                if os.path.isfile(resolved):
                    self.model_path = resolved
                else:
                    self.last_error = f"فایل مدل کپچا پیدا نشد: {self.model_path}"
                    raise FileNotFoundError(self.last_error)

            model = CRNN(num_chars=len(CHARS)).to(self.device)
            try:
                checkpoint = torch.load(
                    self.model_path,
                    map_location=self.device,
                    weights_only=True,
                )
            except TypeError:
                checkpoint = torch.load(self.model_path, map_location=self.device)

            if isinstance(checkpoint, dict):
                for key in ("model_state_dict", "state_dict"):
                    if key in checkpoint and isinstance(checkpoint[key], dict):
                        checkpoint = checkpoint[key]
                        break
            if not isinstance(checkpoint, dict):
                self.last_error = "فرمت فایل مدل کپچا معتبر نیست (state_dict یافت نشد)"
                raise ValueError(self.last_error)

            state_dict = {
                (key[7:] if key.startswith("module.") else key): value
                for key, value in checkpoint.items()
            }
            model.load_state_dict(state_dict, strict=True)
            model.eval()
            self._model = model
            if str(self.device) == "cpu":
                ModelCaptchaSolver._SHARED_MODEL = model
            self.last_error = None
            return self._model

    @staticmethod
    def _open_image(image_source):
        if isinstance(image_source, Image.Image):
            img = image_source.copy()
        elif isinstance(image_source, (bytes, bytearray, memoryview)):
            img = Image.open(io.BytesIO(bytes(image_source)))
        elif isinstance(image_source, (str, os.PathLike)):
            img = Image.open(os.fspath(image_source))
        elif hasattr(image_source, "read"):
            img = Image.open(image_source)
        else:
            raise TypeError("CAPTCHA image must be bytes, a path, or a readable binary stream")

        # Composite transparent pixels onto a clean white background
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            return bg
        elif img.mode != "RGB" and img.mode != "L":
            img = img.convert("RGB")
        return img

    @staticmethod
    def _enhance_image(image: Image.Image, mode: str = "raw") -> Image.Image:
        """
        Preprocess CAPTCHA image:
        - 'raw': pure grayscale ('L') exactly matching standard CRNN training
        - 'contrast': gentle autocontrast + sharpen without destructive hard-thresholding
        - 'binarize': adaptive threshold fallback only if raw/contrast fail
        """
        image = image.convert("L")
        if mode == "raw":
            return image
        if mode == "contrast":
            image = ImageOps.autocontrast(image, cutoff=1)
            image = image.filter(ImageFilter.SHARPEN)
            return image
        if mode == "binarize":
            image = ImageOps.autocontrast(image, cutoff=2)
            arr = np.asarray(image, dtype=np.uint8)
            thresh = int(np.mean(arr)) if arr.size else 128
            image = image.point(lambda x: 255 if x > thresh else 0, "1").convert("L")
            return image
        return image

    def preprocess(self, image_source, enhance=False):
        if not TORCH_AVAILABLE:
            raise OSError("PyTorch is not installed")
        mode = enhance if isinstance(enhance, str) else ("contrast" if enhance else "raw")
        with self._open_image(image_source) as image:
            image = self._enhance_image(image, mode=mode)
            resampling = getattr(Image, "Resampling", Image)
            image = image.resize((IMAGE_WIDTH, IMAGE_HEIGHT), resampling.BILINEAR)
            pixels = torch.tensor(list(image.getdata()), dtype=torch.float32)

        return pixels.reshape(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH).div_(255.0).to(self.device)

    @staticmethod
    def decode(logits) -> Tuple[str, float]:
        probs = F.softmax(logits, dim=2)
        max_probs, indices = torch.max(probs, dim=2)
        indices = indices.permute(1, 0)
        max_probs = max_probs.permute(1, 0)

        decoded = []
        confidences = []

        for seq_idx in range(indices.shape[0]):
            previous = -1
            chars = []
            seq_confidences = []

            for time_idx in range(indices.shape[1]):
                index = int(indices[seq_idx, time_idx].item())
                prob = float(max_probs[seq_idx, time_idx].item())

                if index != BLANK_LABEL and index != previous:
                    char = IDX2CHAR.get(index)
                    if char:
                        chars.append(char)
                        seq_confidences.append(prob)
                previous = index

            decoded.append("".join(chars))
            avg_confidence = sum(seq_confidences) / len(seq_confidences) if seq_confidences else 0.0
            confidences.append(avg_confidence)

        return (decoded[0] if decoded else "", confidences[0] if confidences else 0.0)

    def solve(self, image_source, use_enhanced: bool = True, min_confidence: float = None) -> Optional[str]:
        """
        Solve CAPTCHA image using local models:
        1. CRNN Primary: Evaluates PyTorch CRNN model across standard preprocessing modes.
        2. DdddOcr Fallback: If CRNN does not output a valid code, invokes local offline OCR engine.
        """
        if min_confidence is None:
            min_confidence = self.CONFIDENCE_THRESHOLD

        best_prediction = None
        best_confidence = -1.0

        try:
            model = self.load_model()
            modes = ["raw", "contrast", "binarize"] if use_enhanced else ["raw"]
            for mode in modes:
                try:
                    tensor = self.preprocess(image_source, enhance=mode)
                    with self._lock, torch.inference_mode():
                        logits = model(tensor)
                        prediction, confidence = self.decode(logits)

                    prediction = "".join(char for char in prediction.upper() if char in CHARS)
                    if not prediction:
                        continue

                    if validate_captcha_prediction(prediction, 4, 8):
                        if confidence >= min_confidence:
                            return prediction
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_prediction = prediction
                    elif best_prediction is None and len(prediction) >= 4:
                        best_prediction = prediction
                except Exception:
                    continue
        except Exception as exc:
            self.last_error = str(exc)

        if best_prediction and validate_captcha_prediction(best_prediction, 4, 8):
            return best_prediction

        # Auxiliary local engine fallback if CRNN alone did not yield a valid code
        try:
            ocr = self._get_ddddocr()
            if ocr is not None:
                img_bytes = None
                if isinstance(image_source, (bytes, bytearray, memoryview)):
                    img_bytes = bytes(image_source)
                elif isinstance(image_source, Image.Image):
                    buf = io.BytesIO()
                    image_source.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                elif isinstance(image_source, (str, os.PathLike)) and os.path.exists(image_source):
                    with open(image_source, "rb") as f:
                        img_bytes = f.read()
                if img_bytes:
                    raw = str(ocr.classification(img_bytes) or "").strip().upper()
                    clean = "".join(c for c in raw if c in CHARS)
                    if validate_captcha_prediction(clean, 4, 8):
                        return clean
        except Exception:
            pass

        return best_prediction


def wait_for_captcha_image_ready(
    page,
    timeout_ms: int = 5000,
    trigger_refresh_if_missing: bool = True,
    previous_src: Optional[str] = None,
):
    """
    Wait until a CAPTCHA image element appears in the DOM, has finished loading, and is visible on screen with real dimensions.
    Returns (captcha_locator, scope) or (None, None).
    """
    deadline = time.time() + max(0.5, timeout_ms / 1000.0)
    refreshed_once = False

    def _iter_scopes():
        yield page
        try:
            for frame in page.frames:
                fname = (frame.name or "").lower()
                furl = (frame.url or "").lower()
                if "tspd" in fname or "tspd" in furl:
                    continue
                yield frame
        except Exception:
            return

    while time.time() < deadline:
        for scope in _iter_scopes():
            for sel in CAPTCHA_IMAGE_SELECTORS:
                try:
                    loc = scope.locator(sel)
                    cnt = loc.count()
                    for idx in range(min(cnt, 4)):
                        cand = loc.nth(idx)
                        if not cand.is_visible():
                            continue

                        # Check image has completed loading
                        try:
                            is_loaded = cand.evaluate("""el => {
                                const img = el.tagName && el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                                if (img) {
                                    return img.complete && (img.naturalWidth === undefined || img.naturalWidth > 0);
                                }
                                return true;
                            }""")
                            if not is_loaded:
                                continue
                        except Exception:
                            pass

                        # If previous_src was specified, ensure image src has changed
                        if previous_src:
                            try:
                                cur_src = cand.evaluate("""el => {
                                    const img = el.tagName && el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                                    return img ? (img.getAttribute('src') || '') : '';
                                }""")
                                if cur_src and cur_src == previous_src:
                                    continue
                            except Exception:
                                pass

                        try:
                            box = cand.bounding_box()
                            if box and (box.get("width", 0) < 15 or box.get("height", 0) < 8):
                                continue
                        except Exception:
                            pass

                        return cand, scope
                except Exception:
                    continue

        if trigger_refresh_if_missing and not refreshed_once and (deadline - time.time()) < (timeout_ms / 2000.0):
            refreshed_once = True
            for scope in _iter_scopes():
                for rsel in CAPTCHA_RELOAD_SELECTORS:
                    try:
                        rloc = scope.locator(rsel)
                        if rloc.count() > 0 and rloc.first.is_visible():
                            rloc.first.click(timeout=700)
                            break
                    except Exception:
                        continue

        time.sleep(0.08)

    return None, None


def refresh_captcha_or_reload(
    bot,
    page,
    input_sel="#ctl00_ContentPlaceHolder1_tbCaptcha2",
    timeout=12000,
    max_retries=3,
    allow_reload=True,
    captcha_rejected=False,
    **kwargs,
):
    """Smart strategy: Try clicking refresh button or image first, then fallback to full reload if allowed."""
    old_src = None
    try:
        current_img, _ = wait_for_captcha_image_ready(page, timeout_ms=800, trigger_refresh_if_missing=False)
        if current_img is not None:
            old_src = current_img.evaluate("""el => {
                const img = el.tagName && el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                return img ? (img.getAttribute('src') || '') : '';
            }""")
    except Exception:
        old_src = None

    for attempt in range(max_retries):
        try:
            target_scope = page
            clicked_reload = False
            for rsel in CAPTCHA_RELOAD_SELECTORS:
                try:
                    reload_btn = target_scope.locator(rsel).first
                    if reload_btn.count() > 0 and reload_btn.is_visible():
                        reload_btn.click(timeout=1000)
                        clicked_reload = True
                        break
                except Exception:
                    continue

            if not clicked_reload:
                try:
                    cimg, _ = wait_for_captcha_image_ready(page, timeout_ms=600, trigger_refresh_if_missing=False)
                    if cimg is not None:
                        cimg.click(timeout=800)
                        clicked_reload = True
                except Exception:
                    pass

            if clicked_reload:
                if input_sel:
                    try:
                        loc = page.locator(input_sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.fill("")
                    except Exception:
                        pass

                new_img, _ = wait_for_captcha_image_ready(
                    page, timeout_ms=2500, trigger_refresh_if_missing=False, previous_src=old_src
                )
                if new_img is not None:
                    return True
        except Exception:
            continue

    if not allow_reload:
        return False

    try:
        page.reload(wait_until="commit")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
        except Exception:
            pass
        return True
    except Exception:
        return False

def detect_captcha_failure(page) -> bool:
    """Detect if CAPTCHA submission failed based on page content."""
    phrases = [
        "کد امنیتی اشتباه",
        "کد امنیتی صحیح",
        "کد امنیتی نادرست",
        "کد امنیتی وارد شده نامعتبر",
        "security code",
        "captcha incorrect",
        "invalid captcha",
        "کپچا نامعتبر",
    ]
    try:
        content = page.content()
    except Exception:
        return False
    return any(phrase in content for phrase in phrases)


def validate_captcha_prediction(prediction: str, min_length: int = 4, max_length: int = 12) -> bool:
    if not prediction:
        return False
    if not (min_length <= len(prediction) <= max_length):
        return False
    if not prediction.isalnum():
        return False
    return True


def iter_page_scopes(page):
    yield page
    try:
        for frame in page.frames:
            fname = (frame.name or "").lower()
            furl = (frame.url or "").lower()
            if "tspd" in fname or "tspd" in furl:
                continue
            yield frame
    except Exception:
        return


def wait_for_manual_captcha_handoff(
    bot,
    page,
    input_sel: str,
    btn_sel: str,
    stop_event,
    source: str = "general",
    success_selectors=("#ctl00_ContentPlaceHolder1_tbMobileConfCode", "input[name$='tbMobileConfCode']", "#ctl00_ContentPlaceHolder1_ddlBankName"),
) -> bool:
    """
    Hybrid Manual + Active Model Assist Loop:
    While waiting for manual CAPTCHA entry, if a fresh/valid CAPTCHA image appears on the page,
    automatically pass it to the local model to solve and submit!
    """
    from event_logger import emit_structured_event

    emit_structured_event(
        bot.nid,
        "CAPTCHA_MANUAL_REQUIRED",
        step=getattr(bot, "_current_step", "STEP_1_ENTRY_FORM"),
        reason=f"auto_captcha_assist:{source}",
        result="waiting_or_auto_solving",
        message="منتظر بارگذاری تصویر کپچا برای حل خودکار با مدل (یا ورود دستی کاربر)...",
        level="warning",
    )
    bot.set_runtime_state("WAITING_USER_INPUT", "CAPTCHA_MANUAL_REQUIRED", browser_status="OPEN", last_error="captcha_manual_or_model")
    DBHandler.update_status(bot.nid, "منتظر CAPTCHA", "تلاش همزمان مدل کپچا و امکان ورود دستی")

    timeout_sec = int(bot.settings.get("captcha_manual_timeout", 300))
    if hasattr(bot, "suspend_watchdog"):
        bot.suspend_watchdog(timeout_sec + 15)

    deadline = time.time() + timeout_sec
    last_model_try_ts = 0.0
    last_img_bytes = None

    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if hasattr(bot, "mark_progress"):
            bot.mark_progress("manual_captcha_wait")

        for sel in success_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    bot.log("✅ عبور از مرحله کپچا تایید شد و صفحه به مرحله بعد منتقل شد.", "success", page)
                    bot._captcha_retry = 0
                    return True
            except Exception:
                continue

        now = time.time()
        if now - last_model_try_ts >= 2.5:
            last_model_try_ts = now
            try:
                img_loc, _ = wait_for_captcha_image_ready(page, timeout_ms=600, trigger_refresh_if_missing=True)
                if img_loc is not None:
                    cur_bytes = None
                    try:
                        cur_bytes = img_loc.screenshot()
                    except Exception:
                        cur_bytes = None
                    if cur_bytes is None or cur_bytes != last_img_bytes:
                        last_img_bytes = cur_bytes
                        if bot.solve_captcha_with_model(
                            page,
                            input_sel,
                            btn_sel,
                            stop_event=stop_event,
                            source=f"{source}_assist",
                            captcha_element=img_loc,
                        ):
                            time.sleep(0.6)
                            continue
                    else:
                        refresh_captcha_or_reload(bot, page, input_sel=input_sel, allow_reload=False)
            except Exception:
                pass

        time.sleep(0.3)

    bot.set_runtime_state("ERROR", "CAPTCHA_MANUAL_TIMEOUT", browser_status="OPEN", last_error="captcha_manual_timeout")
    return False


def solve_captcha_wrapper(bot, page, input_sel, btn_sel, mode):
    stop_event = getattr(bot, "_active_stop_event", None)
    max_retries = max(1, int(bot.settings.get("captcha_max_retries", 5)))
    attempts = 0
    try:
        while attempts < max_retries:
            if stop_event is not None and stop_event.is_set():
                return False
            attempts += 1

            # Wait for CAPTCHA image to appear and finish loading
            img_loc, _ = wait_for_captcha_image_ready(page, timeout_ms=4000, trigger_refresh_if_missing=True)
            if img_loc is None:
                try:
                    page.wait_for_selector(
                        "#ctl00_ContentPlaceHolder1_ddlBankName",
                        timeout=800,
                        state="visible",
                    )
                    return True
                except Exception:
                    pass

            if not solve_captcha_step(bot, page, input_sel, btn_sel, source="otp"):
                refresh_captcha_or_reload(bot, page, input_sel=input_sel, allow_reload=False)
                continue

            code = ""
            try:
                inp = page.locator(input_sel)
                if inp.count() > 0:
                    code = inp.first.input_value().strip()
            except Exception:
                code = ""

            failed = detect_captcha_failure(page)
            if not failed:
                if code:
                    DBHandler.append_captcha_attempt(bot.nid, code, "success", source="select")
                return True

            if code:
                DBHandler.append_captcha_attempt(bot.nid, code, "failed", source="select")
            refresh_captcha_or_reload(bot, page, input_sel=input_sel, allow_reload=False)

        if bool(bot.settings.get("pause_for_manual_captcha", True)):
            return wait_for_manual_captcha_handoff(bot, page, input_sel, btn_sel, stop_event, source="wrapper")
        return False
    except Exception:
        return False


def solve_captcha_step(bot, page, input_sel: str, btn_sel: str, source: str = "general") -> bool:
    stop_event = getattr(bot, "_active_stop_event", None)
    if stop_event is None or stop_event.is_set():
        return False

    img_loc, _ = wait_for_captcha_image_ready(page, timeout_ms=3500, trigger_refresh_if_missing=True)
    return bot.solve_captcha_with_model(
        page,
        input_sel,
        btn_sel,
        stop_event=stop_event,
        source=source,
        captcha_element=img_loc,
    )
