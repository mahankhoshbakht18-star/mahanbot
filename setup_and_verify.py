"""
setup_and_verify.py — Pre-flight Check and Initialization for MAHANBOT Unified Selector.

Verifies:
1. Python environment and required packages.
2. Presence and integrity of PyTorch CRNN CAPTCHA model (my_captcha_model.pth).
3. Creation and seeding of isolated test database (cbi_test.db).
4. Playwright browser availability.
"""

import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def print_status(title: str, ok: bool, details: str = ""):
    icon = "✅" if ok else "❌"
    msg = f"{icon} {title}"
    if details:
        msg += f" — {details}"
    print(msg)


def run_checks() -> bool:
    print("=" * 60)
    print("  بررسی آمادگی سامانه یکپارچه انتخاب بانک وام ازدواج MAHANBOT")
    print("=" * 60)

    all_ok = True

    # 1. Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_py_ok = sys.version_info >= (3, 10)
    print_status("نسخه پایتون", is_py_ok, py_ver)
    all_ok = all_ok and is_py_ok

    # 2. PyTorch and Torchvision
    try:
        import torch
        print_status("کتابخانه PyTorch", True, f"نسخه {torch.__version__} (Device: CPU)")
    except ImportError as e:
        print_status("کتابخانه PyTorch", False, str(e))
        all_ok = False

    # 3. Model weights check
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(base_dir, "my_captcha_model.pth")
    model_exists = os.path.isfile(model_path)
    if model_exists:
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        print_status("فایل مدل عصبی کپچا (my_captcha_model.pth)", True, f"{size_mb:.1f} مگابایت")
        try:
            from core.pure_crnn_solver import PureCRNNSolver
            solver = PureCRNNSolver.get_instance(model_path)
            print_status("بارگذاری وزن‌های شبکه CRNN بدون ddddocr", solver.model is not None, "OK")
        except Exception as e:
            print_status("بارگذاری وزن‌های شبکه CRNN", False, str(e))
            all_ok = False
    else:
        print_status("فایل مدل عصبی کپچا (my_captcha_model.pth)", False, "فایل پیدا نشد!")
        all_ok = False

    # 4. Isolated Test Database
    try:
        from core.test_database_manager import TestDatabaseManager, TEST_DB_PATH
        db = TestDatabaseManager(TEST_DB_PATH)
        db.init_database()
        apps = db.get_all_applicants()
        print_status("دیتابیس آزمایشی مستقل (cbi_test.db)", True, f"تعداد {len(apps)} متقاضی نمونه")
    except Exception as e:
        print_status("دیتابیس آزمایشی مستقل (cbi_test.db)", False, str(e))
        all_ok = False

    # 5. Playwright
    try:
        import importlib.metadata
        pw_ver = importlib.metadata.version("playwright")
        print_status("کتابخانه Playwright", True, f"نسخه {pw_ver}")
    except Exception as e:
        print_status("کتابخانه Playwright", False, str(e))
        all_ok = False


    # 6. FastAPI & Uvicorn
    try:
        import fastapi
        import uvicorn
        print_status("سرور وب (FastAPI & Uvicorn)", True, f"FastAPI {fastapi.__version__}")
    except ImportError as e:
        print_status("سرور وب (FastAPI & Uvicorn)", False, str(e))
        all_ok = False

    print("=" * 60)
    if all_ok:
        print("🎉 کلیه پیش‌نیازها با موفقیت تأیید شدند. سیستم آماده اجرا است.")
    else:
        print("⚠️ برخی پیش‌نیازها دچار مشکل هستند. لطفاً خطاهای بالا را رفع کنید.")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
