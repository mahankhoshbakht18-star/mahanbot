from __future__ import annotations

import gc
import hashlib
import io
import math
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import torch
import torch.nn as nn
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError


IMAGE_WIDTH = 160
IMAGE_HEIGHT = 60
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
IDX2CHAR = {index + 1: char for index, char in enumerate(CHARS)}
BLANK_LABEL = 0
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_PIXELS = 20_000_000
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "application/octet-stream",
}


def _default_model_path() -> Path:
    configured = str(os.getenv("MAHANBOT_CAPTCHA_MODEL_PATH") or "").strip()
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        try:
            return path.resolve(strict=False)
        except OSError:
            return path.absolute()
    return Path(__file__).resolve().with_name("my_captcha_model.pth")


MODEL_PATH = _default_model_path()


class CRNN(nn.Module):
    """Architecture used by the bundled, user-owned offline test model."""

    def __init__(self, num_chars: int, hidden_size: int = 256) -> None:
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
            dummy = torch.zeros(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH)
            output = self.cnn(dummy)
        linear_input = int(output.shape[1] * output.shape[2])
        self.rnn = nn.LSTM(
            input_size=linear_input,
            hidden_size=hidden_size,
            bidirectional=True,
            num_layers=2,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        batch_size = value.size(0)
        value = self.cnn(value)
        value = value.permute(0, 3, 1, 2)
        value = value.reshape(batch_size, value.size(1), -1)
        value, _ = self.rnn(value)
        value = self.fc(value)
        return value.permute(1, 0, 2)


def _load_state_dict(path: Path, device: torch.device) -> Dict[str, Any]:
    try:
        payload = torch.load(path, map_location=device, weights_only=True)
    except TypeError:
        payload = torch.load(path, map_location=device)

    if isinstance(payload, dict):
        for key in ("state_dict", "model_state_dict", "model"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                payload = nested
                break
    if not isinstance(payload, dict):
        raise RuntimeError("Unsupported model checkpoint format")

    cleaned: Dict[str, Any] = {}
    for key, value in payload.items():
        normalized = str(key)
        if normalized.startswith("module."):
            normalized = normalized[7:]
        cleaned[normalized] = value
    return cleaned


def _decode_ctc(output: torch.Tensor) -> Tuple[str, float]:
    probabilities = torch.softmax(output, dim=2)
    max_probabilities, predicted = probabilities.max(dim=2)
    ids = predicted[:, 0].detach().cpu().tolist()
    probs = max_probabilities[:, 0].detach().cpu().tolist()

    chars: list[str] = []
    emitted_probabilities: list[float] = []
    previous = -1
    for index, probability in zip(ids, probs):
        if index != BLANK_LABEL and index != previous:
            char = IDX2CHAR.get(int(index))
            if char:
                chars.append(char)
                emitted_probabilities.append(max(float(probability), 1e-9))
        previous = int(index)

    if not emitted_probabilities:
        confidence = 0.0
    else:
        confidence = math.exp(
            sum(math.log(value) for value in emitted_probabilities)
            / len(emitted_probabilities)
        )
    return "".join(chars), round(confidence, 4)


def _resolve_device() -> Tuple[torch.device, str]:
    policy = str(os.getenv("MAHANBOT_MODEL_DEVICE") or "auto").strip().lower()
    if policy == "cpu":
        return torch.device("cpu"), policy
    if policy.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        return torch.device(policy), policy
    return torch.device("cuda" if torch.cuda.is_available() else "cpu"), "auto"


class OfflineModelLab:
    """Lazy local inference for synthetic or operator-provided test images.

    The result is shown for human review only. This service has no browser page,
    locator or submit callback and cannot populate a live form automatically.
    """

    def __init__(self, model_path: Path | str = MODEL_PATH) -> None:
        self.model_path = Path(model_path)
        self.device, self._device_policy = _resolve_device()
        self._model: Optional[CRNN] = None
        self._load_error: Optional[str] = None
        self._lock = threading.RLock()
        self._sha_cache_key: Optional[Tuple[int, int]] = None
        self._sha_cache_value: Optional[str] = None

    def _model_sha256(self) -> Optional[str]:
        if not self.model_path.is_file():
            return None
        try:
            stat = self.model_path.stat()
        except OSError:
            return None
        key = (int(stat.st_size), int(stat.st_mtime_ns))
        with self._lock:
            if key == self._sha_cache_key:
                return self._sha_cache_value

        digest = hashlib.sha256()
        try:
            with self.model_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        value = digest.hexdigest()
        with self._lock:
            self._sha_cache_key = key
            self._sha_cache_value = value
        return value

    def load(self, *, force: bool = False) -> CRNN:
        with self._lock:
            if self._model is not None and not force:
                return self._model
            self._model = None
            self._load_error = None
            if not self.model_path.is_file():
                self._load_error = f"Model file not found: {self.model_path.name}"
                raise FileNotFoundError(self._load_error)

            try:
                # Load and validate on CPU first. Moving a valid model to the
                # selected accelerator afterwards produces clearer errors.
                cpu = torch.device("cpu")
                model = CRNN(num_chars=len(CHARS))
                state_dict = _load_state_dict(self.model_path, cpu)
                model.load_state_dict(state_dict, strict=True)
                try:
                    model = model.to(self.device)
                except Exception:
                    if self._device_policy != "auto":
                        raise
                    self.device = cpu
                    model = model.to(cpu)
                model.eval()
                self._model = model
                return model
            except Exception as exc:
                self._load_error = str(exc)
                raise

    def status(self, *, load: bool = False) -> Dict[str, Any]:
        if load:
            try:
                self.load()
            except Exception:
                pass
        with self._lock:
            loaded = self._model is not None
            load_error = self._load_error
            device = str(self.device)
        return {
            "available": self.model_path.is_file(),
            "loaded": loaded,
            "device": device,
            "model_file": self.model_path.name,
            "model_sha256": self._model_sha256(),
            "load_error": load_error,
            "scope": "offline-test-only",
            "live_workflow_connected": False,
            "browser_autofill": False,
            "requires_operator_confirmation": True,
        }

    def predict(self, image_bytes: bytes) -> Dict[str, Any]:
        if not image_bytes:
            raise ValueError("Image is empty")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds 2 MB")

        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                source.load()
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
                    raise ValueError("Image dimensions are not accepted")
                image = ImageOps.exif_transpose(source).convert("L").resize(
                    (IMAGE_WIDTH, IMAGE_HEIGHT),
                    Image.Resampling.LANCZOS,
                )
        except ValueError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Unsupported or corrupted image") from exc

        pixels = torch.tensor(bytearray(image.tobytes()), dtype=torch.uint8).to(torch.float32)
        tensor = pixels.reshape(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH).div_(255.0)
        model = self.load()
        with self._lock, torch.inference_mode():
            output = model(tensor.to(self.device, non_blocking=False))
        text, confidence = _decode_ctc(output)
        return {
            "prediction": text,
            "confidence": confidence,
            "device": str(self.device),
            "input_size": [IMAGE_WIDTH, IMAGE_HEIGHT],
            "scope": "offline-test-only",
            "saved": False,
            "live_workflow_connected": False,
            "browser_autofill": False,
            "requires_operator_confirmation": True,
        }

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._load_error = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


OFFLINE_MODEL_LAB = OfflineModelLab()


def install_model_lab(app: FastAPI, auth_dependency: Callable[..., Any]) -> None:
    """Compatibility installer retained for older imports.

    The unified application uses model_lab_api.install_model_lab, but keeping this
    function avoids breaking standalone users of the earlier module.
    """

    if getattr(app.state, "offline_model_lab_installed", False):
        return
    app.state.offline_model_lab_installed = True

    @app.get("/api/v1/model/status")
    def model_status(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        return {"status": "ok", "model": OFFLINE_MODEL_LAB.status(load=False)}

    @app.post("/api/v1/model/load")
    def model_load(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        try:
            OFFLINE_MODEL_LAB.load()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Model could not be loaded: {exc}",
            ) from exc
        return {"status": "ok", "model": OFFLINE_MODEL_LAB.status(load=False)}

    @app.post("/api/v1/model/unload")
    def model_unload(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        OFFLINE_MODEL_LAB.unload()
        return {"status": "ok", "model": OFFLINE_MODEL_LAB.status(load=False)}

    @app.post("/api/v1/model/predict")
    async def model_predict(
        image: UploadFile = File(...),
        _: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        content_type = str(image.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            await image.close()
            raise HTTPException(status_code=415, detail="Only PNG, JPEG, WEBP or BMP images are accepted")
        payload = await image.read(MAX_IMAGE_BYTES + 1)
        await image.close()
        if len(payload) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds 2 MB")
        try:
            result = OFFLINE_MODEL_LAB.predict(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Offline inference failed: {exc}") from exc
        return {"status": "ok", "result": result}
