with open('static/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update topbar to add version/update button
topbar_target = '<div class="topbar-actions">'
topbar_replacement = '''<div class="topbar-actions">
        <button class="btn btn-outline-primary action-pill" onclick="switchView('support'); setTimeout(() => { document.getElementById('updateCenterCard')?.scrollIntoView({behavior:'smooth'}); checkForUpdates(); }, 300);" data-tooltip="مرکز به‌روزرسانی هوشمند نسخه ۲.۰">
          <i class="fa-solid fa-cloud-arrow-down"></i>
          <span id="topbarVersionText">نسخه ۲.۰.۱</span>
        </button>'''

if topbar_target in text and 'id="topbarVersionText"' not in text:
    text = text.replace(topbar_target, topbar_replacement, 1)
    print("Updated topbar in index.html")

# 2. Replace the old Android card with the dual download cards + Advanced Update Center
old_android_card_marker = '<!-- Android SMS Forwarder App Download Card -->'
end_android_card_marker = '<!-- FAQ & Diagnostics Accordion -->'

start_idx = text.find(old_android_card_marker)
end_idx = text.find(end_android_card_marker)

if start_idx != -1 and end_idx != -1:
    new_support_block = '''<!-- ════════════════ SERVER & APP DOWNLOADS ════════════════ -->
      <div class="row g-3 mb-4">
        <!-- Android SMS Forwarder Direct Download Card -->
        <div class="col-lg-6">
          <article class="panel h-100 d-flex flex-column justify-content-between" style="border: 2px dashed #00BFB3; background: linear-gradient(135deg, #FFFFFF 0%, #F2FCFB 100%);">
            <div>
              <div class="d-flex align-items-center gap-3 mb-3">
                <div class="support-icon-box" style="background:#009F98;color:#FFFFFF;width:52px;height:52px;font-size:24px;border-radius:12px;display:flex;align-items:center;justify-content:center;">
                  <i class="fa-brands fa-android"></i>
                </div>
                <div>
                  <div class="d-flex align-items-center gap-2">
                    <h4 class="mb-0 fw-bold fs-6">اپلیکیشن فورواردر پیامک اندروید</h4>
                    <span class="badge" style="background:#E6FAF8;color:#009F98;border:1px solid #39E0D3;font-size:11px;">نسخه ۲.۰.۱ رسمی</span>
                  </div>
                  <small class="text-muted font-monospace">mahan-otp-2.0.1.apk</small>
                </div>
              </div>
              <p class="text-muted small mb-3">دریافت خودکار پیامک‌های تأییدیه بانک و ارسال لحظه‌ای OTP به ربات از طریق وب‌هوک مستقیم بدون نیاز به دخالت دست.</p>
              
              <div class="bg-light p-2 rounded mb-3 border">
                <div class="d-flex justify-content-between align-items-center text-muted small">
                  <span><i class="fa-solid fa-server ms-1 text-primary"></i> سرور رله اختصاصی:</span>
                  <span class="font-monospace fw-bold text-dark">otp.mahanvip.ir</span>
                </div>
                <div class="d-flex justify-content-between align-items-center text-muted small mt-1">
                  <span><i class="fa-solid fa-mobile-screen ms-1 text-success"></i> سازگاری:</span>
                  <span>اندروید ۷.۰ الی ۱۵ (بدون نیاز به روت)</span>
                </div>
              </div>
            </div>

            <div class="d-flex gap-2">
              <a href="https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk" target="_blank" class="btn btn-primary flex-fill" data-tooltip="دانلود مستقیم فایل نصبی APK از سرور اختصاصی">
                <i class="fa-solid fa-download ms-1"></i> دانلود مستقیم APK
              </a>
              <button type="button" class="btn btn-outline-secondary" onclick="copyToClipboard('https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk', this)" data-tooltip="کپی لینک مستقیم دانلود APK">
                <i class="fa-regular fa-copy"></i>
              </button>
            </div>
          </article>
        </div>

        <!-- MAHANBOT Server & Windows Release Download Card -->
        <div class="col-lg-6">
          <article class="panel h-100 d-flex flex-column justify-content-between" style="border: 2px dashed #E5A900; background: linear-gradient(135deg, #FFFFFF 0%, #FEF9EB 100%);">
            <div>
              <div class="d-flex align-items-center gap-3 mb-3">
                <div class="support-icon-box" style="background:#E5A900;color:#FFFFFF;width:52px;height:52px;font-size:24px;border-radius:12px;display:flex;align-items:center;justify-content:center;">
                  <i class="fa-solid fa-robot"></i>
                </div>
                <div>
                  <div class="d-flex align-items-center gap-2">
                    <h4 class="mb-0 fw-bold fs-6">دانلود نسخه نهایی ماهان‌بات (ویندوز و سرور)</h4>
                    <span class="badge" style="background:#FEF3D6;color:#B38300;border:1px solid #F4BD32;font-size:11px;">v2.0.1 پایدار</span>
                  </div>
                  <small class="text-muted font-monospace">mahanbot-latest.zip (~30 MB)</small>
                </div>
              </div>
              <p class="text-muted small mb-3">بسته کامل نرم‌افزاری شامل تمامی ماژول‌های اتوماسیون، موتور شکار ظرفیت، دیتابیس لوکال و لانچرهای اختصاصی Admin و Client.</p>
              
              <div class="bg-light p-2 rounded mb-3 border">
                <div class="d-flex justify-content-between align-items-center text-muted small">
                  <span><i class="fa-brands fa-windows ms-1 text-primary"></i> پلتفرم‌های هدف:</span>
                  <span class="fw-bold text-dark">Windows 10/11 & Linux VPS Server</span>
                </div>
                <div class="d-flex justify-content-between align-items-center text-muted small mt-1">
                  <span><i class="fa-solid fa-bolt ms-1 text-warning"></i> راه‌اندازی:</span>
                  <span>اجرای ۱ کلیکه بدون نیاز به کانفیگ پیچیده</span>
                </div>
              </div>
            </div>

            <div class="d-flex gap-2">
              <a href="https://otp.mahanvip.ir/downloads/mahanbot-latest.zip" target="_blank" class="btn btn-primary flex-fill" style="background:#E5A900;border-color:#E5A900;" data-tooltip="دانلود مستقیم بسته نصبی نسخه نهایی ماهان‌بات">
                <i class="fa-solid fa-file-zipper ms-1"></i> دانلود مستقیم ماهان‌بات (ZIP)
              </a>
              <button type="button" class="btn btn-outline-secondary" onclick="copyToClipboard('https://otp.mahanvip.ir/downloads/mahanbot-latest.zip', this)" data-tooltip="کپی لینک مستقیم دانلود ماهان‌بات">
                <i class="fa-regular fa-copy"></i>
              </button>
            </div>
          </article>
        </div>
      </div>

      <!-- ════════════════ ADVANCED IN-APP UPDATE CENTER ════════════════ -->
      <article class="panel mb-4" id="updateCenterCard" style="border: 1px solid #D5EFEB; box-shadow: 0 4px 16px rgba(0, 191, 179, 0.08);">
        <div class="panel-heading d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
          <div>
            <span class="section-kicker"><i class="fa-solid fa-arrows-rotate text-primary ms-1"></i> مرکز ارتقا و نگهداری خودکار</span>
            <h3 class="mb-0">مرکز به‌روزرسانی هوشمند درون‌برنامه‌ای</h3>
            <p class="text-muted small mb-0">بررسی، دریافت و اعمال نسخه‌های جدید به صورت خودکار بدون اختلال در داده‌های متقاضیان و پایگاه داده</p>
          </div>
          <div class="d-flex align-items-center gap-2">
            <span id="updateStatusPill" class="badge" style="background:#E6FAF8;color:#009F98;border:1px solid #39E0D3;padding:6px 14px;border-radius:20px;font-weight:700;">
              <i class="fa-solid fa-shield-check ms-1"></i> نسخه شما: v2.0.1
            </span>
            <button type="button" class="btn btn-sm btn-outline-primary" onclick="checkForUpdates()" id="btnCheckUpdate" data-tooltip="استعلام آخرین نسخه از سرور اختصاصی otp.mahanvip.ir">
              <i class="fa-solid fa-arrows-rotate ms-1" id="checkUpdateIcon"></i> بررسی نسخه جدید
            </button>
          </div>
        </div>

        <!-- Update Status Details & Actions -->
        <div id="updateInfoContainer" class="p-3 rounded mb-3" style="background:#F2FCFB;border:1px solid #D5EFEB;">
          <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
            <div>
              <h5 class="fw-bold mb-1 fs-6" id="updateHeadline"><i class="fa-solid fa-circle-check text-success ms-1"></i> وضعیت سیستم: به‌روز و پایدار</h5>
              <p class="text-muted small mb-0" id="updateSubtext">شما از آخرین نگارش رسمی MAHANBOT 2.0 استفاده می‌فرمایید. کلیه ماژول‌های اتوماسیون فعال می‌باشند.</p>
            </div>
            <div id="updateActionButtons" style="display:none;">
              <button class="btn btn-primary" id="btnStartUpdate" onclick="startAutoUpdate()">
                <i class="fa-solid fa-cloud-arrow-down ms-1"></i> ارتقای خودکار به نسخه جدید
              </button>
            </div>
          </div>

          <!-- Changelog Details Box (collapsed/hidden until update available or requested) -->
          <div id="updateChangelogBox" class="mt-3 pt-3 border-top" style="display:none;">
            <h6 class="fw-bold fs-7 text-primary mb-2"><i class="fa-solid fa-list-check ms-1"></i> یادداشت‌های تغییرات نسخه جدید:</h6>
            <ul id="updateReleaseNotesList" class="small text-muted mb-0 ps-3"></ul>
          </div>
        </div>

        <!-- 5-Step Progress Stepper (visible during update) -->
        <div id="updateProgressCard" class="p-3 rounded border" style="display:none;background:#FFFFFF;">
          <h6 class="fw-bold fs-7 mb-3 text-secondary"><i class="fa-solid fa-gears ms-1 text-primary"></i> مراحل ارتقای خودکار سیستم:</h6>
          <div class="update-stepper mb-3">
            <div class="stepper-step" id="step-backup">
              <span class="step-num">۱</span>
              <span class="step-label">پشتیبان دیتابیس</span>
            </div>
            <div class="stepper-line"></div>
            <div class="stepper-step" id="step-download">
              <span class="step-num">۲</span>
              <span class="step-label">دریافت بسته جدید</span>
            </div>
            <div class="stepper-line"></div>
            <div class="stepper-step" id="step-verify">
              <span class="step-num">۳</span>
              <span class="step-label">اعتبارسنجی فایل‌ها</span>
            </div>
            <div class="stepper-line"></div>
            <div class="stepper-step" id="step-apply">
              <span class="step-num">۴</span>
              <span class="step-label">اعمال تغییرات</span>
            </div>
            <div class="stepper-line"></div>
            <div class="stepper-step" id="step-complete">
              <span class="step-num">۵</span>
              <span class="step-label">پایان و تثبیت</span>
            </div>
          </div>

          <div class="progress mb-2" style="height: 10px; border-radius: 6px;">
            <div id="updateProgressBar" class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%; background: linear-gradient(90deg, #00BFB3, #009F98);"></div>
          </div>
          <div class="d-flex justify-content-between align-items-center">
            <small class="text-muted font-monospace" id="updateProgressMessage">در حال آماده‌سازی...</small>
            <small class="fw-bold text-primary font-monospace" id="updateProgressPercent">۰٪</small>
          </div>
        </div>

        <!-- Rollback & Safety Note -->
        <div class="d-flex justify-content-between align-items-center mt-3 pt-2 text-muted small">
          <div>
            <i class="fa-solid fa-shield-halved text-success ms-1"></i>
            <span>حفاظت قطعی داده‌ها: پیش از هرگونه به‌روزرسانی، پشتیبان خودکار ایجاد می‌شود.</span>
          </div>
          <button type="button" class="btn btn-sm btn-link text-muted p-0" id="btnRollback" onclick="rollbackUpdate()" style="display:none;" data-tooltip="بازگردانی دیتابیس به پیش از آخرین آپدیت">
            <i class="fa-solid fa-rotate-left ms-1"></i> بازگردانی به نسخه پیشین
          </button>
        </div>
      </article>

      '''
    text = text[:start_idx] + new_support_block + text[end_idx:]
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(text)
    print("SUCCESS: Updated #view-support with downloads and Update Center.")
else:
    print(f"FAILED: start_idx={start_idx}, end_idx={end_idx}")
