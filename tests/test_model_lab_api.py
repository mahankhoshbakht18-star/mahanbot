import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_lab_api


class ModelLabApiTests(unittest.TestCase):
    def test_configured_absolute_model_path_is_used(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = Path(temp_dir) / "custom-model.pth"
            with patch.dict(
                os.environ,
                {"MAHANBOT_CAPTCHA_MODEL_PATH": str(expected)},
                clear=False,
            ):
                self.assertEqual(model_lab_api.resolve_model_path(), expected.resolve())

    def test_relative_model_path_is_resolved_from_project_folder(self):
        with patch.dict(
            os.environ,
            {"MAHANBOT_CAPTCHA_MODEL_PATH": "models/custom-model.pth"},
            clear=False,
        ):
            expected = Path(model_lab_api.__file__).resolve().parent / "models" / "custom-model.pth"
            self.assertEqual(model_lab_api.resolve_model_path(), expected.resolve())

    def test_missing_model_does_not_break_warmup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-model.pth"
            with patch.dict(
                os.environ,
                {"MAHANBOT_CAPTCHA_MODEL_PATH": str(missing)},
                clear=False,
            ):
                result = model_lab_api.warmup_model_lab()

        self.assertFalse(result["available"])
        self.assertFalse(result["loaded"])
        self.assertEqual(result["integration_route"], "CaptchaService.local_test")
        self.assertFalse(result["live_workflow_connected"])
        self.assertFalse(result["browser_autofill"])
        self.assertTrue(result["requires_operator_confirmation"])

    def test_status_decorates_operator_review_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.pth"
            result = model_lab_api._decorate_status({"loaded": False}, path)

        self.assertEqual(result["scope"], "offline-test-only")
        self.assertFalse(result["live_workflow_connected"])
        self.assertFalse(result["browser_autofill"])
        self.assertTrue(result["requires_operator_confirmation"])
        self.assertEqual(result["model_path"], str(path))


if __name__ == "__main__":
    unittest.main()
