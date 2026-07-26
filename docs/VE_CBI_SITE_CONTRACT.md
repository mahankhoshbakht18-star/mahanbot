# قرارداد فقط‌خواندنی سایت VE.CBI

بازرسی در ۲۰۲۶-۰۷-۲۶ با مرورگر کنترل‌شده و بدون ورود اطلاعات، click، submit یا حل CAPTCHA
انجام شد. navigation هر سه URL در محیط Codex timeout شد؛ بنابراین هیچ selector حدسی تازه‌ای
به‌عنوان واقعیت ثبت نشده است.

| صفحه | هدف | وضعیت قرارداد |
|---|---|---|
| `https://ve.cbi.ir/Register.aspx` | ثبت درخواست | غیرقابل دسترس؛ selector OTP موجود در کد: `#ctl00_ContentPlaceHolder1_tbMobileConfCode` و fallback مبتنی بر `name` |
| `https://ve.cbi.ir/SelectBnkShb.aspx` | انتخاب بانک/شعبه | غیرقابل دسترس؛ fixture باید از snapshot پاک‌سازی‌شده تهیه شود |
| `https://ve.cbi.ir/TasTrace.aspx` | پیگیری وضعیت | غیرقابل دسترس؛ این جریان در Bot نیاز به session OTP ندارد |

ترتیب selector در inspector و تست‌ها: `id`، `name`، label مرتبط، role، متن ثابت و سپس CSS
fallback. XPath وابسته به ترتیب DOM ممنوع است. marker تغییر ساختار، نبود هم‌زمان selector
اصلی و fallback است و باید خطای قابل بازیابی ایجاد کند؛ نه submit یا retry پرتعداد.

پیام OTP نامعتبر/منقضی، نشانه موفقیت و دکمه ادامه تا زمان snapshot معتبر «تأییدنشده» هستند.
CI فقط fixture آفلاین را اجرا می‌کند و عدم دسترسی شبکه build را fail نمی‌کند. screenshot
تولید نشد، چون صفحه قبل از render timeout شد؛ در نتیجه هیچ داده هویتی در artifact وجود ندارد.
