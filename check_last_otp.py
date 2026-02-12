import json
import time
from database import DBHandler

def monitor_latest_otp():
    """
    نمایش آخرین کد پیامک ثبت شده در کل سیستم
    """
    print("--- سیستم نمایش آخرین وضعیت پیامک‌های دریافتی ---")
    
    try:
        # دریافت تمام متقاضیان از دیتابیس
        applicants = DBHandler.get_all_applicants()
        
        if not applicants:
            print("⚠️ هیچ متقاضی در دیتابیس یافت نشد.")
            return

        latest_record = None
        max_ts = 0

        for applicant in applicants:
            # داده‌های متقاضی به صورت دیکشنری است
            data = applicant.get('data', {})
            
            # استخراج کد و زمان دریافت
            otp_code = data.get('otp_code')
            otp_ts = data.get('otp_ts', 0)

            if otp_code and otp_ts > max_ts:
                max_ts = otp_ts
                latest_record = {
                    "nid": applicant.get('national_id'),
                    "name": applicant.get('full_name'),
                    "code": otp_code,
                    "status": data.get('otp_status'),
                    "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(otp_ts))
                }

        if latest_record:
            print(f"✅ آخرین پیامک دریافت شده:")
            print(f"👤 نام متقاضی: {latest_record['name']}")
            print(f"🆔 کد ملی: {latest_record['nid']}")
            print(f"🔢 کد تایید (OTP): {latest_record['code']}")
            print(f"🕒 زمان ثبت در دیتابیس: {latest_record['time']}")
            print(f"📡 وضعیت: {latest_record['status']}")
        else:
            print("❌ هیچ پیامکی تاکنون در دیتابیس ثبت نشده است.")

    except Exception as e:
        print(f"❌ خطا در خواندن دیتابیس: {e}")

if __name__ == "__main__":
    monitor_latest_otp()