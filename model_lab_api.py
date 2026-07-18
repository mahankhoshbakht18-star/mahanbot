from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status


MODEL_PATH = Path(__file__).resolve().with_name("my_captcha_model.pth")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
}

_SERVICE: Optional[Any] = None
_SERVICE_LOCK = threading.RLock()
_IMPORT_ERROR: Optional[str] = None


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_service() -> Any:
    global _SERVICE, _IMPORT_ERROR
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            return _SERVICE
        try:
            from offline_model_lab import OfflineModelLab

            _SERVICE = OfflineModelLab(MODEL_PATH)
            _IMPORT_ERROR = None
            return _SERVICE
        except Exception as exc:
            _IMPORT_ERROR = str(exc)
            raise RuntimeError(f"Model runtime is unavailable: {exc}") from exc


def _lightweight_status() -> Dict[str, Any]:
    with _SERVICE_LOCK:
        service = _SERVICE
        import_error = _IMPORT_ERROR
    if service is not None:
        return service.status(load=False)
    return {
        "available": MODEL_PATH.is_file(),
        "loaded": False,
        "device": "not-loaded",
        "model_file": MODEL_PATH.name,
        "model_sha256": None,
        "load_error": import_error,
        "scope": "offline-test-only",
        "live_workflow_connected": False,
    }


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
        model_status = service.status(load=False)
        if not model_status.get("model_sha256"):
            model_status["model_sha256"] = _file_sha256(MODEL_PATH)
        return {"status": "ok", "model": model_status}

    @app.post("/api/v1/model/unload")
    def model_unload(_: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        with _SERVICE_LOCK:
            service = _SERVICE
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
            result = _get_service().predict(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Offline inference failed: {exc}") from exc
        return {"status": "ok", "result": result}
