"""
core/bank_selection/catalog_parser.py — Structured Bank Catalog & Option Parsing
Extracts bank options, quotas, and queue counts from HTML or Playwright page elements.
"""
import re
from typing import List, Dict, Any, Optional
from core.bank_matcher import PersianBankMatcher, BankOptionInfo


class BankCatalogParser:
    """Parses CBI bank selection dropdown options into structured objects."""

    @classmethod
    def parse_option(cls, value: str, text: str) -> BankOptionInfo:
        """Parse a single option's value and raw text."""
        info = PersianBankMatcher.parse_option_text(text)
        info.value = str(value or "").strip()
        return info

    @classmethod
    def parse_html(cls, html: str) -> List[BankOptionInfo]:
        """Parse raw HTML containing select#ctl00_ContentPlaceHolder1_ddlBankName."""
        if not html:
            return []
        options: List[BankOptionInfo] = []
        # Find options inside ddlBankName or general select options
        select_match = re.search(r'<select[^>]*id=["\'][^"\']*ddlBankName[^"\']*["\'][^>]*>(.*?)</select>', html, re.DOTALL | re.IGNORECASE)
        target_content = select_match.group(1) if select_match else html

        option_matches = re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', target_content, re.DOTALL | re.IGNORECASE)
        for val, raw_txt in option_matches:
            val = val.strip()
            if not val or val == "0" or val == "-1" or "انتخاب" in raw_txt:
                continue
            # Strip inner HTML tags if any
            clean_txt = re.sub(r'<[^>]+>', '', raw_txt).strip()
            options.append(cls.parse_option(val, clean_txt))
        return options

    @classmethod
    def parse_playwright_page(cls, page) -> List[BankOptionInfo]:
        """Extract and parse all bank options directly from Playwright page DOM."""
        options: List[BankOptionInfo] = []
        try:
            raw_options = page.eval_on_selector(
                "#ctl00_ContentPlaceHolder1_ddlBankName",
                """el => Array.from(el.options || []).map(o => ({
                    value: o.value || '',
                    text: o.text || '',
                    selected: o.selected || false
                }))"""
            )
            for item in raw_options:
                val = str(item.get("value") or "").strip()
                txt = str(item.get("text") or "").strip()
                if not val or val in ("0", "-1") or "انتخاب" in txt:
                    continue
                options.append(cls.parse_option(val, txt))
        except Exception:
            # Fallback via direct locator evaluation if eval_on_selector fails
            try:
                locator = page.locator("#ctl00_ContentPlaceHolder1_ddlBankName option")
                count = locator.count()
                for i in range(count):
                    opt = locator.nth(i)
                    val = (opt.get_attribute("value") or "").strip()
                    txt = (opt.inner_text() or "").strip()
                    if not val or val in ("0", "-1") or "انتخاب" in txt:
                        continue
                    options.append(cls.parse_option(val, txt))
            except Exception:
                pass
        return options

    @classmethod
    def to_dict_list(cls, options: List[BankOptionInfo]) -> List[Dict[str, Any]]:
        """Convert list of BankOptionInfo into JSON-serializable dictionaries."""
        return [
            {
                "value": opt.value,
                "text": opt.text,
                "clean_title": opt.clean_title,
                "has_active_quota": opt.has_active_quota,
                "is_reserve": opt.is_reserve,
                "waiting_count": opt.waiting_count,
                "raw_waiting_text": opt.raw_waiting_text,
            }
            for opt in options
        ]
