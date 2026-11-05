import io
import os
import threading
import time
from pathlib import Path
import io
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    "img[src*='BotDetectCaptcha'][src*='get=image']",
    "img[src*='BotDetectCaptcha']:not([src*='icon']):not([src*='sound']):not([src*='reload'])",
    ".BDC_CaptchaImage img",
    "#captchaImage",
    "#captchaImg",
    "img[src*='captcha' i]:not([src*='icon']):not([src*='sound']):not([src*='reload'])",
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
        Composites transparent pixels onto white background to ensure reliable OCR across all BotDetect styles.
        """
        if min_confidence is None:
            min_confidence = self.CONFIDENCE_THRESHOLD

        best_prediction = None
        best_confidence = -1.0

        clean_pil = self._open_image(image_source)
        g_lum = clean_pil.convert("L")
        mean_lum = sum(g_lum.resize((16, 16)).getdata()) / 256.0
        active_source = ImageOps.invert(clean_pil.convert("RGB")) if mean_lum < 125 else clean_pil

        def _try_auxiliary_fallback():
            try:
                ocr = self._get_ddddocr()
                if ocr is not None:
                    # 1. Standard composited image on clean white
                    buf = io.BytesIO()
                    clean_pil.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                    if img_bytes:
                        raw = str(ocr.classification(img_bytes) or "").strip().upper()
                        clean = "".join(c for c in raw if c in CHARS)
                        if validate_captcha_prediction(clean, 4, 8):
                            self.last_error = None
                            return clean

                    # 2. Inverted if dark background
                    if active_source is not clean_pil:
                        buf_act = io.BytesIO()
                        active_source.save(buf_act, format="PNG")
                        raw_act = str(ocr.classification(buf_act.getvalue()) or "").strip().upper()
                        clean_act = "".join(c for c in raw_act if c in CHARS)
                        if validate_captcha_prediction(clean_act, 4, 8):
                            self.last_error = None
                            return clean_act

                    # 3. Autocontrast + sharpen
                    ac_im = ImageOps.autocontrast(g_lum, cutoff=1).filter(ImageFilter.SHARPEN).convert("RGB")
                    buf_ac = io.BytesIO()
                    ac_im.save(buf_ac, format="PNG")
                    raw_ac = str(ocr.classification(buf_ac.getvalue()) or "").strip().upper()
                    clean_ac = "".join(c for c in raw_ac if c in CHARS)
                    if validate_captcha_prediction(clean_ac, 4, 8):
                        self.last_error = None
                        return clean_ac
            except Exception:
                pass
            return None

        try:
            model = self.load_model()
            modes = ["raw", "contrast", "binarize"] if use_enhanced else ["raw"]
            for idx, mode in enumerate(modes):
                try:
                    tensor = self.preprocess(active_source, enhance=mode)
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
                except Exception:
                    continue

                if idx == 0 and (not best_prediction or best_confidence < min_confidence):
                    fallback_code = _try_auxiliary_fallback()
                    if fallback_code:
                        return fallback_code
        except Exception as exc:
            self.last_error = str(exc)

        fallback_code = _try_auxiliary_fallback()
        if fallback_code:
            return fallback_code

        if best_prediction and validate_captcha_prediction(best_prediction, 4, 8):
            return best_prediction

        return None


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
                        if hasattr(cand, 'locator') and cand.evaluate('el => el.tagName') != 'IMG' and cand.locator('img').count():
                            cand = cand.locator('img').first
                        if not cand.is_visible():
                            continue

                        # Check image has completed loading and is a real CAPTCHA, not an icon
                        try:
                            is_loaded = cand.evaluate("""el => {
                                const img = el.tagName && el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
                                if (!img) return false;
                                const src = (img.getAttribute('src') || '').toLowerCase();
                                if (src.includes('reload') || src.includes('sound') || src.includes('icon') || src.includes('help')) {
                                    return false;
                                }
                                const nw = img.naturalWidth !== undefined ? img.naturalWidth : (img.width || 0);
                                const nh = img.naturalHeight !== undefined ? img.naturalHeight : (img.height || 0);
                                if (nw > 0 && nw < 70) return false;
                                if (nh > 0 && nh < 20) return false;
                                return img.complete && nw > 0;
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
                            if box and (box.get("width", 0) < 70 or box.get("height", 0) < 20):
                                continue
                        except Exception:
                            pass

                        return cand, scope
                except Exception:
                    continue

        if trigger_refresh_if_missing and not refreshed_once and (deadline - time.time()) < (timeout_ms / 2000.0):
            refreshed_once = True
            clicked = False
            for scope in _iter_scopes():
                for rsel in CAPTCHA_RELOAD_SELECTORS:
                    try:
                        rloc = scope.locator(rsel)
                        if rloc.count() > 0 and rloc.first.is_visible():
                            rloc.first.click(timeout=700)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break

        page.wait_for_timeout(40)

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
    """Refresh only a rejected challenge, in the same frame as its answer."""
    rejected = (captcha_rejected or getattr(bot, '_last_dialog_action', None) == 'captcha_invalid'
                or detect_captcha_failure(page) or kwargs.get("force", False))
    if not rejected:
        return False
    image, scope = wait_for_captcha_image_ready(page, timeout_ms=800, trigger_refresh_if_missing=False)
    if image is None:
        image, scope = wait_for_captcha_image_ready(page, timeout_ms=800, trigger_refresh_if_missing=True)
        return image is not None
    old_src = image.evaluate("el => el.getAttribute('src') || ''")
    try:
        for selector in CAPTCHA_RELOAD_SELECTORS:
            link = scope.locator(selector).first
            if link.count() and link.is_visible():
                link.click(timeout=1000)
                break
        else:
            image.click(timeout=1000)
        candidate_selectors = [
            input_sel,
            "input[name*='tbCaptcha']",
            "#ctl00_ContentPlaceHolder1_tbCaptcha",
            "#ctl00_ContentPlaceHolder1_tbCaptcha2",
            "#ans"
        ]
        for csel in candidate_selectors:
            if not csel:
                continue
            try:
                ans_loc = scope.locator(csel)
                if ans_loc.count() > 0 and ans_loc.first.is_visible():
                    ans_loc.first.fill('')
                    break
            except Exception:
                pass
        bot._last_dialog_action = None
        flags = getattr(bot, '_recovery_flags', {})
        if isinstance(flags, dict):
            flags['captcha_invalid'] = False
        fresh, _ = wait_for_captcha_image_ready(page, timeout_ms=min(timeout, 2500),
                         trigger_refresh_if_missing=False, previous_src=old_src)
        return fresh is not None
    except Exception:
        return False


def detect_captcha_failure(page) -> bool:
    """Only visible server rejection text counts; labels and scripts are not errors."""
    import re
    from otp_form_detector import normalize_text, live_scopes
    try:
        for scope in live_scopes(page):
            text = normalize_text(scope.locator('body').inner_text(timeout=500))
            if re.search(r'(?:کد امنیتی|کد تصویر|کپچا|عبارت امنیتی|security code).{0,30}'
                         r'(?:اشتباه|نادرست|نامعتبر|صحیح نیست|صحیح نمی|incorrect|invalid)', text):
                return True
    except Exception:
        pass
    return False


def detect_form_validation_error(page) -> Optional[str]:
    """Detect if the page has validation error messages (missing dates, invalid NID) that stop form progression."""
    selectors = [
        "#ctl00_ContentPlaceHolder1_lblMessage",
        "#ctl00_ContentPlaceHolder1_vsValidation",
        ".ValidationSummary",
        "span[id*='lblMessage']",
        "span[id*='lblError']",
        "div.alert-danger",
    ]
    try:
        for sel in selectors:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = (loc.first.inner_text() or "").strip()
                if txt and not any(c in txt for c in ("کد امنیتی", "کپچا", "security")):
                    return txt
    except Exception:
        pass
    return None


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
    success_selectors=("#ctl00_ContentPlaceHolder1_tbMobileConfCode", "input[name$=\'tbMobileConfCode\']", "#ctl00_ContentPlaceHolder1_ddlBankName"),
    refresh_on_failure: bool = False,
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
    last_img_src = None
    cur_src = None

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
        if (cur_src != last_img_src) or (now - last_model_try_ts >= 1.0):
            last_model_try_ts = now
            try:
                img_loc, _ = wait_for_captcha_image_ready(page, timeout_ms=600, trigger_refresh_if_missing=True)
                if img_loc is not None:
                    cur_src = None
                    try:
                        cur_src = img_loc.evaluate("el => el.getAttribute('src') || ''")
                    except Exception:
                        cur_src = None
                    if cur_src != last_img_src:
                        last_img_src = cur_src
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
                        if refresh_on_failure and not page.locator(input_sel).first.input_value().strip():
                            # Select's primary form needs another native challenge after unreadable OCR.
                            refresh_captcha_or_reload(bot, page, input_sel=input_sel, allow_reload=False, force=True)
                            last_img_src = None
            except Exception as exc:
                if refresh_on_failure:
                    last_img_src = None
                    bot.log(f"خطای تلاش مجدد کپچای انتخاب بانک: {type(exc).__name__}", "warning", page)

        if refresh_on_failure:
            try:
                page.wait_for_timeout(300)
            except Exception as exc:
                bot.log(f"توقف انتظار کپچای انتخاب بانک: {type(exc).__name__}", "warning", page)
                return False
        else:
            time.sleep(0.3)

    bot.set_runtime_state("ERROR", "CAPTCHA_MANUAL_TIMEOUT", browser_status="OPEN", last_error="captcha_manual_timeout")
    return False


def solve_captcha_wrapper(bot, page, input_sel, btn_sel, mode):
    stop_event = getattr(bot, "_active_stop_event", None)
    max_retries = max(1, int(bot.settings.get("captcha_max_retries", 10)))
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
                return False

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

        if bool(bot.settings.get("pause_for_manual_captcha", False)):
            return wait_for_manual_captcha_handoff(bot, page, input_sel, btn_sel, stop_event, source="wrapper")
        return False
    except Exception:
        return False


def solve_captcha_step(bot, page, input_sel: str, btn_sel: str, source: str = "general") -> bool:
    stop_event = getattr(bot, "_active_stop_event", None)
    if stop_event is not None and stop_event.is_set():
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


@dataclass
class CaptchaResult:
    success: bool
    code: str = ""
    attempts_used: int = 0
    error_reason: Optional[str] = None


def solve_step_captcha(
    page,
    step_name: str,
    force_refresh: bool = False,
    max_attempts: int = 10,
    bot: Any = None,
    stop_event: Any = None,
) -> CaptchaResult:
    """
    Unified Deterministic CAPTCHA Solver for both Step 1 and Step 2.
    Ensures complete image rendering (img.complete && img.naturalWidth > 0) before taking
    screenshots, captures image bytes, invokes the local CRNN model, validates alphanumeric format,
    retries automatically on empty/invalid predictions, fills the field, and verifies insertion.
    Emits the exact 6-step lifecycle events:
      1. CAPTCHA_ELEMENT_SEARCH
      2. CAPTCHA_IMAGE_FOUND
      3. CAPTCHA_INPUT_FOUND
      4. CAPTCHA_RECOGNITION_STARTED
      5. CAPTCHA_RECOGNITION_RESULT
      6. CAPTCHA_FILLED
    """
    norm_step = str(step_name or "").upper()
    is_step_1 = "1" in norm_step or "ENTRY" in norm_step
    step_key = "STEP_1_ENTRY_FORM" if is_step_1 else "STEP_2_OTP"
    step_fa = "مرحله ۱" if is_step_1 else "مرحله پیامک"

    if is_step_1:
        img_selectors = [
            "#ctl00_ContentPlaceHolder1_imgCaptcha",
            "img.BDC_CaptchaImage",
            "img[id*='CaptchaImage' i]",
            "img[src*='BotDetectCaptcha'][src*='get=image']",
        ]
        input_selectors = [
            "#ctl00_ContentPlaceHolder1_tbCaptcha",
            "#ctl00_ContentPlaceHolder1_tbCaptcha1",
            "input[name$='tbCaptcha']",
            "input[name$='tbCaptcha1']",
            "input[id*='tbCaptcha']",
        ]
        reload_selectors = [
            "#ctl00_ContentPlaceHolder1_imgCaptcha_ReloadLink",
            ".BDC_ReloadLink",
            "a[id*='ReloadLink' i]",
            "img[id*='ReloadIcon' i]",
            "a[title*='تغییر' i]",
        ]
    else:
        img_selectors = [
            "#ctl00_ContentPlaceHolder1_imgCaptcha2",
            "img[id*='captcha2' i][id*='CaptchaImage' i]",
            "img[id*='CaptchaImage' i]",
            "img[src*='captcha2']",
            "img.BDC_CaptchaImage",
            "img[src*='BotDetectCaptcha'][src*='get=image']",
        ]
        input_selectors = [
            "#ctl00_ContentPlaceHolder1_tbCaptcha2",
            "input[name$='tbCaptcha2']",
            "input[id*='tbCaptcha2']",
            "#ctl00_ContentPlaceHolder1_tbCaptcha",
            "input[name$='tbCaptcha']",
        ]
        reload_selectors = [
            "a[id*='captcha2' i][id*='ReloadLink' i]",
            ".BDC_ReloadLink",
            "a[id*='ReloadLink' i]",
            "img[id*='ReloadIcon' i]",
            "a[title*='تغییر' i]",
        ]

    def _emit(event_type: str, **kwargs):
        if bot is not None and hasattr(bot, "emit_event"):
            bot.emit_event(event_type, step=step_key, page=page, **kwargs)

    if bot is not None and hasattr(bot, "settings") and bot.settings.get('pause_for_manual_captcha', False):
        _emit(
            "CAPTCHA_MANUAL_REQUIRED",
            reason="settings_pause_for_manual",
            message="ورود دستی کپچا فعال است؛ منتظر اقدام کاربر.",
            level="warning",
        )
        if hasattr(bot, "nid"):
            DBHandler.update_runtime(bot.nid, otp_state='CAPTCHA_REQUIRED')
        return CaptchaResult(success=False, error_reason="manual_required")

    min_length = int(bot.settings.get("captcha_min_length", 4)) if (bot and hasattr(bot, "settings")) else 4
    max_length = int(bot.settings.get("captcha_max_length", 12)) if (bot and hasattr(bot, "settings")) else 12

    for attempt in range(1, max_attempts + 1):
        if stop_event is not None and stop_event.is_set():
            return CaptchaResult(success=False, attempts_used=attempt - 1, error_reason="stopped")

        _emit("CAPTCHA_ELEMENT_SEARCH", message=f"در حال جستجوی المان‌های کپچای {step_fa}...")

        image_elem = None
        input_elem = None
        reload_elem = None

        # For Step 2, use _locate_otp_captcha_controls if available
        if not is_step_1:
            try:
                from bot_select_otp import _locate_otp_captcha_controls
                c_img, c_inp, _ = _locate_otp_captcha_controls(page, wait_ms=1000)
                if c_img is not None:
                    image_elem = c_img
                if c_inp is not None:
                    input_elem = c_inp
            except Exception:
                pass

        # If not found via helper, iterate scopes
        for scope in iter_page_scopes(page):
            if input_elem is None:
                for sel in input_selectors:
                    try:
                        loc = scope.locator(sel)
                        cnt = loc.count() if hasattr(loc, "count") else (1 if loc else 0)
                        if cnt > 0:
                            cand = loc.first if hasattr(loc, "first") else loc
                            if cand.is_visible():
                                input_elem = cand
                                break
                    except Exception:
                        continue

            if image_elem is None:
                for sel in img_selectors:
                    try:
                        loc = scope.locator(sel)
                        cnt = loc.count() if hasattr(loc, "count") else (1 if loc else 0)
                        if cnt > 0:
                            cand = loc.first if hasattr(loc, "first") else loc
                            try:
                                src = (cand.evaluate("el => el.getAttribute('src') || ''") or "").lower()
                                if any(x in src for x in ("sound", "reload", "icon", "help")):
                                    continue
                            except Exception:
                                pass
                            if cand.is_visible():
                                image_elem = cand
                                break
                    except Exception:
                        continue

            if reload_elem is None:
                for sel in reload_selectors:
                    try:
                        loc = scope.locator(sel)
                        cnt = loc.count() if hasattr(loc, "count") else (1 if loc else 0)
                        if cnt > 0:
                            cand = loc.first if hasattr(loc, "first") else loc
                            if cand.is_visible():
                                reload_elem = cand
                                break
                    except Exception:
                        continue

            if image_elem is not None and input_elem is not None:
                break

        if image_elem is None:
            image_elem, _ = wait_for_captcha_image_ready(page, timeout_ms=1200, trigger_refresh_if_missing=False)

        if image_elem is not None:
            _emit("CAPTCHA_IMAGE_FOUND", result="found", message=f"تصویر کپچای {step_fa} شناسایی شد.")
        elif bot is not None and hasattr(bot, "log"):
            bot.log(f"تصویر کپچای {step_fa} یافت نشد.", "warning", page)

        if input_elem is not None:
            _emit("CAPTCHA_INPUT_FOUND", result="found", message=f"فیلد ورودی کپچای {step_fa} شناسایی شد.")
        elif bot is not None and hasattr(bot, "log"):
            bot.log(f"فیلد ورودی کپچای {step_fa} یافت نشد.", "warning", page)

        if image_elem is None or input_elem is None:
            if not is_step_1 and image_elem is None:
                _emit("IMAGE_NOT_READY", result="image_missing", level="warning")
            if attempt < max_attempts:
                time.sleep(0.2)
                continue
            return CaptchaResult(success=False, attempts_used=attempt, error_reason="elements_missing")

        # If refresh requested
        if (force_refresh or attempt > 1) and reload_elem is not None:
            try:
                reload_elem.click(timeout=1000)
                time.sleep(0.25)
            except Exception:
                try:
                    image_elem.click(timeout=1000)
                    time.sleep(0.25)
                except Exception:
                    pass

        if not is_step_1:
            try:
                handle = image_elem.element_handle(timeout=1000) if hasattr(image_elem, "element_handle") else None
                if hasattr(page, "wait_for_function"):
                    page.wait_for_function(
                        "(img) => img && img.complete && img.naturalWidth > 0",
                        arg=handle,
                        timeout=3000,
                    )
                elif hasattr(image_elem, "evaluate"):
                    ready = image_elem.evaluate("(img) => img && img.complete && (img.naturalWidth === undefined || img.naturalWidth > 0)")
                    if ready is False:
                        _emit("IMAGE_NOT_READY", result="image_not_decoded", level="warning")
                        if attempt < max_attempts:
                            time.sleep(0.2)
                            continue
            except Exception:
                _emit("IMAGE_NOT_READY", result="image_not_decoded", level="warning")
                if attempt < max_attempts:
                    time.sleep(0.2)
                    continue
                return CaptchaResult(success=False, attempts_used=attempt, error_reason="image_not_ready")
        else:
            # Wait for image DOM readiness (complete && naturalWidth > 0)
            try:
                handle = image_elem.element_handle() if hasattr(image_elem, "element_handle") else None
                if handle and hasattr(page, "wait_for_function"):
                    page.wait_for_function(
                        "(img) => !img || (typeof img.complete === 'undefined') || (img.complete && (img.naturalWidth === undefined || img.naturalWidth > 0))",
                        arg=handle,
                        timeout=3000,
                    )
            except Exception:
                pass

        # Clear field before recognition
        try:
            input_elem.fill("")
        except Exception:
            pass

        source_bytes = None
        if bot is not None and hasattr(bot, "_capture_captcha_image"):
            try:
                source_bytes = bot._capture_captcha_image(image_elem, "otp" if not is_step_1 else "step1")
            except Exception:
                source_bytes = None

        if not source_bytes:
            try:
                source_bytes = image_elem.screenshot(timeout=3000)
            except Exception:
                source_bytes = None

        if is_step_1 and source_bytes and len(source_bytes) < 600:
            source_bytes = None

        if not is_step_1 and not source_bytes:
            _emit("IMAGE_NOT_READY", result="image_capture_failed", level="warning")
            continue

        _emit("CAPTCHA_RECOGNITION_STARTED", message="آغاز پردازش و استخراج خودکار کد کپچا با مدل هوشمند...")

        raw_code = ""
        processing_error = False
        if bot is not None and hasattr(bot, "captcha_service") and bot.captcha_service:
            try:
                if not is_step_1:
                    from api_solver import CaptchaService
                    service = bot.captcha_service
                    options = {"raise_on_error": True} if isinstance(service, CaptchaService) else {}
                    raw_code = str(service.solve(source_bytes, **options) or "")
                else:
                    raw_code = str((bot.captcha_service.solve(source_bytes) if source_bytes else bot.captcha_service.solve_element(image_elem)) or "")
            except Exception:
                processing_error = True
        else:
            try:
                solver = ModelCaptchaSolver.get_instance()
                if solver and solver.is_ready() and source_bytes:
                    raw_code = str(solver.solve_image(source_bytes) or "")
                else:
                    processing_error = True
            except Exception:
                processing_error = True

        if not is_step_1:
            if processing_error:
                _emit("MODEL_PROCESSING_ERROR", result="solver_failed", level="warning")
            elif not raw_code.strip():
                _emit("MODEL_OUTPUT_EMPTY", result="empty_prediction", level="warning")

        code = re.sub(r'[^A-Z0-9]', '', str(raw_code or "").strip().upper())
        is_valid = bool(re.fullmatch(rf"[0-9A-Z]{{{min_length},{max_length}}}", code)) and validate_captcha_prediction(code, min_length, max_length)

        _emit(
            "CAPTCHA_RECOGNITION_RESULT",
            result=f"code={code}" if is_valid else "invalid",
            message=f"نتیجه تشخیص مدل کپچا: {code}" if is_valid else f"پاسخ کپچا نامعتبر است: {raw_code}",
            level="info" if is_valid else "warning",
        )

        if not is_valid:
            if bot is not None and hasattr(bot, "nid"):
                DBHandler.append_captcha_attempt(bot.nid, code, "failed", source=step_key)
            try:
                input_elem.fill("")
            except Exception:
                pass
            force_refresh = True
            time.sleep(0.2)
            continue

        try:
            from browser_launcher import BrowserActions
            BrowserActions.human_fill(input_elem, code)
        except Exception:
            try:
                input_elem.fill(code, timeout=1500)
            except Exception:
                pass

        try:
            input_elem.dispatch_event("change")
        except Exception:
            pass

        filled_val = (input_elem.input_value() or "").strip().upper()
        if filled_val == code:
            _emit(
                "CAPTCHA_FILLED",
                result="success",
                message=f"کپچای {step_fa} با مقدار {code} با موفقیت پر شد.",
                level="info",
            )
            if bot is not None and hasattr(bot, "nid"):
                DBHandler.append_captcha_attempt(bot.nid, code, "filled", source=step_key)
            return CaptchaResult(success=True, code=code, attempts_used=attempt, error_reason=None)
        else:
            if bot is not None and hasattr(bot, "nid"):
                DBHandler.append_captcha_attempt(bot.nid, code, "fill_failed", source=step_key)
            force_refresh = True
            time.sleep(0.2)
            continue

    return CaptchaResult(success=False, code="", attempts_used=max_attempts, error_reason="max_attempts_exceeded")
