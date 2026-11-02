"""
core/pure_crnn_solver.py — Pure PyTorch CRNN CAPTCHA Solver for MAHANBOT.

Loads `my_captcha_model.pth` and runs local CPU inference.
Completely free of ddddocr or any third-party OCR libraries.
"""

import io
import logging
import os
import threading
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("mahanbot.pure_crnn")

IMAGE_WIDTH = 160
IMAGE_HEIGHT = 60
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BLANK_LABEL = 0
IDX2CHAR = {index + 1: char for index, char in enumerate(CHARS)}


class CRNN(nn.Module):
    """CRNN architecture matching the trained my_captcha_model.pth weights."""

    def __init__(self, num_chars: int = len(CHARS), hidden_size: int = 256):
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
        self.rnn = nn.LSTM(
            input_size=512 * 3,
            hidden_size=hidden_size,
            bidirectional=True,
            num_layers=2,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.cnn(x)
        features = features.permute(0, 3, 1, 2)
        features = features.reshape(features.size(0), features.size(1), -1)
        recurrent, _ = self.rnn(features)
        b, t, h = recurrent.size()
        output = self.fc(recurrent.reshape(b * t, h))
        return output.reshape(b, t, -1).permute(1, 0, 2)


class PureCRNNSolver:
    """Thread-safe, standalone CRNN CAPTCHA solver."""

    _INSTANCE: Optional["PureCRNNSolver"] = None
    _LOCK = threading.RLock()

    def __init__(self, model_path: Optional[str] = None):
        if model_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, "my_captcha_model.pth")
        self.model_path = model_path
        self.device = torch.device("cpu")
        self.model: Optional[CRNN] = None
        self._load_model()

    @classmethod
    def get_instance(cls, model_path: Optional[str] = None) -> "PureCRNNSolver":
        with cls._LOCK:
            if cls._INSTANCE is None:
                cls._INSTANCE = cls(model_path)
            return cls._INSTANCE

    def _load_model(self):
        if not os.path.isfile(self.model_path):
            raise FileNotFoundError(f"Model file not found: {self.model_path}")

        model = CRNN(num_chars=len(CHARS)).to(self.device)
        try:
            checkpoint = torch.load(self.model_path, map_location=self.device, weights_only=True)
        except TypeError:
            checkpoint = torch.load(self.model_path, map_location=self.device)

        if isinstance(checkpoint, dict):
            for key in ("model_state_dict", "state_dict"):
                if key in checkpoint and isinstance(checkpoint[key], dict):
                    checkpoint = checkpoint[key]
                    break

        if not isinstance(checkpoint, dict):
            raise ValueError("Invalid model checkpoint format")

        state_dict = {
            (k[7:] if k.startswith("module.") else k): v
            for k, v in checkpoint.items()
        }
        model.load_state_dict(state_dict, strict=True)
        model.eval()
        self.model = model
        logger.info(f"CRNN CAPTCHA model loaded successfully from {self.model_path}")

    @staticmethod
    def _open_image(source: Union[bytes, bytearray, str, Image.Image]) -> Image.Image:
        if isinstance(source, Image.Image):
            img = source.copy()
        elif isinstance(source, (bytes, bytearray)):
            img = Image.open(io.BytesIO(bytes(source)))
        elif isinstance(source, str):
            img = Image.open(source)
        else:
            raise TypeError("Unsupported image source")

        # Handle alpha channel: composite over white background
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            return bg
        elif img.mode not in ("RGB", "L"):
            return img.convert("RGB")
        return img

    @staticmethod
    def _enhance_image(image: Image.Image, mode: str = "raw") -> Image.Image:
        img_gray = image.convert("L")
        if mode == "raw":
            return img_gray
        if mode == "contrast":
            img_contrast = ImageOps.autocontrast(img_gray, cutoff=1)
            return img_contrast.filter(ImageFilter.SHARPEN)
        if mode == "binarize":
            img_contrast = ImageOps.autocontrast(img_gray, cutoff=2)
            arr = np.asarray(img_contrast, dtype=np.uint8)
            thresh = int(np.mean(arr)) if arr.size else 128
            return img_contrast.point(lambda x: 255 if x > thresh else 0, "1").convert("L")
        return img_gray

    def preprocess(self, img: Image.Image, mode: str = "raw") -> torch.Tensor:
        enhanced = self._enhance_image(img, mode=mode)
        resampling = getattr(Image, "Resampling", Image)
        resized = enhanced.resize((IMAGE_WIDTH, IMAGE_HEIGHT), resampling.BILINEAR)
        pixels = torch.tensor(list(resized.getdata()), dtype=torch.float32)
        return pixels.reshape(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH).div_(255.0).to(self.device)

    @staticmethod
    def decode(logits: torch.Tensor) -> Tuple[str, float]:
        probs = F.softmax(logits, dim=2)
        max_probs, indices = torch.max(probs, dim=2)
        indices = indices.permute(1, 0)
        max_probs = max_probs.permute(1, 0)

        decoded = []
        confidences = []

        for seq_idx in range(indices.shape[0]):
            previous = -1
            chars = []
            seq_conf = []

            for time_idx in range(indices.shape[1]):
                idx = int(indices[seq_idx, time_idx].item())
                prob = float(max_probs[seq_idx, time_idx].item())

                if idx != BLANK_LABEL and idx != previous:
                    char = IDX2CHAR.get(idx)
                    if char:
                        chars.append(char)
                        seq_conf.append(prob)
                previous = idx

            decoded.append("".join(chars))
            avg_conf = sum(seq_conf) / len(seq_conf) if seq_conf else 0.0
            confidences.append(avg_conf)

        return (decoded[0] if decoded else "", confidences[0] if confidences else 0.0)

    def solve(
        self,
        image_source: Union[bytes, bytearray, str, Image.Image],
        min_confidence: float = 0.35,
    ) -> Optional[str]:
        """
        Solve CAPTCHA purely using the local CRNN model.
        Tries raw, contrast, and binarized modes and selects highest confidence valid prediction.
        """
        if self.model is None:
            self._load_model()

        img = self._open_image(image_source)

        # Check if inverted background (e.g. dark mode)
        g_lum = img.convert("L")
        mean_lum = sum(g_lum.resize((16, 16)).getdata()) / 256.0
        active_source = ImageOps.invert(img.convert("RGB")) if mean_lum < 125 else img

        best_pred = None
        best_conf = -1.0

        for mode in ("raw", "contrast", "binarize"):
            try:
                tensor = self.preprocess(active_source, mode=mode)
                with torch.no_grad():
                    logits = self.model(tensor)
                    pred, conf = self.decode(logits)

                pred_clean = "".join(c for c in pred.upper() if c in CHARS)
                if 4 <= len(pred_clean) <= 8 and pred_clean.isalnum():
                    if conf > best_conf:
                        best_conf = conf
                        best_pred = pred_clean
                        if conf >= min_confidence:
                            return best_pred
            except Exception as e:
                logger.debug(f"CRNN mode {mode} failed: {e}")
                continue

        if best_pred and len(best_pred) >= 4:
            return best_pred
        return None
