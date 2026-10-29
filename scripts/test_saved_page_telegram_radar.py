# -*- coding: utf-8 -*-
"""
scripts/test_saved_page_telegram_radar.py
Extracts bank capacity from a test saved page, builds the beautiful Telegram Radar message,
and dispatches it to the configured Telegram channel with proxy support and offline queueing.
"""
import os
import re
import sys
import glob

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath('.'))

from database import DBHandler
from core.bank_matcher import PersianBankMatcher
from core.telegram_notifier import TELEGRAM_NOTIFIER

def run_test():
    print("=" * 65)
    print("🚀 استخراج سهمیه بانک‌ها از صفحه ذخیره‌شده و ارسال رادار تلگرام")
    print("=" * 65)

    # 1. Ensure Bot Token & Channel are configured in DB
    bot_token = "8988974593:AAHnlt1ZVf5WAMFm59fbLtenxppF9aHM_Fc"
    channel_id = "-1003720759140"
    DBHandler.update_config({
        "telegram_bot_token": bot_token,
        "telegram_channel_id": channel_id,
        "telegram_admin_chat_id": channel_id,
        "telegram_notifications_enabled": True,
    })

    # 2. Pick a realistic test saved page from saved_pages
    sample_files = glob.glob('saved_pages/**/*.html', recursive=True)
    if not sample_files:
        print("❌ هیچ فایل ذخیره‌شده‌ای در پوشه saved_pages یافت نشد!")
        return

    # Choose a rich page (e.g. from احمد آزادی with 13 banks)
    target_file = None
    for f in sample_files:
        if "احمد آزادی" in f:
            target_file = f
            break
    if not target_file:
        target_file = sample_files[0]

    print(f"\n📂 صفحه ذخیره‌شده انتخابی:\n   {target_file}")

    with open(target_file, 'r', encoding='utf-8', errors='ignore') as fp:
        html = fp.read()

    # Extract Province and City
    m_prov = re.search(r'id=["\'].*?labStateName["\'][^>]*>([^<]+)<', html)
    province = m_prov.group(1).strip() if m_prov else "سیستان و بلوچستان"

    m_city = re.search(r'id=["\'].*?ddlCity["\'][^>]*>.*?<option[^>]*selected[^>]*>([^<]+)<', html, re.DOTALL)
    city = m_city.group(1).strip() if m_city else "مهرستان"

    # Extract Banks
    m_select = re.search(r'<select[^>]*id=["\'].*?ddlBankName["\'][^>]*>(.*?)</select>', html, re.DOTALL)
    if not m_select:
        print("❌ تگ انتخاب بانک (ddlBankName) در صفحه یافت نشد!")
        return

    options = re.findall(r'<option[^>]*value=["\']([^"\']+)["\'][^>]*>(.*?)</option>', m_select.group(1))
    valid_options = [(v.strip(), t.strip()) for v, t in options if v.strip() not in ('0', '', '-1')]

    active_banks = []
    reserve_banks = []

    for v, t in valid_options:
        parsed = PersianBankMatcher.parse_option_text(t)
        parsed.value = v
        info = {
            "bank_name": parsed.clean_title,
            "raw_text": t,
            "value": v,
            "is_active": parsed.has_active_quota,
            "is_reserve": parsed.is_reserve,
            "waiting_count": parsed.waiting_count,
        }
        if parsed.has_active_quota:
            active_banks.append(info)
        elif parsed.is_reserve:
            reserve_banks.append(info)
        else:
            active_banks.append(info)

    print(f"📍 موقعیت: استان {province} | شهر {city}")
    print(f"📊 بانک‌های استخراج‌شده: {len(active_banks)} سهمیه فعال | {len(reserve_banks)} در صف رزرو")

    # 3. Format Beautiful Radar Message (Provincial broadcast: Only province, all 13 banks unified)
    message_text = TELEGRAM_NOTIFIER.format_beautiful_radar_message(
        province_name=province,
        city_name=city,
        active_banks=active_banks,
        reserve_banks=reserve_banks,
        applicant_nid="0012345678",
        action_status="پایش سهمیه صفحه ۳ (تست سیو پیج)",
        provincial_only=True,
    )

    print("\n" + "─" * 60)
    print("📝 متن پیام ارسالی به کانال تلگرام (@Mahanvpn3):")
    print("─" * 60)
    print(message_text)
    print("─" * 60)

    # 4. Check Effective Proxy
    effective_proxy = TELEGRAM_NOTIFIER._detect_effective_proxy()
    print(f"\n🌐 وضعیت پروکسی تلگرام: {effective_proxy if effective_proxy else 'شناسایی نشد (تلاش برای ارسال مستقیم / صف هوشمند)'}")

    # 5. Dispatch message to Telegram
    print(f"\n📡 در حال ارسال پیام به کانال {channel_id}...")
    success, result_detail = TELEGRAM_NOTIFIER.send_raw_telegram(
        chat_id=channel_id,
        text=message_text,
    )

    if success:
        print(f"✅ نتیجه: ارسال مستقیم به تلگرام با موفقیت انجام شد! ({result_detail})")
    else:
        print(f"⚠️ پیام به علت فیلترینگ تلگرام در ارسال مستقیم متوقف شد: {result_detail}")
        print("💾 ذخیره‌سازی خودکار پیام در صف آفلاین دیتابیس (Queue)...")
        DBHandler.enqueue_telegram_message(
            chat_id=channel_id,
            message_text=message_text,
            message_type="capacity_change",
            event_id="test_saved_page_event",
        )
        pending = DBHandler.fetch_pending_telegram_messages(limit=5)
        print(f"📥 تعداد پیام‌های موجود در صف انتظار دیتابیس: {len(pending)} پیام")
        print("💡 به محض اتصال پروکسی (مانند V2Ray / Clash) یا تنظیم `telegram_proxy` در داشبورد، پیام به صورت خودکار به کانال ارسال می‌گردد.")

if __name__ == "__main__":
    run_test()
