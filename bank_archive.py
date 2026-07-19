from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from database import DBHandler


ROOT = Path(__file__).resolve().parent
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
SAFE_NID = re.compile(r"[^0-9]+")


def sanitize_nid(value: object) -> str:
    text = str(value or "").strip()
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = text.translate(translation)
    return SAFE_NID.sub("", text)[:32]


def sanitize_filename(value: object, fallback: str = "bank") -> str:
    text = str(value or "").strip()
    text = INVALID_FILENAME_CHARS.sub("-", text)
    text = re.sub(r"\s+", "_", text).strip(" ._-")
    return (text or fallback)[:90]


class BankArchiveStore:
    """Local-only archive for applicant bank pages.

    Each snapshot is written below ``archives/<nid>/banks`` as PNG, HTML and
    JSON metadata. Paths stored in the applicant record are relative to the
    archive root so moving the project folder does not break the archive.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        configured = os.getenv("MAHANBOT_ARCHIVE_DIR", "").strip()
        self.root = Path(root or configured or (ROOT / "archives")).expanduser().resolve()
        self._lock = threading.RLock()

    def applicant_dir(self, nid: object) -> Path:
        safe_nid = sanitize_nid(nid)
        if not safe_nid:
            raise ValueError("Invalid applicant national ID")
        target = (self.root / safe_nid / "banks").resolve()
        target.relative_to(self.root)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def capture(
        self,
        page: Any,
        nid: object,
        bank_name: object,
        *,
        stage: str,
        available_banks: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_nid = sanitize_nid(nid)
        bank_label = str(bank_name or "بانک").strip() or "بانک"
        safe_bank = sanitize_filename(bank_label, "bank")
        safe_stage = sanitize_filename(stage, "page")
        now = time.time()
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        base_name = f"{safe_bank}_{safe_stage}_{stamp}"

        with self._lock:
            folder = self.applicant_dir(safe_nid)
            screenshot_path = folder / f"{base_name}.png"
            html_path = folder / f"{base_name}.html"
            metadata_path = folder / f"{base_name}.json"

            screenshot_saved = False
            html_saved = False
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
                screenshot_saved = screenshot_path.is_file()
            except Exception:
                screenshot_saved = False

            try:
                html = page.content()
                html_path.write_text(str(html or ""), encoding="utf-8")
                html_saved = True
            except Exception:
                html_saved = False

            page_url = ""
            page_title = ""
            try:
                page_url = str(page.url or "")
            except Exception:
                page_url = ""
            try:
                page_title = str(page.title() or "")
            except Exception:
                page_title = ""

            record: Dict[str, Any] = {
                "id": base_name,
                "nid": safe_nid,
                "bank": bank_label,
                "stage": str(stage or "page"),
                "created_at": now,
                "created_at_iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                "url": page_url,
                "title": page_title,
                "available_banks": [str(item) for item in (available_banks or [])],
                "screenshot": self._relative(screenshot_path) if screenshot_saved else None,
                "html": self._relative(html_path) if html_saved else None,
                "metadata": self._relative(metadata_path),
            }
            if metadata:
                record["details"] = dict(metadata)

            metadata_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._remember_in_database(safe_nid, record)
            return record

    def _remember_in_database(self, nid: str, record: Dict[str, Any]) -> None:
        row = DBHandler.get_applicant(nid)
        if not row:
            return
        try:
            row_data = dict(row) if not isinstance(row, dict) else row
            data = json.loads(row_data.get("data") or "{}")
        except Exception:
            data = {}
        archives = data.get("bank_archives")
        if not isinstance(archives, list):
            archives = []
        archives.append(dict(record))
        data["bank_archives"] = archives[-300:]
        DBHandler.update_applicant_data(nid, {"bank_archives": data["bank_archives"]})

    def list_for_applicant(self, nid: object) -> List[Dict[str, Any]]:
        safe_nid = sanitize_nid(nid)
        if not safe_nid:
            return []
        row = DBHandler.get_applicant(safe_nid)
        records: List[Dict[str, Any]] = []
        if row:
            try:
                row_data = dict(row) if not isinstance(row, dict) else row
                data = json.loads(row_data.get("data") or "{}")
                stored = data.get("bank_archives") or []
                if isinstance(stored, list):
                    records.extend(item for item in stored if isinstance(item, dict))
            except Exception:
                pass

        if records:
            return sorted(records, key=lambda item: float(item.get("created_at") or 0), reverse=True)

        folder = self.root / safe_nid / "banks"
        if not folder.is_dir():
            return []
        for path in folder.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    records.append(payload)
            except Exception:
                continue
        return sorted(records, key=lambda item: float(item.get("created_at") or 0), reverse=True)

    def resolve_file(self, nid: object, relative_path: str) -> Path:
        safe_nid = sanitize_nid(nid)
        if not safe_nid:
            raise FileNotFoundError("Invalid applicant national ID")
        candidate = (self.root / str(relative_path or "")).resolve()
        applicant_root = (self.root / safe_nid).resolve()
        candidate.relative_to(applicant_root)
        if not candidate.is_file():
            raise FileNotFoundError(candidate.name)
        return candidate


BANK_ARCHIVE_STORE = BankArchiveStore()
