import requests
import base64
import time

# --- تنظیمات ---
# سریال نمایندگی شما (طبق عکس ارسالی)
API_KEY = "56937567295291171393355883194218"
# آدرس‌های API طبق مستندات سایت hcaptcha.ir
URL_SUBMIT = "http://api.hcaptcha.ir/in.php"
URL_RESULT = "http://api.hcaptcha.ir/res.php"

def solve_captcha_api(image_bytes):
    """
    این تابع بایت‌های عکس را می‌گیرد و با استفاده از API حل می‌کند.
    """
    if not image_bytes:
        print("❌ خطای ورودی: عکسی دریافت نشد.")
        return None

    print("🚀 در حال ارسال کپچا به سرویس ابری...")

    # 1. تبدیل عکس به فرمت Base64 (طبق مستندات متد base64)
    try:
        b64_encoded = base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        print(f"❌ خطا در کدگذاری عکس: {e}")
        return None

    # 2. ارسال درخواست حل کپچا (POST)
    payload = {
        'key': API_KEY,
        'method': 'base64',  # طبق مستندات برای کپچای تصویری
        'body': b64_encoded, # بدنه فایل به صورت base64
        'json': 1            # درخواست خروجی JSON برای پردازش راحت‌تر
    }

    try:
        response = requests.post(URL_SUBMIT, data=payload)
        result = response.json()
        
        # بررسی موفقیت آمیز بودن ارسال
        if result.get('status') != 1:
            # چاپ متن خطا اگر مشکلی باشد
            print(f"❌ خطا در ارسال به سایت: {result.get('request')}")
            return None
        
        request_id = result.get('request')
        print(f"✅ کپچا ارسال شد. شناسه پیگیری: {request_id}")
        
    except Exception as e:
        print(f"❌ خطای ارتباط با سرور (ارسال): {e}")
        return None

    # 3. چک کردن نتیجه (Polling)
    # طبق مستندات باید ۵ ثانیه صبر کنیم و بعد درخواست GET بزنیم
    print("⏳ در حال پردازش توسط سرور (لطفاً صبر کنید)...")
    
    for i in range(20): # ۲۰ بار تلاش (حدود ۱ دقیقه)
        time.sleep(3) # ۳ ثانیه وقفه بین هر چک
        
        try:
            # ساخت لینک دریافت جواب طبق مستندات
            check_url = f"{URL_RESULT}?key={API_KEY}&action=get&id={request_id}&json=1"
            
            check_response = requests.get(check_url)
            ans = check_response.json()
            
            # حالت اول: کپچا هنوز آماده نیست
            if ans.get('request') == "CAPCHA_NOT_READY":
                print(f"Drafting... ({i+1})")
                continue
            
            # حالت دوم: کپچا حل شد
            if ans.get('status') == 1:
                captcha_text = ans.get('request')
                print(f"🎉 جواب دریافت شد: {captcha_text}")
                return captcha_text
            
            # حالت سوم: خطای دیگر
            else:
                print(f"❌ خطای API در دریافت جواب: {ans.get('request')}")
                return None
                
        except Exception as e:
            print(f"⚠️ خطای شبکه در دریافت جواب: {e}")
            continue

    print("❌ زمان انتظار تمام شد و جوابی نیامد.")
    return None