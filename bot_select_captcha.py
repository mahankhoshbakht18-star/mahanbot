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
    "img[id*='CaptchaImage' i]",
    "img[src*='BotDetectCaptcha' i]",
    "img.BDC_CaptchaImage",
    "img#captchaImage",
    "img#captchaImg",
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

    def __init__(self, model_path=None, device=None, model=None):

        self.model_path = _resolve_captcha_model_path(model_path)

        # Force CPU device for CAPTCHA inference to eliminate CUDA context overhead & device mismatch errors

        if TORCH_AVAILABLE:

            self.device = torch.device(device or "cpu")

        else:

            self.device = device or "cpu"

        self._model = model

        self._lock = threading.RLock()

        self.last_error: Optional[str] = None

        self._ddddocr = None

        self._init_ddddocr()

        if self._model is not None and TORCH_AVAILABLE and hasattr(self._model, "to"):

            self._model.to(self.device)

            self._model.eval()

    def _init_ddddocr(self):

        """Initialize local ddddocr instance as ultra-fast fallback/co-solver."""

        if self._ddddocr is None:

            try:

                import ddddocr

                self._ddddocr = ddddocr.DdddOcr(show_ad=False)

                try:

                    self._ddddocr.set_ranges(5)  # 5 = digits + uppercase letters

                except Exception:

                    pass

            except Exception:

                self._ddddocr = None

    @property

    def is_loaded(self):

        return self._model is not None

    def load_model(self):

        """Load and cache model weights on the configured local device."""

        if self._model is not None:

            return self._model

        with self._lock:

            if self._model is not None:

                return self._model

            if not TORCH_AVAILABLE:

                self.last_error = "کتابخانه PyTorch (torch) نصب نیست"

                raise FileNotFoundError(self.last_error)

            if not os.path.isfile(self.model_path):

                # Re-attempt dynamic discovery in case user copied the file after startup

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
            raise TypeError(f"CAPTCHA image must be bytes, PIL Image, a path, or readable stream, got {type(image_source)}")

        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            return bg
        return img

    @staticmethod

    def _enhance_image(image: Image.Image, mode: str = "raw") -> Image.Image:

        """

        Preprocess CAPTCHA image:

        - 'invert': Inverted grayscale (white text on dark background - matches BotDetect CRNN training!)

        - 'invert_contrast': Inverted + autocontrast + sharpen

        - 'raw': pure grayscale ('L')

        - 'contrast': gentle autocontrast + sharpen

        - 'binarize': adaptive threshold

        """

        image = image.convert("L")

        if mode == "raw":

            return image

        if mode == "invert":

            return ImageOps.invert(image)

        if mode == "invert_contrast":

            inv = ImageOps.invert(image)

            return ImageOps.autocontrast(inv, cutoff=1).filter(ImageFilter.SHARPEN)

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
        """Try CRNN once, then OCR on the same image before further preprocessing."""
        if min_confidence is None:
            min_confidence = self.CONFIDENCE_THRESHOLD
        self.last_error = None
        try:
            if isinstance(image_source, (bytes, bytearray, memoryview)):
                image_bytes = bytes(image_source)
            elif isinstance(image_source, Image.Image):
                buffer = io.BytesIO()
                image_source.save(buffer, format="PNG")
                image_bytes = buffer.getvalue()
            elif isinstance(image_source, (str, os.PathLike)):
                image_bytes = Path(image_source).read_bytes()
            else:
                image_bytes = image_source.read()
        except Exception as exc:
            self.last_error = str(exc)
            return None

        try:
            model = self.load_model()
        except Exception as exc:
            self.last_error = str(exc)
            model = None

        best_crnn_prediction = None
        best_crnn_confidence = -1.0

        modes = ["invert", "invert_contrast", "raw", "contrast", "binarize"] if use_enhanced else ["invert", "raw"]
        for index, mode in enumerate(modes if model is not None else [None]):
            if model is not None:
                try:
                    tensor = self.preprocess(image_bytes, enhance=mode)
                    with self._lock, torch.inference_mode():
                        logits = model(tensor)
                        raw, confidence = self.decode(logits)
                    candidate = "".join(char for char in raw.upper() if char in CHARS)
                    if validate_captcha_prediction(candidate, 4, 8):
                        if confidence >= min_confidence:
                            self.last_error = None
                            return candidate
                        if confidence > best_crnn_confidence:
                            best_crnn_confidence = confidence
                            best_crnn_prediction = candidate
                except Exception as exc:
                    self.last_error = str(exc)

            # Check ddddocr fallback right after the primary mode if confidence was below threshold
            if index == 0 and self._ddddocr is not None and image_bytes:
                try:
                    with self._lock:
                        raw = str(self._ddddocr.classification(image_bytes) or "").strip().upper()
                    candidate = "".join(char for char in raw if char in CHARS)
                    if validate_captcha_prediction(candidate, 4, 8):
                        self.last_error = None
                        return candidate
                except Exception as exc:
                    self.last_error = str(exc)

        # Fallback to best CRNN prediction if valid
        if best_crnn_prediction is not None:
            self.last_error = None
            return best_crnn_prediction

        return None


def iter_page_scopes(page):
    """Yield the page and its child frames once, excluding firewall frames."""
    yield "page", page
    try:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            if "tspd" in (frame.name or "").lower() or "tspd" in (frame.url or "").lower():
                continue
            yield f"frame:{frame.name}", frame
    except Exception:
        return


def wait_for_captcha_image_ready(page, timeout_ms: int = 5000, trigger_refresh_if_missing: bool = True, previous_src: Optional[str] = None):
    """Return a loaded, visible IMG and its frame; recover a missing image once."""
    deadline = time.time() + max(0.1, timeout_ms / 1000.0)
    refreshed_once = False
    while time.time() < deadline:
        for _, scope in iter_page_scopes(page):
            for selector in CAPTCHA_IMAGE_SELECTORS:
                try:
                    images = scope.locator(selector)
                    for index in range(min(images.count(), 4)):
                        image = images.nth(index)
                        if not image.is_visible():
                            continue
                        if not image.evaluate("img => img.tagName === 'IMG' && img.complete && img.naturalWidth > 0"):
                            continue
                        if previous_src and image.get_attribute("src") == previous_src:
                            continue
                        box = image.bounding_box()
                        if box and box.get("width", 0) >= 20 and box.get("height", 0) >= 10:
                            return image, scope
                except Exception:
                    continue
        if trigger_refresh_if_missing and not refreshed_once and deadline - time.time() < timeout_ms / 1500.0:
            refreshed_once = True
            clicked = False
            for _, scope in iter_page_scopes(page):
                for selector in CAPTCHA_RELOAD_SELECTORS:
                    try:
                        link = scope.locator(selector).first
                        if link.count() and link.is_visible():
                            link.click(timeout=700)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break
        time.sleep(0.08)
    return None, None

def refresh_captcha_or_reload(
    bot,
    page,
    input_sel="#ctl00_ContentPlaceHolder1_tbCaptcha2",
    timeout=12000,
    max_retries=3,
    allow_reload=False,
    captcha_rejected=False,
):
    """Refresh once only after server rejection or an image-loading failure."""
    image, scope = wait_for_captcha_image_ready(page, timeout_ms=600, trigger_refresh_if_missing=False)
    rejected = (
        captcha_rejected or detect_captcha_failure(page)
        or getattr(bot, "_last_dialog_action", None) == "captcha_invalid"
        or getattr(bot, "_recovery_flags", {}).get("captcha_invalid") is True
    )
    if image is not None and not rejected:
        return False
    old_src = image.get_attribute("src") if image is not None else None
    scopes = [scope] if scope is not None else [item for _, item in iter_page_scopes(page)]
    for candidate_scope in scopes:
        for selector in CAPTCHA_RELOAD_SELECTORS:
            try:
                link = candidate_scope.locator(selector).first
                if not link.count() or not link.is_visible():
                    continue
                link.click(timeout=800)
            except Exception:
                continue
            if getattr(bot, "_last_dialog_action", None) == "captcha_invalid":
                bot._last_dialog_action = None
            flags = getattr(bot, "_recovery_flags", {})
            if isinstance(flags, dict):
                flags["captcha_invalid"] = False
            if input_sel:
                target_scope = scope if scope is not None else candidate_scope
                target = target_scope.locator(input_sel).first
                if target.count() and target.is_visible():
                    target.fill("")
            fresh, _ = wait_for_captcha_image_ready(
                page, timeout_ms=min(timeout, 3000), trigger_refresh_if_missing=False,
                previous_src=old_src,
            )
            return fresh is not None
    if not allow_reload:
        return False
    try:
        page.reload(wait_until="domcontentloaded", timeout=timeout)
        if hasattr(bot, "_ensure_nid_after_reload"):
            bot._ensure_nid_after_reload(page)
        fresh, _ = wait_for_captcha_image_ready(page, timeout_ms=min(timeout, 3000), trigger_refresh_if_missing=False)
        return fresh is not None
    except Exception:
        return False

def detect_captcha_failure(page) -> bool:
    """Match explicit rejection in rendered text, never scripts or form labels."""
    phrases = (
        "کد امنیتی اشتباه", "کد امنیتی نادرست", "کد امنیتی وارد شده نامعتبر",
        "کد امنیتی صحیح نیست", "کد امنیتی صحیح نمی", "کپچا نامعتبر",
        "captcha incorrect", "incorrect captcha", "invalid captcha", "wrong captcha",
        "incorrect security code", "invalid security code", "security code is incorrect",
        "security code is invalid",
    )
    for _, scope in iter_page_scopes(page):
        try:
            text = scope.inner_text("body", timeout=500)
            text = " ".join(text.replace("\u200c", " ").lower().split())
            if any(phrase in text for phrase in phrases):
                return True
        except Exception:
            continue
    return False


def validate_captcha_prediction(prediction: str, min_length: int = 4, max_length: int = 8) -> bool:
    return (
        isinstance(prediction, str) and min_length <= len(prediction) <= max_length
        and all(char in CHARS for char in prediction)
    )

def wait_for_manual_captcha_handoff(
    bot, page, input_sel: str, btn_sel: str, stop_event, source: str = "general",
    success_selectors=("#ctl00_ContentPlaceHolder1_tbMobileConfCode", "input[name$='tbMobileConfCode']", "#ctl00_ContentPlaceHolder1_ddlBankName"),
) -> bool:
    """Keep the challenge stable for the user; assist once per distinct image."""
    from browser_actions import BrowserActions
    from event_logger import emit_structured_event

    emit_structured_event(
        bot.nid, "CAPTCHA_MANUAL_REQUIRED",
        step=getattr(bot, "_current_step", "STEP_1_ENTRY_FORM"),
        reason=f"auto_captcha_assist:{source}", result="waiting_or_auto_solving",
        message="تصویر کپچا برای ورود دستی حفظ شده است.", level="warning",
    )
    bot.set_runtime_state("WAITING_USER_INPUT", "CAPTCHA_MANUAL_REQUIRED", browser_status="OPEN", last_error="captcha_manual_or_model")
    DBHandler.update_status(bot.nid, "منتظر CAPTCHA", "انتظار برای ورود دستی کپچا")
    timeout_sec = int(bot.settings.get("captcha_manual_timeout", 300))
    if hasattr(bot, "suspend_watchdog"):
        bot.suspend_watchdog(timeout_sec + 15)
    deadline = time.time() + timeout_sec
    last_model_try_ts = 0.0
    last_image = None
    submission_pending = False
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if hasattr(bot, "mark_progress"):
            bot.mark_progress("manual_captcha_wait")
        for _, scope in iter_page_scopes(page):
            for selector in success_selectors:
                try:
                    target = scope.locator(selector).first
                    if target.count() and target.is_visible():
                        bot._captcha_retry = 0
                        return True
                except Exception:
                    continue
        if submission_pending and (
            detect_captcha_failure(page) or getattr(bot, "_last_dialog_action", None) == "captcha_invalid"
        ):
            refresh_captcha_or_reload(bot, page, input_sel=input_sel, captcha_rejected=True)
            submission_pending = False
        now = time.time()
        if not submission_pending and now - last_model_try_ts >= 2.5:
            last_model_try_ts = now
            try:
                user_typing = False
                for _, scope in iter_page_scopes(page):
                    field = BrowserActions.find_captcha_input(scope, input_sel)
                    if field is not None:
                        user_typing = bool(field.input_value()) or field.evaluate("el => el === el.ownerDocument.activeElement") is True
                        break
                if not user_typing:
                    image, _ = wait_for_captcha_image_ready(page, timeout_ms=600, trigger_refresh_if_missing=False)
                    if image is not None:
                        current = image.screenshot(type="png", timeout=1000)
                        if current != last_image:
                            last_image = current
                            submission_pending = bot.solve_captcha_with_model(
                                page, input_sel, btn_sel, stop_event=stop_event,
                                source=f"{source}_assist", captcha_element=image,
                            )
            except Exception:
                pass
        time.sleep(0.3)
    bot.set_runtime_state("ERROR", "CAPTCHA_MANUAL_TIMEOUT", browser_status="OPEN", last_error="captcha_manual_timeout")
    return False

def solve_captcha_wrapper(bot, page, input_sel, btn_sel, mode):
    stop_event = getattr(bot, "_active_stop_event", None)
    max_retries = max(1, int(bot.settings.get("captcha_max_retries", 5)))
    for _ in range(max_retries):
        if stop_event is not None and stop_event.is_set():
            return False
        if not solve_captcha_step(bot, page, input_sel, btn_sel, source="otp"):
            break
        if hasattr(bot, "wait_for_aspnet_postback"):
            bot.wait_for_aspnet_postback(page, timeout_ms=4000)
        rejected = detect_captcha_failure(page) or getattr(bot, "_last_dialog_action", None) == "captcha_invalid"
        if not rejected:
            return True
        if not refresh_captcha_or_reload(bot, page, input_sel=input_sel, captcha_rejected=True):
            break
    if bool(bot.settings.get("pause_for_manual_captcha", True)):
        return wait_for_manual_captcha_handoff(bot, page, input_sel, btn_sel, stop_event, source="wrapper")
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

