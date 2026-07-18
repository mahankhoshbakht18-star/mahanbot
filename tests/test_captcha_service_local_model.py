import unittest

from captcha_service import CaptchaService


class FakeLocalModel:
    def __init__(self):
        self.calls = 0

    def predict(self, payload):
        self.calls += 1
        return {
            "prediction": "AB12",
            "confidence": 0.98,
            "device": "cpu",
            "saved": False,
        }

    def status(self, load=False):
        return {
            "available": True,
            "loaded": True,
            "device": "cpu",
        }


class CaptchaServiceLocalModelTests(unittest.TestCase):
    def test_live_modes_never_call_local_model(self):
        model = FakeLocalModel()
        service = CaptchaService(local_model=model)

        self.assertIsNone(service.solve(b"image", mode="general"))
        self.assertIsNone(service.solve(b"image", mode="firewall"))
        self.assertEqual(model.calls, 0)

    def test_local_test_mode_uses_core_adapter(self):
        model = FakeLocalModel()
        service = CaptchaService(local_model=model)

        prediction = service.solve(b"image", mode="local_test")

        self.assertEqual(prediction, "AB12")
        self.assertEqual(model.calls, 1)

    def test_predict_local_adds_integration_metadata(self):
        service = CaptchaService(local_model=FakeLocalModel())

        result = service.predict_local(b"image")

        self.assertEqual(result["prediction"], "AB12")
        self.assertEqual(result["integration_route"], "CaptchaService.local_test")
        self.assertEqual(result["scope"], "offline-test-only")
        self.assertFalse(result["live_workflow_connected"])

    def test_empty_image_is_rejected(self):
        service = CaptchaService(local_model=FakeLocalModel())
        with self.assertRaises(ValueError):
            service.predict_local(b"")

    def test_local_status_marks_core_route(self):
        service = CaptchaService(local_model=FakeLocalModel())

        status = service.local_status()

        self.assertEqual(status["integration_route"], "CaptchaService.local_test")
        self.assertFalse(status["live_workflow_connected"])


if __name__ == "__main__":
    unittest.main()
