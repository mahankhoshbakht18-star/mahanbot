from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from captcha_service import CaptchaService


DEFAULT_MODEL_PATH = Path(__file__).resolve().with_name("my_captcha_model.pth")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
}

_SERVICE: Optional[Any] = None
_SERVICE_PATH: Optional[Path] = None
_SERVICE_LOCK = threading.RLock()
_IMPORT_ERROR: Optional[str] = None


def resolve_model_path() -> Path:
    """Return the configured local checkpoint path.

    Relative paths are resolved from the project folder so the Windows one-click
    package can keep ``my_captcha_model.pth`` beside ``unified_server.py``.
    """

    configured = os.getenv("MAHANBOT_CAPTCHA_MODEL_PATH", "").strip()
    if not configured:
        return DEFAULT_MODEL_PATH

    expanded = Path(os.path.expandvars(configured)).expanduser()
    if not expanded.is_absolute():
        expanded = Path(__file__).resolve().parent / expanded
    try:
        return expanded.resolve()
    except OSError:
        return expanded.absolute()


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_service() -> Any:
    global _SERVICE, _SERVICE_PATH, _IMPORT_ERROR
    target_path = resolve_model_path()
    with _SERVICE_LOCK:
        if _SERVICE is not None and _SERVICE_PATH == target_path:
            return _SERVICE
        try:
            from offline_model_lab import OfflineModelLab

            _SERVICE = OfflineModelLab(target_path)
            _SERVICE_PATH = target_path
            _IMPORT_ERROR = None
            return _SERVICE
        except Exception as exc:
            _SERVICE = None
            _SERVICE_PATH = target_path
            _IMPORT_ERROR = str(exc)
            raise RuntimeError(f"Model runtime is unavailable: {exc}") from exc


def _core_adapter() -> CaptchaService:
    return CaptchaService(local_model=_get_service())


def _decorate_status(result: Dict[str, Any], path: Path) -> Dict[str, Any]:
    decorated = dict(result)
    decorated.setdefault("available", path.is_file())
    decorated.setdefault("loaded", False)
    decorated.setdefault("device", "not-loaded")
    decorated.setdefault("model_file", path.name)
    decorated.setdefault("model_sha256", _file_sha256(path))
    decorated.setdefault("load_error", None)
    decorated["model_path"] = str(path)
    decorated["integration_route"] = "CaptchaService.local_test"
    decorated["runtime_connected"] = True
    decorated["scope"] = "offline-test-only"
    decorated["live_workflow_connected"] = False
    return decorated


def _lightweight_status() -> Dict[str, Any]:
    path = resolve_model_path()
    with _SERVICE_LOCK:
        service = _SERVICE if _SERVICE_PATH == path else None
        import_error = _IMPORT_ERROR if _SERVICE_PATH == path else None
    if service is not None:
        status_result = CaptchaService(local_model=service).local_status()
        return _decorate_status(status_result, path)
    return _decorate_status(
        {
            "available": path.is_file(),
            "loaded": False,
            "device": "not-loaded",
            "model_file": path.name,
            "model_sha256": _file_sha256(path),
            "load_error": import_error,
        },
        path,
    )


def warmup_model_lab() -> Dict[str, Any]:
    """Load the local model when present without blocking server availability.

    The unified server calls this in a background thread. A missing or invalid
    checkpoint never prevents the dashboard from starting; the error is exposed
    through the model status endpoint for the operator.
    """

    path = resolve_model_path()
    if not path.is_file():
        return _lightweight_status()
    try:
        service = _get_service()
        service.load()
    except Exception:
        return _lightweight_status()
    return _lightweight_status()


def install_model_lab(app: FastAPI, auth_dependency: Callable[..., Any]) -> None:
    if getattr(app.state, "offline_model_lab_installed", False):
        return
    app.state.offline_model_lab_installed = True

    @app.get("/api/v1/model/status")
    def model_status(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        return {"status": "ok", "model": _lightweight_status()}

    @app.post("/api/v1/model/load")
    def model_load(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        try:
            service = _get_service()
            service.load()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Model could not be loaded: {exc}",
            ) from exc
        return {"status": "ok", "model": _lightweight_status()}

    @app.post("/api/v1/model/unload")
    def model_unload(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        path = resolve_model_path()
        with _SERVICE_LOCK:
            service = _SERVICE if _SERVICE_PATH == path else None
        if service is not None:
            service.unload()
        return {"status": "ok", "model": _lightweight_status()}

    @app.post("/api/v1/model/predict")
    async def model_predict(
        image: UploadFile = File(...),
        _: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        content_type = str(image.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Only PNG, JPEG, WEBP or BMP images are accepted",
            )
        payload = await image.read(MAX_IMAGE_BYTES + 1)
        await image.close()
        if len(payload) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds 2 MB")
        try:
            result = _core_adapter().predict_local(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Offline inference failed: {exc}") from exc
        result["runtime_connected"] = True
        return {"status": "ok", "result": result}
