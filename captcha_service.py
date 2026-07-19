from __future__ import annotations

import io
from typing import Any, Dict, Optional, Tuple


LOCAL_TEST_MODES = frozenset({"local_test", "offline_test", "synthetic_test"})


class CaptchaService:
    """Manual-only CAPTCHA boundary for live workflows plus a local model adapter.

    Live workflow modes always return ``None`` so browser jobs remain on the
    visible operator-entry path. The local checkpoint can be exercised only by
    the explicit test/review methods, which have no page or submit callback.
    """

    def __init__(
        self,
        model: Any = None,
        ocr_firewall: Any = None,
        *,
        local_model: Any = None,
    ) -> None:
        # Legacy constructor arguments are accepted for compatibility but are
        # intentionally not used by live workflows.
        self._model = None
        self._ocr_firewall = None
        self._local_model = local_model

    @staticmethod
    def _normalize_image_bytes(image_bytes: Any) -> bytes:
        if image_bytes is None:
            raise ValueError("Image is empty")
        if isinstance(image_bytes, (bytes, bytearray, memoryview)):
            payload = bytes(image_bytes)
        elif hasattr(image_bytes, "read"):
            payload = bytes(image_bytes.read())
        else:
            try:
                payload = io.BytesIO(image_bytes).getvalue()
            except Exception as exc:
                raise ValueError("Unsupported image input") from exc
        if not payload:
            raise ValueError("Image is empty")
        return payload

    def _get_local_model(self) -> Any:
        if self._local_model is None:
            # Lazy import keeps the main bot available even when the optional
            # model runtime is not installed yet.
            from offline_model_lab import OFFLINE_MODEL_LAB

            self._local_model = OFFLINE_MODEL_LAB
        return self._local_model

    @staticmethod
    def _decorate_review_boundary(result: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(result)
        normalized["prediction"] = str(normalized.get("prediction") or "").strip()
        normalized["integration_route"] = "CaptchaService.local_test"
        normalized["scope"] = "offline-test-only"
        normalized["live_workflow_connected"] = False
        normalized["browser_autofill"] = False
        normalized["requires_operator_confirmation"] = True
        return normalized

    def predict_local(self, image_bytes: Any) -> Dict[str, Any]:
        """Run a synthetic or operator-provided image through the local model."""

        payload = self._normalize_image_bytes(image_bytes)
        result = self._get_local_model().predict(payload)
        if not isinstance(result, dict):
            raise RuntimeError("Local model returned an invalid result")
        return self._decorate_review_boundary(result)

    def solve(self, image_bytes: Any, mode: str = "general") -> Optional[str]:
        normalized_mode = str(mode or "general").strip().lower()
        if normalized_mode not in LOCAL_TEST_MODES:
            # Returning None tells all live bots to use visible operator entry.
            return None
        result = self.predict_local(image_bytes)
        return str(result.get("prediction") or "").strip() or None

    def local_status(self) -> Dict[str, Any]:
        model = self._get_local_model()
        status = model.status(load=False)
        result = dict(status) if isinstance(status, dict) else {}
        return self._decorate_review_boundary(result)


def load_captcha_resources() -> Tuple[None, None]:
    """Compatibility helper retained for legacy server startup."""

    return None, None
