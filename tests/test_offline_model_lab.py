import io
import unittest

try:
    import torch
    from PIL import Image

    from offline_model_lab import BLANK_LABEL, CHARS, OfflineModelLab, _decode_ctc

    MODEL_RUNTIME_AVAILABLE = True
except ImportError:
    torch = None
    Image = None
    BLANK_LABEL = 0
    CHARS = ""
    OfflineModelLab = None
    _decode_ctc = None
    MODEL_RUNTIME_AVAILABLE = False


@unittest.skipUnless(MODEL_RUNTIME_AVAILABLE, "offline model runtime is not installed in lightweight CI")
class OfflineModelLabTests(unittest.TestCase):
    def test_ctc_decode_collapses_repeats_and_blank(self):
        classes = len(CHARS) + 1
        output = torch.full((6, 1, classes), -12.0)
        a_index = CHARS.index("A") + 1
        b_index = CHARS.index("B") + 1
        sequence = [a_index, a_index, BLANK_LABEL, b_index, b_index, BLANK_LABEL]
        for timestep, index in enumerate(sequence):
            output[timestep, 0, index] = 12.0

        text, confidence = _decode_ctc(output)

        self.assertEqual(text, "AB")
        self.assertGreater(confidence, 0.99)

    def test_invalid_image_is_rejected_before_model_load(self):
        lab = OfflineModelLab()
        with self.assertRaises(ValueError):
            lab.predict(b"not-an-image")

    def test_valid_image_reaches_model_loading_boundary(self):
        image = Image.new("L", (160, 60), color=255)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        lab = OfflineModelLab(model_path="missing-test-model.pth")

        with self.assertRaises(FileNotFoundError):
            lab.predict(stream.getvalue())

    def test_status_marks_live_workflow_disconnected(self):
        status = OfflineModelLab(model_path="missing-test-model.pth").status(load=False)
        self.assertFalse(status["live_workflow_connected"])
        self.assertEqual(status["scope"], "offline-test-only")


if __name__ == "__main__":
    unittest.main()
