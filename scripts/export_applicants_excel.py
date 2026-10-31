"""
scripts/export_applicants_excel.py
Exports all applicants from cbi_ultimate.db to a beautifully styled, RTL-ready Excel (.xlsx) file.
Preserves leading zeros on National IDs, mobiles, postal codes, and tracking codes.
"""
import sqlite3
import json
import os
import sys
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "cbi_ultimate.db"


def generate_applicants_workbook(db_path: Path = DB_PATH) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()

    # ─────────────────────────────────────────────────────────────
    # Sheet 1: متقاضیان (Applicants)
    # ─────────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "متقاضیان"
    ws.views.sheetView[0].rightToLeft = True
    ws.views.sheetView[0].showGridLines = True

    headers = [
        ("ردیف", 8, "center"),
        ("کد ملی", 15, "center"),
        ("نام و نام خانوادگی", 24, "right"),
        ("شماره موبایل", 16, "center"),
        ("استان", 20, "right"),
        ("شهر", 18, "right"),
        ("نوع وام", 14, "center"),
        ("بانک‌های اولویت", 30, "right"),
        ("روش اخذ وام", 15, "center"),
        ("بانک رزرو", 12, "center"),
        ("کد رهگیری", 16, "center"),
        ("کد پستی", 15, "center"),
        ("کد ملی همسر", 15, "center"),
        ("کد ملی فرزند", 15, "center"),
        ("تلفن ثابت", 15, "center"),
        ("کد شعبه", 12, "center"),
        ("تاریخ تولد", 14, "center"),
        ("تاریخ ازدواج", 14, "center"),
        ("وضعیت سیستم", 14, "center"),
    ]

    # Styles
    header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    data_font = Font(name="Segoe UI", size=10)
    zebra_fill = PatternFill(start_color="F0F4F8", end_color="F0F4F8", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Write Headers
    ws.row_dimensions[1].height = 28
    for col_idx, (h_name, width, align) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Read from DB
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT id, full_name, national_id, status, data FROM applicants ORDER BY id")
    rows = c.fetchall()
    conn.close()

    for row_idx, r in enumerate(rows, start=2):
        ws.row_dimensions[row_idx].height = 22
        fill = zebra_fill if (row_idx % 2 == 0) else white_fill

        data = json.loads(r[4]) if r[4] else {}

        # Banks list
        banks = data.get("priority_banks") or data.get("banks") or []
        if isinstance(banks, list):
            banks_str = "، ".join(str(b.get('name') if isinstance(b, dict) else b) for b in banks)
        else:
            banks_str = str(banks)

        # Loan type
        raw_loan = str(data.get("loan_type", "")).lower()
        loan_type_fa = "فرزندآوری" if raw_loan in ("child", "childbearing", "فرزند", "فرزندآوری") else "ازدواج"

        # Birth date
        by, bm, bd = data.get("birth_y"), data.get("birth_m"), data.get("birth_d")
        birth_str = f"{by}/{str(bm).zfill(2)}/{str(bd).zfill(2)}" if (by and bm and bd) else ""

        # Marriage date
        my, mm, md = data.get("marriage_y"), data.get("marriage_m"), data.get("marriage_d")
        marr_str = f"{my}/{str(mm).zfill(2)}/{str(md).zfill(2)}" if (my and mm and md) else ""

        # Reserve bank
        res_val = data.get("allow_reserve")
        reserve_fa = "بله" if res_val in (True, 1, "true", "1") else ("خیر" if res_val in (False, 0, "false", "0") else "")

        # Mode
        raw_mode = data.get("bank_mode", "")
        if raw_mode == "only_online":
            mode_fa = "غیرحضوری"
        elif raw_mode == "in_person":
            mode_fa = "حضوری"
        elif raw_mode:
            mode_fa = str(raw_mode)
        else:
            mode_fa = ""

        # Mobile
        mob = str(data.get("mobile") or data.get("phone_mobile") or "").strip()

        # National IDs and Postal code
        nid = str(r[2] or "").strip()
        spouse_nid = str(data.get("spouse_national_id") or data.get("spouse") or "").strip()
        child_nid = str(data.get("child_national_id") or "").strip()
        postal = str(data.get("postal_code") or data.get("zip_code") or "").strip()
        track = str(data.get("tracking_code") or "").strip()
        branch = str(data.get("branch_code") or "").strip()
        phone = str(data.get("phone") or "").strip()
        status_val = str(r[3] or "Stopped").strip()

        row_values = [
            row_idx - 1,                      # ردیف
            nid,                              # کد ملی (Text)
            str(r[1] or "").strip(),          # نام و نام خانوادگی
            mob,                              # موبایل (Text)
            str(data.get("state") or "").strip(), # استان
            str(data.get("city") or "").strip(),  # شهر
            loan_type_fa,                     # نوع وام
            banks_str,                        # بانک‌های اولویت
            mode_fa,                          # روش اخذ وام
            reserve_fa,                       # بانک رزرو
            track,                            # کد رهگیری (Text)
            postal,                           # کد پستی (Text)
            spouse_nid,                       # کد ملی همسر (Text)
            child_nid,                        # کد ملی فرزند (Text)
            phone,                            # تلفن ثابت (Text)
            branch,                           # کد شعبه (Text)
            birth_str,                        # تاریخ تولد
            marr_str,                         # تاریخ ازدواج
            status_val                        # وضعیت
        ]

        text_columns = {2, 4, 11, 12, 13, 14, 15, 16}  # 1-based indices that must be string format

        for col_idx, val in enumerate(row_values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.border = thin_border
            align_type = headers[col_idx - 1][2]
            cell.alignment = Alignment(horizontal=align_type, vertical="center")

            if col_idx in text_columns:
                cell.number_format = '@'

    # ─────────────────────────────────────────────────────────────
    # Sheet 2: راهنمای_ستون‌ها (Columns Guide)
    # ─────────────────────────────────────────────────────────────
    ws_guide = wb.create_sheet(title="راهنمای_ستون‌ها")
    ws_guide.views.sheetView[0].rightToLeft = True
    ws_guide.views.sheetView[0].showGridLines = True

    guide_headers = ["نام ستون", "ضروری؟", "فرمت و مقادیر مجاز", "توضیحات و مثال"]
    ws_guide.row_dimensions[1].height = 26
    for c_idx, gh in enumerate(guide_headers, start=1):
        cell = ws_guide.cell(row=1, column=c_idx, value=gh)
        cell.font = header_font
        cell.fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    guide_rows = [
        ("کد ملی", "بله", "۱۰ رقم (متن)", "کد ملی متقاضی. ارقام صفر اولیه نباید حذف شوند (مثال: 0941234567)."),
        ("نام و نام خانوادگی", "بله", "متن فارسی", "نام کامل متقاضی (مثال: سعید دادکاریان)."),
        ("شماره موبایل", "اختیاری/توصیه شده", "۱۱ رقم با 09", "جهت دریافت کدهای تایید و پیامک‌های سامانه (مثال: 09123456789)."),
        ("استان", "بله", "نام استان رسمی", "نام استان سکونت متقاضی (مثال: سیستان و بلوچستان، لرستان، تهران)."),
        ("شهر", "بله", "نام شهر یا شهرستان", "شهر محل درخواست تسهیلات (مثال: زاهدان، مهرستان، ازنا، سوران)."),
        ("نوع وام", "بله", "ازدواج یا فرزندآوری", "نوع تسهیلات بانکی (پیش‌فرض: ازدواج)."),
        ("بانک‌های اولویت", "بله", "نام بانک‌ها با ویرگول", "اولویت‌های بانک به ترتیب (مثال: صادرات، تجارت، ملی ایران)."),
        ("روش اخذ وام", "اختیاری", "حضوری / غیرحضوری / هردو", "فیلتر نحوه پذیرش در صفحه بانک‌ها (پیش‌فرض: هردو)."),
        ("بانک رزرو", "اختیاری", "بله / خیر", "در صورت نبود بانک‌های اصلی، آیا بانک‌های جایگزین انتخاب شوند؟"),
        ("کد رهگیری", "برای پایش بانک", "۱۰ رقم", "کد رهگیری دریافت شده از مرحله اول ثبت‌نام بانک مرکزی."),
        ("کد پستی", "برای ثبت‌نام اولیه", "۱۰ رقم", "کد پستی محل سکونت متقاضی."),
        ("کد ملی همسر", "وام ازدواج", "۱۰ رقم", "کد ملی همسر متقاضی در صورت وجود."),
        ("کد ملی فرزند", "وام فرزندآوری", "۱۰ رقم", "کد ملی فرزند متولدشده برای تسهیلات فرزندآوری."),
        ("تاریخ تولد", "برای ورود اولیه", "YYYY/MM/DD", "تاریخ تولد هجری شمسی (مثال: 1380/05/12)."),
        ("تاریخ ازدواج", "وام ازدواج", "YYYY/MM/DD", "تاریخ عقد/ازدواج هجری شمسی (مثال: 1403/07/20)."),
    ]

    ws_guide.column_dimensions['A'].width = 20
    ws_guide.column_dimensions['B'].width = 16
    ws_guide.column_dimensions['C'].width = 24
    ws_guide.column_dimensions['D'].width = 65

    for r_idx, g_vals in enumerate(guide_rows, start=2):
        ws_guide.row_dimensions[r_idx].height = 22
        fill = zebra_fill if (r_idx % 2 == 0) else white_fill
        for c_idx, val in enumerate(g_vals, start=1):
            cell = ws_guide.cell(row=r_idx, column=c_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="right" if c_idx == 4 else "center", vertical="center")

    return wb


def export_applicants_excel():
    wb = generate_applicants_workbook(DB_PATH)

    out_fa = ROOT_DIR / "اطلاعات_متقاضیان_MAHANBOT.xlsx"
    out_en = ROOT_DIR / "applicants_data.xlsx"

    wb.save(str(out_fa))
    wb.save(str(out_en))

    print(f"[SUCCESS] Exported applicants Excel files:")
    print(f"  - {out_fa.name} ({out_fa.stat().st_size} bytes)")
    print(f"  - {out_en.name} ({out_en.stat().st_size} bytes)")


if __name__ == "__main__":
    export_applicants_excel()
