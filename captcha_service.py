from __future__ import annotations

import io
from typing import Any, Dict, Optional, Tuple


LOCAL_TEST_MODES = frozenset({"local_test", "offline_test", "synthetic_test"})


class CaptchaService:
    """Central CAPTCHA service with a strict live/manual boundary.

    Live workflow modes such as ``general`` and ``firewall`` always return
    ``None`` so browser jobs switch to operator entry. The user-owned model is
    available only through an explicit local-test mode and is lazy-loaded.
    """

    def __init__(
        self,
        model: Any = None,
        ocr_firewall: Any = None,
        *,
        local_model: Any = None,
    ) -> None:
        # Legacy constructor arguments are accepted for compatibility but are
        # never used by live workflows.
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
            # Lazy import keeps the main bot lightweight until the developer
            # explicitly opens the local model test path.
            from offline_model_lab import OFFLINE_MODEL_LAB

            self._local_model = OFFLINE_MODEL_LAB
        return self._local_model

    def predict_local(self, image_bytes: Any) -> Dict[str, Any]:
        """Run the user-owned checkpoint through the bot's core service.

        This method is intended for synthetic or user-owned local test images.
        It never receives a browser page, URL, locator, or submit callback.
        """

        payload = self._normalize_image_bytes(image_bytes)
        result = self._get_local_model().predict(payload)
        if not isinstance(result, dict):
            raise RuntimeError("Local model returned an invalid result")

        normalized = dict(result)
        prediction = str(normalized.get("prediction") or "").strip()
        normalized["prediction"] = prediction
        normalized["integration_route"] = "CaptchaService.local_test"
        normalized["scope"] = "offline-test-only"
        normalized["live_workflow_connected"] = False
        return normalized

    def solve(self, image_bytes: Any, mode: str = "general") -> Optional[str]:
        normalized_mode = str(mode or "general").strip().lower()
        if normalized_mode not in LOCAL_TEST_MODES:
            # Returning None tells all live bots to use the visible manual flow.
            return None
        result = self.predict_local(image_bytes)
        return str(result.get("prediction") or "").strip() or None

    def local_status(self) -> Dict[str, Any]:
        model = self._get_local_model()
        status = model.status(load=False)
        result = dict(status) if isinstance(status, dict) else {}
        result["integration_route"] = "CaptchaService.local_test"
        result["scope"] = "offline-test-only"
        result["live_workflow_connected"] = False
        return result


def load_captcha_resources() -> Tuple[None, None]:
    """Compatibility helper retained for legacy server startup."""
    return None, None
