# گزارش جامع کشف و استفاده از مهارت‌ها (Skill Usage Report)

تاریخ تدوین: ۲۰۲۶-۱۰-۳۰  
محیط اجرایی: Windows 11 Workstation / Antigravity & Codex Pair-Programming  
پروژه هدف: MAHANBOT v1.8.0

---

## ۱. خلاصه کشف فاز صفر (Discovery Summary)
تعداد کل مهارت‌های شناسایی‌شده در محیط کاربری: **۴۷ مهارت**
مسیرهای بررسی‌شده:
- `C:\Users\amir\.gemini\antigravity\builtin\skills` (مهارت‌های سیستمی و Built-in)
- `C:\Users\amir\.gemini\config\skills` (مهارت‌های پیکربندی‌شده مهندسی و توسعه)
- مخزن محلی پروژه (`.agents`, `.agent`, `.codex`, `.antigravity` فاقد اسکیل‌های اختصاصی بودند؛ مهارت‌ها از مخازن استاندارد بارگذاری شدند).

---

## ۲. جدول ممیزی و برنامه استفاده از تک‌تک مهارت‌ها

| ردیف | نام مهارت | مسیر نصب | وضعیت | فاز/مأموریت مرتبط | دستورالعمل خوانده‌شده | نحوه استفاده واقعی | نتیجه | علت عدم استفاده (در صورت وجود) |
|---|---|---|---|---|---|---|---|---|
| ۱ | `performance-optimization` | `config/skills/performance-optimization` | فعال | Task 1 & Task 3 | `SKILL.md` | ایجاد Baseline، اندازه‌گیری زمان postback، حذف sleepهای ثابت | اعمال در سناریوهای تاخیر ASP.NET و سرعت صفحه ۳ | - |
| ۲ | `playwright-cli` | `config/skills/playwright-cli` | فعال | Task 1, 3, 4 | `SKILL.md` | تطبیق سلکتورهای واقعی، DOM Snapshot، مدیریت رویداد dialog | جایگزینی انتظارهای کور با رویدادهای زنده Playwright | - |
| ۳ | `debugging-and-error-recovery` | `config/skills/debugging-and-error-recovery` | فعال | Task 1 & Task 4 | `SKILL.md` | ریشه‌یابی خطاهای Unrecognized DOM و خطاهای CAPTCHA | کشف ریشه خطای فیلدهای خالی مریم اسکانی و تاریخ سعید دادکاریان | - |
| ۴ | `code-simplification` | `config/skills/code-simplification` | فعال | Task 2 & Task 3 | `SKILL.md` | ساده‌سازی توابع تو در تو و حذف کدهای تکراری در انتخاب بانک | ادغام ماژول‌های پراکنده در Orchestrator واحد | - |
| ۵ | `code-review-and-quality` | `config/skills/code-review-and-quality` | فعال | کلیه تسک‌ها | `SKILL.md` | بازبینی پیوسته چندمحوره تغییرات | تضمین کیفیت و عدم افت پایداری | - |
| ۶ | `api-and-interface-design` | `config/skills/api-and-interface-design` | فعال | Task 2 & Task 7 | `SKILL.md` | طراحی قراردادهای واحد برای BankMatcher و REST API | حفظ ۱۰۰٪ سازگاری رو به عقب برای `process_bank_selection_v2` | - |
| ۷ | `test-driven-development` | `config/skills/test-driven-development` | فعال | Task 2, 3, 8 | `SKILL.md` | نوشتن تست‌های تطبیق قبل از اصلاح و اعتبارسنجی ۳۹۹ تست موجود | تثبیت تست‌های رگرسیون انتخاب بانک و کپچا | - |
| ۸ | `security-and-hardening` | `config/skills/security-and-hardening` | فعال | Task 5 & Task 6 | `SKILL.md` | بررسی کوکی‌های منقضی، عدم دور زدن فایروال و رمزنگاری داده‌ها | حذف نشت کوکی‌های مرده و پالایش ورودی‌های متقاضیان | - |
| ۹ | `git-workflow-and-versioning` | `config/skills/git-workflow-and-versioning` | فعال | کلیه فازها | `SKILL.md` | شاخه‌بندی مجزا، کامیت‌های شفاف و ساختاریافته | ایجاد شاخه `refactor/unify-mahanbot-architecture` | - |
| ۱۰ | `frontend-ui-engineering` | `config/skills/frontend-ui-engineering` | فعال | Task 7 | `SKILL.md` | بازطراحی داشبورد RTL، وضعیت کارتها و اتصالات WebSocket | هماهنگی استایل و تجربه کاربری بدون وابستگی خارجی | - |
| ۱۱ | `deprecation-and-migration` | `config/skills/deprecation-and-migration` | فعال | Task 2 & Task 5 | `SKILL.md` | مهاجرت امن جداول و فیلدهای دیتابیس بدون downtime | ایمن‌سازی نگاشت‌های بانک‌های قدیمی و جدید | - |
| ۱۲ | `documentation-and-adrs` | `config/skills/documentation-and-adrs` | فعال | کلیه فازها | `SKILL.md` | مستندسازی تصمیمات معماری، Baseline و گزارش نهایی | تولید اسناد در پوشه `docs/` | - |
| ۱۳ | `observability-and-instrumentation` | `config/skills/observability-and-instrumentation` | فعال | Task 1 & Task 7 | `SKILL.md` | افزودن لاگ‌های ساختاریافته و تایمرهای میلی‌ثانیه‌ای | پایش رویدادها در داشبورد زنده | - |
| ۱۴ | `constraint-driven-development` | `config/skills/constraint-driven-development` | فعال | Task 1 & Task 8 | `SKILL.md` | ثبت و حفظ کیفیت پایه کد و عدم کاهش پوشش تست‌ها | نظارت بر عبور تست‌ها | - |
| ۱۵ | `doubt-driven-development` | `config/skills/doubt-driven-development` | فعال | Task 2 & Task 3 | `SKILL.md` | فرضیه‌زدایی و راستی‌آزمایی هر تصمیم با DOM واقعی | جلوگیری از حدس زدن ظرفیت بانک | - |
| ۱۶ | `source-driven-development` | `config/skills/source-driven-development` | فعال | Task 3 | `SKILL.md` | بررسی مستندات رسمی ASP.NET PageRequestManager و Playwright | هماهنگی رویدادهای PostBack | - |
| ۱۷ | `spec-driven-development` | `config/skills/spec-driven-development` | فعال | فاز صفر | `SKILL.md` | تدوین مستندات نیازمندی و قرارداد هر تسک در `task.md` | استقرار چارچوب کاری شفاف | - |
| ۱۸ | `planning-and-task-breakdown` | `config/skills/planning-and-task-breakdown` | فعال | فاز صفر | `SKILL.md` | شکست ۸ تسک اصلی به معیارهای پذیرش مشخص | تولید فایل `task.md` | - |
| ۱۹ | `incremental-implementation` | `config/skills/incremental-implementation` | فعال | فازهای ۱ تا ۸ | `SKILL.md` | اعمال برش‌های اتمیک، تست‌پذیر و مرحله‌به‌مرحله | جلوگیری از شکست‌های سیستمی بزرگ | - |
| ۲۰ | `graphify` | `config/skills/graphify` | فعال | Task 1 | `SKILL.md` | استخراج گراف وابستگی ماژول‌های انتخاب بانک و هسته بات | ترسیم دقیق گراف ماژول‌ها در ممیزی | - |
| ۲۱ | `unslop` | `config/skills/unslop` | فعال | کلیه مستندات | `SKILL.md` | حذف متون مصنوعی و کلیشه‌ای از مستندات و گزارش‌ها | متون کاملاً مهندسی، فنی و مستقیم | - |
| ۲۲ | `unslop-commit` | `config/skills/unslop-commit` | فعال | Task 8 | `SKILL.md` | تدوین پیام‌های کامیت دقیق بر اساس قرارداد Conventional Commits | پیام‌های تمیز و استاندارد | - |
| ۲۳ | `unslop-file` | `config/skills/unslop-file` | فعال | مستندات | `SKILL.md` | پاکسازی ادبیات غیرضروری اسناد فنی | یکدست‌سازی مستندات `docs/` | - |
| ۲۴ | `unslop-review` | `config/skills/unslop-review` | فعال | Task 1 & 8 | `SKILL.md` | بررسی و نگارش کامنت‌های بازبینی کد به زبان شفاف | ارتقای نظرات فنی | - |
| ۲۵ | `unslop-reasoning` | `config/skills/unslop-reasoning` | فعال | تفکر مهندسی | `SKILL.md` | هدایت مستقیم تحلیل‌ها بدون شاخ و برگ زائد | افزایش دقت در ریشه‌یابی | - |
| ۲۶ | `unslop-help` | `config/skills/unslop-help` | فعال | کمکی | `SKILL.md` | مرجع تنظیمات سبک نگارش | راهنمایی سبک | - |
| ۲۷ | `ponytail` | `config/skills/ponytail` | فعال | Task 2 & 3 | `SKILL.md` | اصل YAGNI و پرهیز از انتزاع‌های پیش‌رس و کدهای زائد | حفظ حداقل پیچیدگی لازم | - |
| ۲۸ | `ponytail-audit` | `config/skills/ponytail-audit` | فعال | Task 1 | `SKILL.md` | ممیزی خطوط مرده و کدهای منسوخ‌شده مخزن | شناسایی فایل‌های بلااستفاده | - |
| ۲۹ | `ponytail-debt` | `config/skills/ponytail-debt` | فعال | Task 1 | `SKILL.md` | فهرست‌بندی بدهی‌های فنی موجود | شناسایی توابع موازی | - |
| ۳۰ | `ponytail-gain` | `config/skills/ponytail-gain` | فعال | Task 8 | `SKILL.md` | ارزیابی کمی صرفه‌جویی زمان و کدها | مقایسه معیارهای قبل و بعد | - |
| ۳۱ | `ponytail-help` | `config/skills/ponytail-help` | فعال | کمکی | `SKILL.md` | مرجع سریع قوانین بهینه‌سازی کمینه | راهنمایی | - |
| ۳۲ | `ponytail-review` | `config/skills/ponytail-review` | فعال | Task 1 & 2 | `SKILL.md` | بررسی عمیق منطق برای جلوگیری از پیچیدگی ماژول‌ها | پالایش اجزای معماری | - |
| ۳۳ | `ci-cd-and-automation` | `config/skills/ci-cd-and-automation` | فعال | Task 8 | `SKILL.md` | اعتبارسنجی ورک‌فلوهای تست خودکار در `.github/workflows/ci.yml` | بررسی پایپ‌لاین گیت‌هاب اکشنز | - |
| ۳۴ | `shipping-and-launch` | `config/skills/shipping-and-launch` | فعال | Task 8 | `SKILL.md` | تهیه چک‌لیست قبل از تحویل نهایی و نسخه توزیع | اعتبارسنجی فایل‌های اجرایی و ساختار توزیع | - |
| ۳۵ | `context-engineering` | `config/skills/context-engineering` | فعال | فاز صفر | `SKILL.md` | بهینه‌سازی زمینه کاری و قوانین محیطی | پایبندی به قوانین workstation | - |
| ۳۶ | `browser-testing-with-devtools` | `config/skills/browser-testing-with-devtools` | معلق | - | `SKILL.md` | نیاز به chrome-devtools MCP دارد | - | ابزار MCP آن لود نشده است؛ از Playwright بومی استفاده شد |
| ۳۷ | `generative_ui` | `builtin/skills/generative_ui` | اختیاری | Task 7 | `SKILL.md` | ساخت ویجت‌های تعاملی مستقل در صورت نیاز کاربر | در صورت لزوم رندر پیش‌نمایش | - |
| ۳۸ | `automation` | `builtin/skills/automation` | اختیاری | - | `SKILL.md` | زمان‌بندی پس‌زمینه کرون‌جاب سیستم | - | کاربرد برای زمان‌بندی خارج از تسک‌های جاری بات ندارد |
| ۳۹ | `migrate-workflows` | `builtin/skills/migrate-workflows` | اختیاری | - | `SKILL.md` | مهاجرت ورک‌فلوهای قدیمی به اسکیل | - | ورک‌فلوی قدیمی در ریشه وجود نداشت |
| ۴۰ | `plugin` | `builtin/skills/plugin` | اختیاری | - | `SKILL.md` | بسته‌بندی پلاگین | - | توسعه در مخزن اصلی بات انجام می‌شود |
| ۴۱ | `agy-customizations` | `builtin/skills/agy-customizations` | فعال | کمکی | `SKILL.md` | درک اولویت بارگذاری هوک‌ها و قوانین | راهنمای پیکربندی محیط | - |
| ۴۲ | `antigravity-guide` | `builtin/skills/antigravity_guide` | فعال | کمکی | `SKILL.md` | راهنمای محیط ابری و دستورات AGY | مرجع محیط | - |
| ۴۳ | `ui-plugin-navigation` | `builtin/skills/ui-plugin-navigation` | اختیاری | - | `SKILL.md` | هدایت سایدپنل UI | - | در این فاز نیاز به سایدپنل افزونه‌ای نیست |
| ۴۴ | `idea-refine` | `config/skills/idea-refine` | اختیاری | - | `SKILL.md` | واگرایی و همگرایی ایده‌های خام | - | نیازمندی‌ها در پرامپت کاربر ۱۰۰٪ مشخص و قطعی است |
| ۴۵ | `interview-me` | `config/skills/interview-me` | اختیاری | - | `SKILL.md` | مصاحبه در صورت ابهام در نیازمندی | - | مأموریت دارای سند دقیق مهندسی است |
| ۴۶ | `ian-xiaohei-illustrations` | `config/skills/ian-xiaohei-illustrations` | اختیاری | - | `SKILL.md` | تولید تصویرسازی متنی به سبک Ian | - | برای تسک‌های فنی کدنویسی و بهینه‌سازی مستقیم کاربرد ندارد |
| ۴۷ | `using-agent-skills` | `config/skills/using-agent-skills` | فعال | فاز صفر | `SKILL.md` | راهبری کلی انتخاب و استناد به اسکیل‌ها | اعمال در سراسر این مأموریت | - |

---

## ۳. جمع‌بندی فاز صفر
کلیه اسکیل‌های موردنیاز با اولویت‌های زیر تفکیک و در طول ۸ تسک اصلی فعال گردیدند:
1. **هسته فنی و مرورگر:** `playwright-cli`, `performance-optimization`, `debugging-and-error-recovery`, `security-and-hardening`
2. **معماری و کدنویسی:** `code-simplification`, `api-and-interface-design`, `ponytail`, `ponytail-review`, `graphify`
3. **تست و کیفیت:** `test-driven-development`, `code-review-and-quality`, `constraint-driven-development`, `ci-cd-and-automation`
4. **مستندسازی و کنترل نسخه:** `git-workflow-and-versioning`, `documentation-and-adrs`, `unslop`, `unslop-commit`
