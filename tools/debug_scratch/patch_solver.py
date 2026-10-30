filepath = 'bot_select_captcha.py'
with open(filepath, 'r', encoding='utf-8', newline='') as f:
    content = f.read()

nl = '\r\n' if '\r\n' in content else '\n'

target = """        try:
            model = self.load_model()
            modes = ["raw", "contrast", "binarize"] if use_enhanced else ["raw"]
            for mode in modes:
                try:
                    tensor = self.preprocess(image_source, enhance=mode)
                    with self._lock, torch.inference_mode():
                        logits = model(tensor)
                        prediction, confidence = self.decode(logits)

                    prediction = "".join(char for char in prediction.upper() if char in CHARS)
                    if not prediction:
                        continue

                    if validate_captcha_prediction(prediction, 4, 8):
                        if confidence >= min_confidence:
                            return prediction
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_prediction = prediction
                except Exception:
                    continue
        except Exception as exc:
            self.last_error = str(exc)

        # Auxiliary local engine fallback if CRNN alone did not yield a high-confidence code
        try:
            ocr = self._get_ddddocr()
            if ocr is not None:
                img_bytes = None
                if isinstance(image_source, (bytes, bytearray, memoryview)):
                    img_bytes = bytes(image_source)
                elif isinstance(image_source, Image.Image):
                    buf = io.BytesIO()
                    image_source.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                elif isinstance(image_source, (str, os.PathLike)) and os.path.exists(image_source):
                    with open(image_source, "rb") as f:
                        img_bytes = f.read()
                if img_bytes:
                    raw = str(ocr.classification(img_bytes) or "").strip().upper()
                    clean = "".join(c for c in raw if c in CHARS)
                    if validate_captcha_prediction(clean, 4, 8):
                        self.last_error = None
                        return clean
        except Exception:
            pass

        if best_prediction and validate_captcha_prediction(best_prediction, 4, 8):
            return best_prediction

        return None"""

replacement = """        def _try_auxiliary_fallback():
            try:
                ocr = self._get_ddddocr()
                if ocr is not None:
                    img_bytes = None
                    if isinstance(image_source, (bytes, bytearray, memoryview)):
                        img_bytes = bytes(image_source)
                    elif isinstance(image_source, Image.Image):
                        buf = io.BytesIO()
                        image_source.save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                    elif isinstance(image_source, (str, os.PathLike)) and os.path.exists(image_source):
                        with open(image_source, "rb") as f:
                            img_bytes = f.read()
                    if img_bytes:
                        raw = str(ocr.classification(img_bytes) or "").strip().upper()
                        clean = "".join(c for c in raw if c in CHARS)
                        if validate_captcha_prediction(clean, 4, 8):
                            self.last_error = None
                            return clean
            except Exception:
                pass
            return None

        try:
            model = self.load_model()
            modes = ["raw", "contrast", "binarize"] if use_enhanced else ["raw"]
            for idx, mode in enumerate(modes):
                try:
                    tensor = self.preprocess(image_source, enhance=mode)
                    with self._lock, torch.inference_mode():
                        logits = model(tensor)
                        prediction, confidence = self.decode(logits)

                    prediction = "".join(char for char in prediction.upper() if char in CHARS)
                    if not prediction:
                        continue

                    if validate_captcha_prediction(prediction, 4, 8):
                        if confidence >= min_confidence:
                            return prediction
                        if confidence > best_confidence:
                            best_confidence = confidence
                            best_prediction = prediction
                except Exception:
                    continue

                # If first CRNN attempt has low confidence, invoke fallback before repeating extra modes
                if idx == 0 and best_prediction and best_confidence < min_confidence:
                    fallback_code = _try_auxiliary_fallback()
                    if fallback_code:
                        return fallback_code
        except Exception as exc:
            self.last_error = str(exc)

        # Auxiliary local engine fallback if CRNN failed or didn't trigger above
        fallback_code = _try_auxiliary_fallback()
        if fallback_code:
            return fallback_code

        if best_prediction and validate_captcha_prediction(best_prediction, 4, 8):
            return best_prediction

        return None"""

t_conv = target.replace('\r\n', '\n').replace('\n', nl)
r_conv = replacement.replace('\r\n', '\n').replace('\n', nl)

assert t_conv in content, 'Target not found in bot_select_captcha.py'
new_content = content.replace(t_conv, r_conv, 1)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(new_content)
print('Successfully patched ModelCaptchaSolver.solve with early fallback')
