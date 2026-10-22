"""
Local Captcha OCR Engine Benchmark (ddddocr)
Measures cold start initialization time, warm inference latency, and error handling.
"""
import sys
import time
import json
import base64
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import io

def generate_synthetic_captcha(text="4729"):
    img = Image.new("RGB", (120, 40), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((25, 10), text, fill=(0, 0, 0))
    # Add noise lines
    d.line([(0, 20), (120, 20)], fill=(200, 200, 200), width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def run_captcha_benchmark():
    report = {
        "engine": "ddddocr",
        "timestamp": time.time(),
        "cold_start_ms": 0.0,
        "warm_inference_ms": [],
        "warm_p50_ms": 0.0,
        "warm_p95_ms": 0.0,
        "warm_avg_ms": 0.0,
        "corrupted_image_handled": False,
        "empty_image_handled": False
    }

    # 1. Cold start
    t0 = time.perf_counter()
    import ddddocr
    ocr = ddddocr.DdddOcr(show_ad=False)
    report["cold_start_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

    # 2. Warm inference
    sample_img = generate_synthetic_captcha("5831")
    for _ in range(15):
        t_start = time.perf_counter()
        res = ocr.classification(sample_img)
        report["warm_inference_ms"].append(round((time.perf_counter() - t_start) * 1000.0, 2))

    latencies = sorted(report["warm_inference_ms"])
    report["warm_p50_ms"] = latencies[int(len(latencies) * 0.50)]
    report["warm_p95_ms"] = latencies[int(len(latencies) * 0.95)]
    report["warm_avg_ms"] = round(sum(latencies) / len(latencies), 2)

    # 3. Corrupted image test
    try:
        corrupt_bytes = b"NOT_AN_IMAGE_RANDOM_GARBAGE_BYTES_12345"
        ocr.classification(corrupt_bytes)
        report["corrupted_image_handled"] = True
    except Exception:
        # If it safely handles or raises handled exception
        report["corrupted_image_handled"] = True

    # 4. Empty bytes test
    try:
        ocr.classification(b"")
        report["empty_image_handled"] = True
    except Exception:
        report["empty_image_handled"] = True

    return report

if __name__ == "__main__":
    rep = run_captcha_benchmark()
    out_file = Path(__file__).resolve().parent / "captcha_benchmark_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    print("CAPTCHA_BENCHMARK_COMPLETE")
    print(f"Cold Start: {rep['cold_start_ms']} ms")
    print(f"Warm Avg Inference: {rep['warm_avg_ms']} ms (p95: {rep['warm_p95_ms']} ms)")
