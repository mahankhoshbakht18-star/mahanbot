"""
packaging/build_exe.py — PyInstaller automation script for MAHANBOT.
Packages the entire FastAPI + Playwright + PyTorch stack into a standalone Windows distribution.
"""
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_package():
    os.chdir(BASE_DIR)
    print("=== MAHANBOT Windows Packaging ===")
    print(f"Base Directory: {BASE_DIR}")

    # Ensure clean DB template exists
    template_db = os.path.join(BASE_DIR, "cbi_clean_template.db")
    if not os.path.exists(template_db):
        print("Generating cbi_clean_template.db...")
        from scripts.create_clean_distribution_db import generate_clean_db
        generate_clean_db(template_db)

    # PyInstaller arguments
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=MAHANBOT",
        f"--add-data=static{os.pathsep}static",
        f"--add-data=my_captcha_model.pth{os.pathsep}.",
        f"--add-data=cbi_clean_template.db{os.pathsep}.",
        f"--add-data=.env.example{os.pathsep}.",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=aiosqlite",
        "--hidden-import=playwright",
        "--hidden-import=torch",
        "--hidden-import=PIL",
        "--hidden-import=numpy",
        "server.py",
    ]

    print("Running command:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("\n=== Build Completed Successfully! ===")
        print(f"Output folder: {os.path.join(BASE_DIR, 'dist', 'MAHANBOT')}")
    else:
        print("\n[ERROR] PyInstaller build failed with code:", result.returncode)


if __name__ == "__main__":
    build_package()
