import ddddocr
import cv2
import numpy as np
import torch
import torch.nn as nn
import os

# ================= تنظیمات مدل هوشمند =================
# تنظیماتی که با آن آموزش دادیم (دست نزنید)
IMAGE_WIDTH = 160
IMAGE_HEIGHT = 60
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHAR2IDX = {char: idx + 1 for idx, char in enumerate(CHARS)}
IDX2CHAR = {idx + 1: char for idx, char in enumerate(CHARS)}
BLANK_LABEL = 0 
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# مسیر فایل مدل (فرض بر این است که کنار همین فایل است)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "my_captcha_model.pth")

# ================= ساختار شبکه عصبی (CRNN) =================
class CRNN(nn.Module):
    def __init__(self, num_chars, hidden_size=256):
        super(CRNN, self).__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, 3, 1, 1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(64, 128, 3, 1, 1), nn.BatchNorm2d(128), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(128, 256, 3, 1, 1), nn.BatchNorm2d(256), nn.ReLU(), nn.MaxPool2d((2, 1)),
            nn.Conv2d(256, 512, 3, 1, 1), nn.BatchNorm2d(512), nn.ReLU(), nn.MaxPool2d((2, 1)),
        )
        # محاسبه سایز برای اتصال به RNN
        dummy_input = torch.zeros(1, 1, IMAGE_HEIGHT, IMAGE_WIDTH)
        dummy_output = self.cnn(dummy_input)
        self.linear_input = dummy_output.shape[1] * dummy_output.shape[2]
        
        self.rnn = nn.LSTM(input_size=self.linear_input, hidden_size=hidden_size, bidirectional=True, num_layers=2, batch_first=True)
        self.fc = nn.Linear(hidden_size * 2, num_chars + 1)

    def forward(self, x):
        batch_size = x.size(0)
        x = self.cnn(x)
        x = x.permute(0, 3, 1, 2)
        x = x.view(batch_size, x.size(1), -1)
        x, _ = self.rnn(x)
        x = self.fc(x)
        return x.permute(1, 0, 2)

# ================= کلاس سرویس کپچا =================
class CaptchaService:
    _instance = None
    _model = None       # مدل هوشمند خودمان
    _ocr_firewall = None # برای کپچای فایروال (هنوز از ddddocr استفاده میکنیم چون نوعش فرق دارد)

    def __new__(cls):
        if cls._instance is None:
            print("🧠 در حال راه‌اندازی سرویس کپچا...")
            cls._instance = super(CaptchaService, cls).__new__(cls)
            
            # 1. لود کردن مدل هوشمند (Main Captcha)
            if os.path.exists(MODEL_PATH):
                try:
                    cls._model = CRNN(num_chars=len(CHARS)).to(device)
                    cls._model.load_state_dict(torch.load(MODEL_PATH, map_location=device))
                    cls._model.eval()
                    print(f"✅ مدل هوشمند بارگذاری شد: {MODEL_PATH}")
                except Exception as e:
                    print(f"❌ خطا در لود مدل هوشمند: {e}")
            else:
                print(f"⚠️ هشدار: فایل مدل پیدا نشد ({MODEL_PATH}).")

            # 2. لود کردن حل‌کننده فایروال (ddddocr)
            # فایروال کپچای متفاوتی دارد، پس فعلا با همین روش قدیمی حلش میکنیم
            cls._ocr_firewall = ddddocr.DdddOcr(show_ad=False, beta=True)
            
        return cls._instance

    def solve(self, image_bytes, mode='general'):
        """
        تابع اصلی که توسط ربات صدا زده می‌شود
        """
        if not image_bytes:
            return None

        try:
            # اگر کپچای اصلی سایت باشد -> استفاده از مدل هوشمند خودمان
            if mode == 'general':
                return self._solve_with_my_model(image_bytes)
            
            # اگر کپچای فایروال باشد -> استفاده از ddddocr + پردازش تصویر
            elif mode == 'firewall':
                return self._solve_firewall_local(image_bytes)
                
        except Exception as e:
            print(f"❌ خطا در حل کپچا ({mode}): {e}")
            return None

    def _solve_with_my_model(self, image_bytes):
        """استفاده از مدل PyTorch آموزش دیده"""
        if self._model is None:
            print("❌ مدل لود نشده است!")
            return None

        # پیش‌پردازش عکس (دقیقاً مثل زمان آموزش)
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        if img is None: return None
        
        img = cv2.resize(img, (IMAGE_WIDTH, IMAGE_HEIGHT))
        img = img / 255.0
        img = torch.tensor(img, dtype=torch.float32).unsqueeze(0).unsqueeze(0) # [1, 1, H, W]
        img = img.to(device)
        
        # پیش‌بینی
        with torch.no_grad():
            output = self._model(img)
            text = self._decode_prediction(output)
        
        print(f"🤖 مدل خواند: {text}")
        return text

    def _decode_prediction(self, text_batch):
        """تبدیل خروجی مدل به متن"""
        text_batch = text_batch.permute(1, 0, 2)
        text_batch = torch.argmax(text_batch, dim=2)
        decoded_texts = []
        for i in range(text_batch.size(0)):
            chars = []
            prev_char = -1
            for idx in text_batch[i]:
                idx = idx.item()
                if idx != BLANK_LABEL and idx != prev_char:
                    chars.append(IDX2CHAR[idx])
                prev_char = idx
            decoded_texts.append("".join(chars))
        return decoded_texts[0]

    def _solve_firewall_local(self, image_bytes):
        """حل فایروال (کپچای متفاوت)"""
        try:
            nparr = np.frombuffer(image_bytes, np.uint8)
            im = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            final_bytes = cv2.imencode('.png', cv2.bitwise_not(binary))[1].tobytes()
            
            res = self._ocr_firewall.classification(final_bytes)
            print(f"🛡️ فایروال حل شد: {res}")
            return res if res.strip() else None
        except:
            return None