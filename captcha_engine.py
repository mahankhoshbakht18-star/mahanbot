"""Isolated CBI captcha engine.

One entry point: solve_and_fill_captcha(page).
The job threads use Playwright's sync API, so this function is synchronous.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger("mahanbot.captcha")

IMAGE_WIDTH = 160
IMAGE_HEIGHT = 60
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
VISION_MODEL = "huihui_ai/qwen2.5-vl-abliterated:7b"
_VISION_OFF_UNTIL = 0.0
BLANK_LABEL = 0
IDX2CHAR = {index + 1: char for index, char in enumerate(CHARS)}
MODEL_PATH = Path(__file__).resolve().parent / "my_captcha_model.pth"

IMG_SELECTORS = (
    "img.BDC_CaptchaImage",
    "img[id*='CaptchaImage' i]",
    "img[src*='BotDetectCaptcha'][src*='get=image']",
    "#ctl00_ContentPlaceHolder1_imgCaptcha2",
    "#ctl00_ContentPlaceHolder1_imgCaptcha",
)
INPUT_SELECTORS = (
    "#ctl00_ContentPlaceHolder1_tbCaptcha2",
    "#ctl00_ContentPlaceHolder1_tbCaptcha1",
    "#ctl00_ContentPlaceHolder1_tbCaptcha",
    "input[id*='tbCaptcha' i]",
)
RELOAD_SELECTORS = (
    "a.BDC_ReloadLink",
    "a[id*='ReloadLink' i]",
    "img[id*='ReloadIcon' i]",
    "a[title*='تغییر' i]",
)

try:
    import torch
    import torch.nn as nn
    TORCH_OK = True
except ImportError:
    torch = None
    nn = None
    TORCH_OK = False


if TORCH_OK:
    class _CRNN(nn.Module):
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
            self.rnn = nn.LSTM(
                input_size=sample.shape[1] * sample.shape[2],
                hidden_size=hidden_size,
                bidirectional=True,
                num_layers=2,
                batch_first=True,
            )
            self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

        def forward(self, image):
            batch = image.size(0)
            features = self.cnn(image)
            features = features.permute(0, 3, 1, 2).reshape(batch, features.size(3), -1)
            features, _ = self.rnn(features)
            return self.fc(features).permute(1, 0, 2)


class CaptchaModel:
    """One shared CPU copy of my_captcha_model.pth."""

    _instance = None
    _lock = threading.RLock()

    def __init__(self):
        self._model = None
        self.last_error = ""
        self.last_raw = ""

    @classmethod
    def shared(cls) -> "CaptchaModel":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def load(self):
        if self._model is not None:
            return self._model
        if not TORCH_OK:
            raise RuntimeError("torch is not installed")
        if not MODEL_PATH.is_file():
            raise FileNotFoundError(str(MODEL_PATH))
        model = _CRNN().to("cpu")
        try:
            checkpoint = torch.load(str(MODEL_PATH), map_location="cpu", weights_only=True)
        except TypeError:
            checkpoint = torch.load(str(MODEL_PATH), map_location="cpu")
        if isinstance(checkpoint, dict):
            for key in ("model_state_dict", "state_dict"):
                if key in checkpoint and isinstance(checkpoint[key], dict):
                    checkpoint = checkpoint[key]
                    break
        state = {(k[7:] if k.startswith("module.") else k): v for k, v in checkpoint.items()}
        model.load_state_dict(state, strict=True)
        model.eval()
        self._model = model
        self.last_error = ""
        return model

    def predict(self, image_bytes: bytes) -> str:
        """Same read as the old bot: grayscale, plain 160x60 resize, one local forward."""
        self.last_raw = ""
        text = _read_trained(self.load(), image_bytes)
        self.last_raw = f"local={text or '-'}"
        return text


class ModelCaptchaSolver(CaptchaModel):
    """Name kept so older imports resolve to this engine."""

    def solve(self, image_source, use_enhanced: bool = True, min_confidence: float = None) -> Optional[str]:
        del use_enhanced, min_confidence
        try:
            if isinstance(image_source, (bytes, bytearray)):
                raw = bytes(image_source)
            else:
                buf = io.BytesIO()
                _open_image(image_source).save(buf, format="PNG")
                raw = buf.getvalue()
            return self.predict(raw) or None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def load_model(self):
        return self.load()


@dataclass
class CaptchaSolveResult:
    success: bool
    code: str = ""
    filled: bool = False
    submitted: bool = False
    attempts: int = 0
    latency_ms: int = 0
    reason: str = ""
    refreshed: bool = False


def solve_and_fill_captcha(
    page,
    input_selector: str = "",
    button_selector: str = "",
    submit: bool = False,
    max_retries: int = 3,
    bot: Any = None,
    stop_event: Any = None,
    step: str = "captcha",
) -> CaptchaSolveResult:
    """Capture, read, fill, and optionally submit one captcha. Never raises."""
    started = time.perf_counter()
    attempts = 0
    refreshed = False
    try:
        max_retries = max(1, min(int(max_retries or 3), 6))
        seen = str(getattr(page, "_mahan_captcha_hash", "") or "")
        for attempts in range(1, max_retries + 1):
            if _stopped(stop_event):
                return _finish(False, attempts, started, "stopped", refreshed, bot, step)
            image, field, reload = _find_controls(page, input_selector)
            if image is None or field is None:
                _log(bot, step, "elements_missing", "", started, False)
                continue
            attempt_started = time.perf_counter()
            captured = capture_captcha_bytes(page, image)
            digest = _digest(captured)
            hold_same = bool(getattr(page, "_mahan_hold_captcha", False))
            if hold_same:
                try:
                    page._mahan_hold_captcha = False
                except Exception:
                    pass
            if (not captured or (seen and digest == seen)) and not hold_same:
                refreshed = refresh_captcha(page, image, reload, getattr(bot, 'settings', None)) or refreshed
                image, field, reload = _find_controls(page, input_selector)
                if image is None or field is None:
                    continue
                captured = capture_captcha_bytes(page, image)
                digest = _digest(captured)
                if not captured or digest == seen:
                    _log(bot, step, "stale_image", "", attempt_started, False, attempt=attempts, detail=digest[:8])
                    continue
            seen = digest
            try:
                page._mahan_captcha_hash = digest
            except Exception:
                pass
            _log(bot, step, "captured", "", attempt_started, True, attempt=attempts, detail=digest[:8])
            t0 = time.perf_counter()
            model = CaptchaModel.shared()
            code = model.predict(captured)
            latency = int((time.perf_counter() - t0) * 1000)
            accepted = len(code or "") >= 4 and all(ch in CHARS for ch in code)
            _log(
                bot, step, "predicted", code, attempt_started, accepted,
                latency_ms=latency, attempt=attempts, detail=model.last_raw,
            )
            if not accepted:
                refreshed = refresh_captcha(page, image, reload, getattr(bot, 'settings', None)) or refreshed
                continue
            if not _fill(field, code):
                _log(bot, step, "fill_failed", code, attempt_started, False, attempt=attempts)
                continue
            submitted = False
            if submit and button_selector:
                try:
                    submitted = _click_submit(page, button_selector, field)
                except Exception:
                    submitted = False
            result = CaptchaSolveResult(
                success=True,
                code=code,
                filled=True,
                submitted=submitted or not submit,
                attempts=attempts,
                latency_ms=int((time.perf_counter() - attempt_started) * 1000),
                refreshed=refreshed,
            )
            _log(bot, step, "filled", code, attempt_started, True, latency_ms=result.latency_ms, attempt=attempts)
            return result
        return _finish(False, attempts, started, "unreadable", refreshed, bot, step)
    except Exception as exc:
        logger.exception("captcha engine failed")
        _log(bot, step, "error", "", started, False, detail=type(exc).__name__)
        return CaptchaSolveResult(False, attempts=attempts, reason=type(exc).__name__, refreshed=refreshed)


def attach_captcha_listener(page) -> None:
    """No separate captcha request. The painted element is the only image source."""
    del page


def capture_captcha_bytes(page, image) -> bytes:
    """Captcha pixels independent of browser zoom.

    An element screenshot is taken at CSS size x devicePixelRatio, so when the
    user lowers Chrome zoom (Ctrl -) the model receives a shrunken, blurred
    image and misreads it. The decoded <img> pixels at naturalWidth/Height are
    identical at every zoom level, so they are always preferred. The screenshot
    is only a fallback and is resampled back to the natural size.
    """
    del page
    raw = _canvas_bytes(image)
    if raw and _usable(raw):
        return raw
    try:
        shot = image.screenshot(type="png", timeout=800) or b""
    except Exception:
        return b""
    return _rescale_to_natural(image, shot)


def _rescale_to_natural(image, shot: bytes) -> bytes:
    if not shot:
        return b""
    try:
        size = image.evaluate(
            """(img) => {
                const node = img && img.tagName && img.tagName.toLowerCase() === 'img' ? img : (img.querySelector ? img.querySelector('img') : null);
                return node && node.naturalWidth ? [node.naturalWidth, node.naturalHeight] : null;
            }"""
        )
        if not size:
            return shot
        width, height = int(size[0]), int(size[1])
        with Image.open(io.BytesIO(shot)) as picture:
            picture.load()
            if picture.size == (width, height) or width < 40 or height < 15:
                return shot
            resampling = getattr(Image, "Resampling", Image)
            resized = picture.convert("RGB").resize((width, height), resampling.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return shot


def _digest(raw: bytes) -> str:
    if not raw:
        return ""
    return hashlib.sha256(raw).hexdigest()


def refresh_captcha(page, image, reload, settings=None) -> bool:
    """Click the on-page reload and wait until the painted pixels actually change."""
    del page
    from core.timings import seconds
    before = _digest(_canvas_bytes(image))
    clicked = False
    for target in (reload, image):
        if target is None:
            continue
        try:
            target.click(timeout=400)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return False
    deadline = time.perf_counter() + seconds(settings, 'captcha_refresh_timeout_ms')
    while time.perf_counter() < deadline:
        painted = _canvas_bytes(image)
        digest = _digest(painted)
        if painted and digest and digest != before and _usable(painted):
            return True
        time.sleep(0.04)
    return False


def _find_controls(page, input_selector: str):
    image = _first(page, IMG_SELECTORS)
    selectors = tuple(part.strip() for part in str(input_selector or "").split(",") if part.strip())
    field = _first(page, selectors + INPUT_SELECTORS)
    reload = _first(page, RELOAD_SELECTORS)
    return image, field, reload


def _first(page, selectors):
    frames = [page]
    try:
        for frame in page.frames:
            url = (getattr(frame, "url", "") or "").lower()
            if "tspd" in url:
                continue
            if frame is not page:
                frames.append(frame)
    except Exception:
        pass
    for frame in frames:
        for selector in selectors:
            try:
                loc = frame.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                continue
    return None


def _canvas_bytes(image) -> bytes:
    try:
        encoded = image.evaluate(
            """(img) => {
                const node = img && img.tagName && img.tagName.toLowerCase() === 'img' ? img : (img.querySelector ? img.querySelector('img') : null);
                if (!node || !node.complete || !node.naturalWidth || node.naturalWidth < 40) return '';
                const canvas = document.createElement('canvas');
                canvas.width = node.naturalWidth;
                canvas.height = node.naturalHeight;
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(node, 0, 0);
                const url = canvas.toDataURL('image/png');
                const comma = url.indexOf(',');
                return comma >= 0 ? url.slice(comma + 1) : '';
            }"""
        )
        if not encoded:
            return b""
        return base64.b64decode(str(encoded))
    except Exception:
        return b""


def _usable(raw) -> bool:
    if not raw or len(raw) < 80:
        return False
    try:
        with Image.open(io.BytesIO(bytes(raw))) as image:
            image.load()
            width, height = image.size
            if width < 40 or height < 15:
                return False
            low, high = image.convert("L").getextrema()
            return (high - low) >= 12
    except Exception:
        return False


def _open_image(source) -> Image.Image:
    if isinstance(source, Image.Image):
        image = source.copy()
    else:
        image = Image.open(io.BytesIO(bytes(source)))
        image.load()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        bg = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "P":
            image = image.convert("RGBA")
        bg.paste(image, mask=image.split()[-1])
        return bg
    if image.mode not in ("RGB", "L"):
        return image.convert("RGB")
    return image


def _prepare(source) -> Image.Image:
    """Crop the element's extra white margin, then match the 160x60 training contrast."""
    image = _crop_margin(_open_image(source))
    gray = image.convert("L")
    sample = gray.resize((16, 16))
    mean = sum(sample.getdata()) / 256.0
    if mean < 125:
        image = ImageOps.invert(image.convert("RGB"))
    return image


def _crop_margin(image: Image.Image) -> Image.Image:
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < 242 else 0)
    box = mask.getbbox()
    if not box:
        return image
    left, top, right, bottom = box
    left = max(0, left - 1)
    top = max(0, top - 1)
    right = min(image.width, right + 1)
    bottom = min(image.height, bottom + 1)
    if right - left < 24 or bottom - top < 12:
        return image
    return image.crop((left, top, right, bottom))


def _tensor(image: Image.Image, mode: str):
    gray = image.convert("L")
    if mode == "contrast":
        gray = ImageOps.autocontrast(gray, cutoff=1).filter(ImageFilter.SHARPEN)
    elif mode == "binarize":
        gray = ImageOps.autocontrast(gray, cutoff=2)
        import numpy as np
        arr = np.asarray(gray, dtype="uint8")
        thresh = int(arr.mean()) if arr.size else 128
        gray = gray.point(lambda value: 255 if value > thresh else 0, "L")
    resampling = getattr(Image, "Resampling", Image)
    gray = gray.resize((IMAGE_WIDTH, IMAGE_HEIGHT), resampling.BILINEAR)
    pixels = torch.tensor(list(gray.getdata()), dtype=torch.float32)
    return pixels.reshape(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH).div_(255.0)


def _local_votes(model, image: Image.Image):
    votes = []
    for mode in ("raw", "contrast", "binarize"):
        with torch.inference_mode():
            logits = model(_tensor(image, mode))
        text, confidence = _decode(logits)
        text = "".join(ch for ch in text.upper() if ch in CHARS)
        votes.append((text, confidence))
    return votes


def _majority(local):
    buckets = {}
    for text, conf in local:
        text = "".join(ch for ch in (text or "").upper() if ch in CHARS)
        if text:
            buckets.setdefault(text, []).append(conf)
    if not buckets:
        return "", 0, 0.0
    text, scores = max(buckets.items(), key=lambda item: (len(item[1]), sum(item[1]) / len(item[1])))
    return text, len(scores), sum(scores) / len(scores)


def _vision_read(image_bytes: bytes) -> str:
    """Ask the local vision model. Skip it once a read exceeds the SMS window budget."""
    global _VISION_OFF_UNTIL
    if not image_bytes or time.time() < _VISION_OFF_UNTIL:
        return ""
    import json
    import urllib.request
    payload = json.dumps({
        "model": VISION_MODEL,
        "stream": False,
        "keep_alive": "10m",
        "messages": [{
            "role": "user",
            "content": "Read this captcha. Reply with only 4 to 6 uppercase letters and digits.",
            "images": [base64.b64encode(image_bytes).decode("ascii")],
        }],
        "options": {"temperature": 0, "num_predict": 8},
    }).encode("utf-8")
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = str((data.get("message") or {}).get("content") or "")
        text = "".join(ch for ch in text.upper() if ch in CHARS)
        if not CODE_RE.fullmatch(text):
            return ""
        return text
    except Exception as exc:
        if time.perf_counter() - started >= 3.5 or "timed out" in str(exc).lower():
            _VISION_OFF_UNTIL = time.time() + 600
            logger.warning("vision model is slower than the SMS window; paused for 10 minutes")
        return ""


def _ddddocr_votes(image: Image.Image):
    engine = _ocr_engine()
    if engine is None:
        return []
    votes = []
    variants = [image, ImageOps.autocontrast(image.convert("L"), cutoff=1).filter(ImageFilter.SHARPEN)]
    for variant in variants:
        try:
            text = engine.classification(_png_bytes(variant))
        except Exception:
            continue
        text = "".join(ch for ch in str(text or "").upper() if ch in CHARS)
        if CODE_RE.fullmatch(text) and text not in votes:
            votes.append(text)
    return votes


_OCR = None
_OCR_FAILED = False


def _ocr_engine():
    global _OCR, _OCR_FAILED
    if _OCR is not None or _OCR_FAILED:
        return _OCR
    try:
        import ddddocr
        _OCR = ddddocr.DdddOcr(show_ad=False)
    except Exception as exc:
        _OCR_FAILED = True
        logger.warning("ddddocr unavailable: %s", exc)
        _OCR = None
    return _OCR


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _read_trained(model, image_bytes: bytes) -> str:
    """Grayscale and a plain resize. Extra contrast or threshold changes the CTC text."""
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return ""
    image = cv2.resize(image, (IMAGE_WIDTH, IMAGE_HEIGHT))
    tensor = torch.from_numpy(image.astype("float32") / 255.0).unsqueeze(0).unsqueeze(0)
    with torch.inference_mode():
        output = model(tensor)
    output = output.permute(1, 0, 2)
    indices = torch.argmax(output, dim=2)
    chars = []
    previous = -1
    for raw_index in indices[0]:
        index = int(raw_index.item())
        if index != BLANK_LABEL and index != previous:
            char = IDX2CHAR.get(index)
            if char:
                chars.append(char)
        previous = index
    return "".join(chars).strip().upper()


def _decode(logits):
    import torch.nn.functional as F
    probs = F.softmax(logits, dim=2)
    max_probs, indices = torch.max(probs, dim=2)
    indices = indices.permute(1, 0)
    max_probs = max_probs.permute(1, 0)
    chars = []
    scores = []
    previous = -1
    for time_idx in range(indices.shape[1]):
        index = int(indices[0, time_idx].item())
        if index != BLANK_LABEL and index != previous:
            char = IDX2CHAR.get(index)
            if char:
                chars.append(char)
                scores.append(float(max_probs[0, time_idx].item()))
        previous = index
    text = "".join(chars)
    confidence = sum(scores) / len(scores) if scores else 0.0
    return text, confidence


def _fill(field, code: str) -> bool:
    """Per-character typing at the dashboard's captcha speed, then one click on the continue button by the caller."""
    from core.timings import jittered_seconds
    try:
        field.scroll_into_view_if_needed(timeout=500)
        think = jittered_seconds("captcha_think_ms")
        if think:
            time.sleep(think)
        field.click(timeout=800)
        field.press("Control+A", timeout=500)
        field.press("Backspace", timeout=500)
        for char in code:
            # The per-keystroke timeout must outlast the configured delay.
            delay_ms = int(jittered_seconds("captcha_type_char_ms") * 1000)
            field.type(char, delay=delay_ms, timeout=max(500, delay_ms + 500))
    except Exception:
        return False
    try:
        return (field.input_value(timeout=300) or "").strip().upper() == code
    except Exception:
        return False


def _click_submit(page, button_selector, field) -> bool:
    """Send the ASP.NET submit. click() must not wait for the postback or it reports failure after the click already landed."""
    posted = {"ok": False}

    def on_request(request):
        try:
            url = str(getattr(request, "url", "") or "").lower()
            method = (getattr(request, "method", "") or "").upper()
            if method == "POST" and (".aspx" in url or not url):
                posted["ok"] = True
        except Exception:
            pass

    try:
        page.on("request", on_request)
    except Exception:
        pass

    def wait_post(seconds: float) -> bool:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline and not posted["ok"]:
            time.sleep(0.02)
        return posted["ok"]

    try:
        return _dispatch_click(page, button_selector, field) or posted["ok"]
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass


def _mouse_click(target) -> bool:
    """One real mouse press on the continue button. No second postback behind it."""
    try:
        box = target.bounding_box(timeout=300)
    except Exception:
        box = None
    page = getattr(target, "page", None)
    if not box or page is None or box.get("width", 0) < 2 or box.get("height", 0) < 2:
        return False
    x = box["x"] + box["width"] * 0.5
    y = box["y"] + box["height"] * 0.55
    page.mouse.move(x, y, steps=5)
    time.sleep(0.03)
    page.mouse.down()
    time.sleep(0.04)
    page.mouse.up()
    return True


def _dispatch_click(page, button_selector, field) -> bool:
    targets = []
    if button_selector is not None and not isinstance(button_selector, str):
        targets.append(button_selector)
    else:
        scope = page
        try:
            handle = field.element_handle() if field is not None else None
            if handle is not None:
                scope = handle.owner_frame() or page
        except Exception:
            scope = page
        candidates = [part.strip() for part in str(button_selector or "").split(",") if part.strip()]
        for s in (scope, page):
            for selector in candidates:
                try:
                    loc = s.locator(selector)
                    if loc.count() > 0 and loc.first.is_visible():
                        targets.append(loc.first)
                        break
                except Exception:
                    continue
            if targets:
                break
    for target in targets:
        try:
            target.click(timeout=2000)
            return True
        except TypeError:
            try:
                target.click(timeout=1200)
                return True
            except Exception:
                continue
        except Exception as exc:
            err = str(exc).lower()
            if "navig" in err or "context" in err or "closed" in err:
                return True
            continue
    return False


def _js_postback(page, button_selector, field) -> bool:
    selector = ""
    if isinstance(button_selector, str):
        selector = next((part.strip() for part in button_selector.split(",") if part.strip()), "")
    try:
        scope = page
        try:
            handle = field.element_handle() if field is not None else None
            if handle is not None:
                scope = handle.owner_frame() or page
        except Exception:
            scope = page
        return bool(scope.evaluate(
            """(sel) => {
                const node = sel ? document.querySelector(sel) : null;
                const btn = node || document.querySelector("input[name$='btnSendConfirmCode'], input[name$='btnContinue'], input[type='submit']");
                if (!btn) return false;
                const target = btn.name || btn.id || '';
                if (typeof window.__doPostBack === 'function' && target) {
                    window.__doPostBack(target, '');
                    return true;
                }
                btn.click();
                return true;
            }""",
            selector,
        ))
    except Exception:
        return False


def _stopped(stop_event) -> bool:
    try:
        return bool(stop_event is not None and stop_event.is_set())
    except Exception:
        return False


def _finish(ok, attempts, started, reason, refreshed, bot, step):
    _log(bot, step, reason, "", started, ok, attempt=attempts)
    return CaptchaSolveResult(
        success=ok,
        attempts=attempts,
        latency_ms=int((time.perf_counter() - started) * 1000),
        reason=reason,
        refreshed=refreshed,
    )


def _log(bot, step, event, code, started, accepted, latency_ms=None, attempt=0, detail=""):
    elapsed = latency_ms if latency_ms is not None else int((time.perf_counter() - started) * 1000)
    logger.info(
        "captcha step=%s event=%s code=%s latency_ms=%s accepted=%s attempt=%s detail=%s",
        step, event, code or "-", elapsed, accepted, attempt, detail or "-",
    )
    text = {
        "captured": f"تصویر کپچا از المان صفحه گرفته شد ({detail})",
        "predicted": f"مدل محلی کپچا را خواند: {code or '—'} ({elapsed}ms) {detail}".strip(),
        "filled": f"کپچا با مقدار {code} پر شد ({elapsed}ms)",
        "stale_image": "تصویر کپچا عوض نشد؛ مدل روی همان تصویر اجرا نمی‌شود",
        "capture_failed": "تصویر رندر شده کپچا خوانده نشد",
        "unreadable": "کپچا بعد از تلاش‌های مجاز خوانا نبود",
        "elements_missing": "تصویر یا فیلد کپچا پیدا نشد",
        "fill_failed": f"کد {code} داخل فیلد ننشست",
        "error": f"خطای موتور کپچا: {detail}",
        "stopped": "حل کپچا متوقف شد",
    }.get(event)
    if text and bot is not None:
        level = "info" if accepted else "warning"
        try:
            if callable(getattr(bot, "log_msg", None)):
                bot.log_msg(text, level)
            elif callable(getattr(bot, "log", None)):
                bot.log(text, level)
        except Exception:
            pass
    if bot is not None and hasattr(bot, "emit_event"):
        try:
            bot.emit_event(
                "CAPTCHA_" + event.upper(),
                step=step,
                result="accepted" if accepted else event,
                message=text or event,
                level="info" if accepted else "warning",
            )
        except Exception:
            pass
