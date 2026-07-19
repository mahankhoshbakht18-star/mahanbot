from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from captcha_service import CaptchaService


DEFAULT_MODEL_PATH = Path(__file__).resolve().with_name("my_captcha_model.pth")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "application/octet-stream",
}

_SERVICE: Optional[Any] = None
_SERVICE_PATH: Optional[Path] = None
_SERVICE_LOCK = threading.RLock()
_IMPORT_ERROR: Optional[str] = None
_HASH_LOCK = threading.RLock()
_HASH_CACHE_KEY: Optional[Tuple[str, int, int]] = None
_HASH_CACHE_VALUE: Optional[str] = None


def resolve_model_path() -> Path:
    """Resolve the user-owned checkpoint from env or the project directory."""

    configured = str(os.getenv("MAHANBOT_CAPTCHA_MODEL_PATH") or "").strip()
    if not configured:
        return DEFAULT_MODEL_PATH

    expanded = Path(os.path.expandvars(configured)).expanduser()
    if not expanded.is_absolute():
        expanded = Path(__file__).resolve().parent / expanded
    try:
        return expanded.resolve(strict=False)
    except OSError:
        return expanded.absolute()


def _normalized_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _file_sha256(path: Path) -> Optional[str]:
    global _HASH_CACHE_KEY, _HASH_CACHE_VALUE
    if not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    with _HASH_LOCK:
        if key == _HASH_CACHE_KEY:
            return _HASH_CACHE_VALUE

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    value = digest.hexdigest()
    with _HASH_LOCK:
        _HASH_CACHE_KEY = key
        _HASH_CACHE_VALUE = value
    return value


def _runtime_dependencies_present() -> bool:
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("PIL") is not None


def _get_service() -> Any:
    global _SERVICE, _SERVICE_PATH, _IMPORT_ERROR
    target_path = resolve_model_path()
    with _SERVICE_LOCK:
        if _SERVICE is not None and _SERVICE_PATH == target_path:
            return _SERVICE
        previous = _SERVICE
        try:
            from offline_model_lab import OFFLINE_MODEL_LAB, OfflineModelLab

            shared_path = _normalized_path(Path(OFFLINE_MODEL_LAB.model_path))
            service = OFFLINE_MODEL_LAB if shared_path == target_path else OfflineModelLab(target_path)
            _SERVICE = service
            _SERVICE_PATH = target_path
            _IMPORT_ERROR = None
        except Exception as exc:
            _SERVICE = None
            _SERVICE_PATH = target_path
            _IMPORT_ERROR = str(exc)
            raise RuntimeError(f"Model runtime is unavailable: {exc}") from exc

    if previous is not None and previous is not _SERVICE:
        try:
            previous.unload()
        except Exception:
            pass
    return _SERVICE


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
    decorated["runtime_connected"] = bool(_runtime_dependencies_present() and not decorated.get("runtime_error"))
    decorated["shared_with_bot_core"] = True
    decorated["scope"] = "offline-test-only"
    decorated["live_workflow_connected"] = False
    decorated["browser_autofill"] = False
    decorated["requires_operator_confirmation"] = True
    return decorated


def _lightweight_status() -> Dict[str, Any]:
    path = resolve_model_path()
    with _SERVICE_LOCK:
        service = _SERVICE if _SERVICE_PATH == path else None
        import_error = _IMPORT_ERROR if _SERVICE_PATH == path else None

    if service is not None:
        try:
            status_result = CaptchaService(local_model=service).local_status()
        except Exception as exc:
            status_result = {
                "available": path.is_file(),
                "loaded": False,
                "device": "error",
                "model_file": path.name,
                "load_error": str(exc),
                "runtime_error": str(exc),
            }
        return _decorate_status(status_result, path)

    return _decorate_status(
        {
            "available": path.is_file(),
            "loaded": False,
            "device": "not-loaded",
            "model_file": path.name,
            "model_sha256": _file_sha256(path),
            "load_error": import_error,
            "runtime_error": import_error,
        },
        path,
    )


def warmup_model_lab() -> Dict[str, Any]:
    """Load the shared checkpoint in a background thread without blocking UI."""

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
        content_type = str(image.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            await image.close()
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only PNG, JPEG, WEBP or BMP images are accepted",
            )

        payload = await image.read(MAX_IMAGE_BYTES + 1)
        await image.close()
        if len(payload) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image exceeds 2 MB",
            )
        try:
            result = _core_adapter().predict_local(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Offline inference failed: {exc}") from exc

        result["runtime_connected"] = True
        result["shared_with_bot_core"] = True
        result["browser_autofill"] = False
        result["requires_operator_confirmation"] = True
        return {"status": "ok", "result": result}
