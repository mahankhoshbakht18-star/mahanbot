# -*- coding: utf-8 -*-
"""
scripts/clean_settings_ui.py
Cleans up redundant/duplicate settings options and removes the corrupted duplicate SMS panel in static/index.html and static/script.js.
"""
import re
import sys

def clean_index_html():
    with open('static/index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # 1. Clean up Bot Behavior panel form-grid
    old_grid_pattern = re.compile(
        r'(<article class="panel" style="border-top:4px solid var\(--primary\)">\s*'
        r'<div class="panel-title">\s*'
        r'<div>\s*'
        r'<h3><i class="fa-solid fa-sliders ms-2 text-primary"></i>[^<]*</h3>\s*'
        r'<p>[^<]*</p>\s*'
        r'</div>\s*'
        r'</div>\s*'
        r'<div class="form-grid compact mt-2">)'
        r'(.*?)'
        r'(</div>\s*<input id="set_captcha" type="hidden")',
        re.DOTALL
    )

    clean_grid_content = """
            <label>محل نمایش مرورگر
              <select id="set_browser_display_mode" class="form-select">
                <option value="external">پنجره جداگانه (پیش‌فرض)</option>
                <option value="embedded">داخل داشبورد</option>
              </select>
            </label>
            <label>بزرگ‌نمایی صفحه مرورگر (Zoom)
              <select id="set_browser_page_zoom" class="form-select">
                <option value="1.0">۱۰۰٪ (عادی)</option>
                <option value="1.25" selected>۱۲۵٪ (پیشنهادی - بهینه‌شده)</option>
                <option value="1.50">۱۵۰٪ (بزرگ)</option>
                <option value="1.75">۱۷۵٪ (خیلی بزرگ)</option>
                <option value="0.55">۵۵٪ (بسیار فشرده)</option>
              </select>
            </label>
            <label class="check-card text-info fw-bold" style="background:#F0F9FF; border-color:#BAE6FD;">
              <input id="set_focus_view_enabled" type="checkbox" checked>
              <i class="fa-solid fa-eye ms-1 text-info"></i> نمای تمرکزی فرم بانک (Focus View)
            </label>
            <label>حالت اجرا
              <select id="set_automation_mode" class="form-select">
                <option value="safe">Safe (ایمن)</option>
                <option value="normal">Normal (عادی)</option>
                <option value="fast">Fast (سریع)</option>
              </select>
            </label>
            <label>فاصله بررسی فهرست بانک (ثانیه)
              <input id="set_bank_refresh" type="number" min="1" max="300" value="5" class="form-control">
            </label>
            <label>اعتبار تخمینی پیامک / OTP (ثانیه)
              <input id="set_otp_validity" type="number" min="30" max="600" value="120" class="form-control">
            </label>
            <label>تعداد تلاش مجدد ورود
              <input id="set_retry" type="number" min="1" max="20" value="5" class="form-control">
            </label>
            <label>حداکثر تلاش کپچا
              <input id="set_captcha_max" type="number" min="1" max="10" value="3" class="form-control">
            </label>
            <label class="check-card"><input id="set_bank_monitor" type="checkbox" checked> پایش خودکار بانک فعال</label>
            <label class="check-card"><input id="set_resume_checkpoint" type="checkbox" checked> ادامه از آخرین Checkpoint</label>
            <label class="check-card"><input id="set_save_page" type="checkbox" checked> ذخیره صفحات (Save Page)</label>
            <label class="check-card"><input id="set_save_change_only" type="checkbox" checked> ذخیره فقط هنگام تغییر وضعیت</label>
            <label class="check-card"><input id="set_save_html" type="checkbox" checked> ذخیره فایل HTML</label>
            <label class="check-card"><input id="set_save_screenshot" type="checkbox" checked> ذخیره تصویر صفحه</label>
            <label class="check-card"><input id="set_pause_manual_captcha" type="checkbox" checked> توقف برای ورود دستی CAPTCHA</label>
            <label class="check-card"><input id="set_pause_manual_otp" type="checkbox" checked> توقف برای ورود دستی OTP</label>
            <label class="check-card text-primary fw-bold" style="background:#EEF2FF; border-color:#C7D2FE;">
              <input id="set_auto_switch_bank" type="checkbox" checked>
              <i class="fa-solid fa-water ms-1 text-primary"></i> سوییچ خودکار آبشاری به اولویت بعدی (Waterfall)
            </label>
            <label class="check-card"><input id="set_allow_reserve_bank" type="checkbox" checked> پذیرش بانک‌های دارای سهمیه رزرو</label>
            <label>مدت زمان کول‌داون بانک پرشده (ثانیه)
              <input id="set_bank_cooldown" type="number" min="5" max="300" value="30" class="form-control">
            </label>
            <label class="check-card"><input id="set_diagnostic_logging" type="checkbox" checked> ثبت لاگ تشخیصی</label>
            <label class="check-card"><input id="set_headless" type="checkbox"> اجرای مرورگر مخفی</label>
            <label class="check-card"><input id="set_useproxy" type="checkbox"> استفاده از پروکسی</label>
            <label class="check-card text-success fw-bold" style="background:#ECFDF5; border-color:#A7F3D0;">
              <input id="set_anti_detection" type="checkbox" checked>
              <i class="fa-solid fa-user-secret ms-1 text-success"></i> سپر ضد تشخیص هویت مرورگر (Anti-Detection Shield)
            </label>
            <label>حداکثر اجرای همزمان متقاضیان (Concurrency Workers)
              <input id="set_max_concurrency" type="number" min="1" max="32" value="4" class="form-control">
            </label>
            <label>استراتژی چرخش پروکسی
              <select id="set_proxy_strategy" class="form-select">
                <option value="round_robin">چرخشی نوبتی (Round Robin)</option>
                <option value="per_applicant">اختصاصی برای هر متقاضی (Sticky Session)</option>
                <option value="random_healthy">انتخاب تصادفی از پروکسی‌های باکیفیت</option>
              </select>
            </label>
          """

    match = old_grid_pattern.search(html)
    if not match:
        print("ERROR: old_grid_pattern did not match in index.html")
        sys.exit(1)

    html = html[:match.start(2)] + clean_grid_content + html[match.end(2):]
    print("SUCCESS: Bot Behavior grid cleaned in index.html")

    # 2. Fix broken quote at Article 5
    broken_art5 = 'class="panel mt-4" admin-only" id="smsGatewayPanel style="border-top:4px solid var(--gold, #E9BD63)"'
    fixed_art5 = 'class="panel mt-4 admin-only" id="automatedSmsPanel" style="border-top:4px solid var(--gold, #E9BD63)"'
    if broken_art5 in html:
        html = html.replace(broken_art5, fixed_art5)
        print("SUCCESS: Fixed Article 5 syntax in index.html")
    else:
        print("NOTE: broken_art5 not found or already fixed")

    # 3. Remove Article 7 (corrupted duplicate smsGatewayPanel)
    corrupted_pattern = re.compile(
        r'<!-- SMS Gateway & OTP Auth Panel \(Admin Only\) -->\s*'
        r'<article id="smsGatewayPanel".*?</article>\s*',
        re.DOTALL
    )
    m_corrupt = corrupted_pattern.search(html)
    if m_corrupt:
        html = html[:m_corrupt.start()] + html[m_corrupt.end():]
        print("SUCCESS: Removed corrupted Article 7 (smsGatewayPanel with ????) in index.html")
    else:
        print("NOTE: Corrupted Article 7 not found by regex")

    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Saved static/index.html")


def clean_script_js():
    with open('static/script.js', 'r', encoding='utf-8') as f:
        js = f.read()

    # 1. Update loadSettings() to safely handle optional/removed elements
    old_load_chunk = """        document.getElementById('set_captcha').value = s.captcha_delay || 0.1;

        document.getElementById('set_retry').value = s.retry_count || 5;
        document.getElementById('set_automation_mode').value = s.automation_mode || 'normal';
        document.getElementById('set_default_timeout').value = s.default_timeout_ms ?? 5000;
        document.getElementById('set_navigation_timeout').value = s.navigation_timeout_ms ?? 15000;
        document.getElementById('set_retry_delay').value = s.retry_delay ?? 0.25;
        document.getElementById('set_captcha_poll').value = s.captcha_poll_interval ?? 0.1;
        document.getElementById('set_captcha_retry_delay').value = s.captcha_retry_delay ?? 0.25;
        document.getElementById('set_captcha_max').value = s.captcha_max_retries ?? 3;
        document.getElementById('set_bank_refresh').value = s.bank_list_refresh_seconds ?? s.bank_refresh_interval ?? 5;
        if (document.getElementById('set_bank_capacity_recheck')) document.getElementById('set_bank_capacity_recheck').value = s.bank_capacity_recheck_seconds ?? 20;
        if (document.getElementById('set_otp_validity')) document.getElementById('set_otp_validity').value = s.otp_validity_seconds ?? 120;
        if (document.getElementById('set_resume_checkpoint')) document.getElementById('set_resume_checkpoint').checked = s.resume_from_checkpoint !== false;
        if (document.getElementById('set_repair_page')) document.getElementById('set_repair_page').checked = s.repair_page_enabled !== false;
        if (document.getElementById('set_pause_manual_captcha')) document.getElementById('set_pause_manual_captcha').checked = s.pause_for_manual_captcha !== false;
        if (document.getElementById('set_pause_manual_otp')) document.getElementById('set_pause_manual_otp').checked = s.pause_for_manual_otp !== false;
        if (document.getElementById('set_allow_reserve_bank')) document.getElementById('set_allow_reserve_bank').checked = s.allow_reserve_bank !== false;
        if (document.getElementById('set_diagnostic_logging')) document.getElementById('set_diagnostic_logging').checked = s.diagnostic_logging_enabled !== false;
        document.getElementById('set_bank_monitor').checked = s.bank_monitor_enabled !== false;
        document.getElementById('set_save_page').checked = s.save_page_enabled !== false;
        document.getElementById('set_save_html').checked = s.save_html !== false;
        document.getElementById('set_save_screenshot').checked = s.save_screenshot !== false;
        document.getElementById('set_save_change_only').checked = s.save_only_on_bank_change !== false;
        document.getElementById('set_auto_repair').checked = s.auto_repair_saved_pages === true;

        document.getElementById('set_headless').checked = s.headless || false;
        document.getElementById('set_browser_display_mode').value = s.browser_display_mode || 'external';
        if (document.getElementById('set_default_bank_mode')) document.getElementById('set_default_bank_mode').value = s.default_bank_mode || 'online';
        if (document.getElementById('set_browser_page_zoom')) document.getElementById('set_browser_page_zoom').value = String(s.browser_page_zoom || '1.25');
        if (document.getElementById('set_focus_view_enabled')) document.getElementById('set_focus_view_enabled').checked = s.focus_view_enabled !== false;
        applyBrowserDisplayMode(s.browser_display_mode || 'external');

        

        //  

        document.getElementById('set_captcha_mode').value = s.captcha_mode || 'model'; 

        setSubmissionDefaults(s);
        document.getElementById('set_final_submit').checked = Boolean(s.auto_final_submit ?? s.final_submit ?? false);"""

    new_load_chunk = """        if (document.getElementById('set_captcha')) document.getElementById('set_captcha').value = s.captcha_delay || 0.1;
        if (document.getElementById('set_retry')) document.getElementById('set_retry').value = s.retry_count || 5;
        if (document.getElementById('set_automation_mode')) document.getElementById('set_automation_mode').value = s.automation_mode || 'normal';
        if (document.getElementById('set_default_timeout')) document.getElementById('set_default_timeout').value = s.default_timeout_ms ?? 5000;
        if (document.getElementById('set_navigation_timeout')) document.getElementById('set_navigation_timeout').value = s.navigation_timeout_ms ?? 15000;
        if (document.getElementById('set_retry_delay')) document.getElementById('set_retry_delay').value = s.retry_delay ?? 0.25;
        if (document.getElementById('set_captcha_poll')) document.getElementById('set_captcha_poll').value = s.captcha_poll_interval ?? 0.1;
        if (document.getElementById('set_captcha_retry_delay')) document.getElementById('set_captcha_retry_delay').value = s.captcha_retry_delay ?? 0.25;
        if (document.getElementById('set_captcha_max')) document.getElementById('set_captcha_max').value = s.captcha_max_retries ?? 3;
        if (document.getElementById('set_bank_refresh')) document.getElementById('set_bank_refresh').value = s.bank_list_refresh_seconds ?? s.bank_refresh_interval ?? 5;
        if (document.getElementById('set_bank_capacity_recheck')) document.getElementById('set_bank_capacity_recheck').value = s.bank_capacity_recheck_seconds ?? 20;
        if (document.getElementById('set_otp_validity')) document.getElementById('set_otp_validity').value = s.otp_validity_seconds ?? 120;
        if (document.getElementById('set_resume_checkpoint')) document.getElementById('set_resume_checkpoint').checked = s.resume_from_checkpoint !== false;
        if (document.getElementById('set_repair_page')) document.getElementById('set_repair_page').checked = s.repair_page_enabled !== false;
        if (document.getElementById('set_pause_manual_captcha')) document.getElementById('set_pause_manual_captcha').checked = s.pause_for_manual_captcha !== false;
        if (document.getElementById('set_pause_manual_otp')) document.getElementById('set_pause_manual_otp').checked = s.pause_for_manual_otp !== false;
        if (document.getElementById('set_allow_reserve_bank')) document.getElementById('set_allow_reserve_bank').checked = s.allow_reserve_bank !== false;
        if (document.getElementById('set_diagnostic_logging')) document.getElementById('set_diagnostic_logging').checked = s.diagnostic_logging_enabled !== false;
        if (document.getElementById('set_bank_monitor')) document.getElementById('set_bank_monitor').checked = s.bank_monitor_enabled !== false;
        if (document.getElementById('set_save_page')) document.getElementById('set_save_page').checked = s.save_page_enabled !== false;
        if (document.getElementById('set_save_html')) document.getElementById('set_save_html').checked = s.save_html !== false;
        if (document.getElementById('set_save_screenshot')) document.getElementById('set_save_screenshot').checked = s.save_screenshot !== false;
        if (document.getElementById('set_save_change_only')) document.getElementById('set_save_change_only').checked = s.save_only_on_bank_change !== false;
        if (document.getElementById('set_auto_repair')) document.getElementById('set_auto_repair').checked = s.auto_repair_saved_pages === true;

        if (document.getElementById('set_headless')) document.getElementById('set_headless').checked = s.headless || false;
        if (document.getElementById('set_browser_display_mode')) document.getElementById('set_browser_display_mode').value = s.browser_display_mode || 'external';
        if (document.getElementById('set_default_bank_mode')) document.getElementById('set_default_bank_mode').value = s.default_bank_mode || 'online';
        if (document.getElementById('set_browser_page_zoom')) document.getElementById('set_browser_page_zoom').value = String(s.browser_page_zoom || '1.25');
        if (document.getElementById('set_focus_view_enabled')) document.getElementById('set_focus_view_enabled').checked = s.focus_view_enabled !== false;
        applyBrowserDisplayMode(s.browser_display_mode || 'external');

        if (document.getElementById('set_captcha_mode')) document.getElementById('set_captcha_mode').value = s.captcha_mode || 'model'; 

        setSubmissionDefaults(s);
        if (document.getElementById('set_final_submit')) document.getElementById('set_final_submit').checked = Boolean(s.auto_final_submit ?? s.final_submit ?? false);"""

    if old_load_chunk in js:
        js = js.replace(old_load_chunk, new_load_chunk)
        print("SUCCESS: Updated loadSettings in script.js")
    else:
        print("NOTE: old_load_chunk not found in script.js (checking normalization)")

    # 2. Update saveSettings() payload construction
    old_save_chunk = """    const payload = {

        captcha_delay: document.getElementById('set_captcha').value,

        retry_count: document.getElementById('set_retry').value,
        automation_mode: document.getElementById('set_automation_mode').value,
        default_timeout_ms: Number(document.getElementById('set_default_timeout').value),
        navigation_timeout_ms: Number(document.getElementById('set_navigation_timeout').value),
        retry_delay: Number(document.getElementById('set_retry_delay').value),
        captcha_poll_interval: Number(document.getElementById('set_captcha_poll').value),
        captcha_retry_delay: Number(document.getElementById('set_captcha_retry_delay').value),
        captcha_max_retries: Number(document.getElementById('set_captcha_max').value),
        bank_refresh_interval: Number(document.getElementById('set_bank_refresh').value || 5),
        bank_list_refresh_seconds: Number(document.getElementById('set_bank_refresh').value || 5),
        bank_capacity_recheck_seconds: Number(document.getElementById('set_bank_capacity_recheck')?.value || 20),
        otp_validity_seconds: Number(document.getElementById('set_otp_validity')?.value || 120),
        resume_from_checkpoint: document.getElementById('set_resume_checkpoint') ? document.getElementById('set_resume_checkpoint').checked : true,
        repair_page_enabled: document.getElementById('set_repair_page') ? document.getElementById('set_repair_page').checked : true,
        pause_for_manual_captcha: document.getElementById('set_pause_manual_captcha') ? document.getElementById('set_pause_manual_captcha').checked : true,
        pause_for_manual_otp: document.getElementById('set_pause_manual_otp') ? document.getElementById('set_pause_manual_otp').checked : true,
        allow_reserve_bank: document.getElementById('set_allow_reserve_bank') ? document.getElementById('set_allow_reserve_bank').checked : false,
        default_bank_mode: document.getElementById('set_default_bank_mode') ? document.getElementById('set_default_bank_mode').value : 'online',
        diagnostic_logging_enabled: document.getElementById('set_diagnostic_logging') ? document.getElementById('set_diagnostic_logging').checked : true,
        auto_final_submit: document.getElementById('set_final_submit').checked,
        bank_monitor_enabled: document.getElementById('set_bank_monitor').checked,
        save_page_enabled: document.getElementById('set_save_page').checked,
        save_html: document.getElementById('set_save_html').checked,
        save_screenshot: document.getElementById('set_save_screenshot').checked,
        save_only_on_bank_change: document.getElementById('set_save_change_only').checked,
        auto_repair_saved_pages: document.getElementById('set_auto_repair').checked,"""

    new_save_chunk = """    const payload = {

        captcha_delay: document.getElementById('set_captcha')?.value || 0.1,

        retry_count: Number(document.getElementById('set_retry')?.value || 5),
        automation_mode: document.getElementById('set_automation_mode')?.value || 'normal',
        default_timeout_ms: Number(document.getElementById('set_default_timeout')?.value || 5000),
        navigation_timeout_ms: Number(document.getElementById('set_navigation_timeout')?.value || 15000),
        retry_delay: Number(document.getElementById('set_retry_delay')?.value || 0.25),
        captcha_poll_interval: Number(document.getElementById('set_captcha_poll')?.value || 0.1),
        captcha_retry_delay: Number(document.getElementById('set_captcha_retry_delay')?.value || 0.25),
        captcha_max_retries: Number(document.getElementById('set_captcha_max')?.value || 3),
        bank_refresh_interval: Number(document.getElementById('set_bank_refresh')?.value || 5),
        bank_list_refresh_seconds: Number(document.getElementById('set_bank_refresh')?.value || 5),
        bank_capacity_recheck_seconds: Number(document.getElementById('set_bank_capacity_recheck')?.value || 20),
        otp_validity_seconds: Number(document.getElementById('set_otp_validity')?.value || 120),
        resume_from_checkpoint: document.getElementById('set_resume_checkpoint') ? document.getElementById('set_resume_checkpoint').checked : true,
        repair_page_enabled: document.getElementById('set_repair_page') ? document.getElementById('set_repair_page').checked : true,
        pause_for_manual_captcha: document.getElementById('set_pause_manual_captcha') ? document.getElementById('set_pause_manual_captcha').checked : true,
        pause_for_manual_otp: document.getElementById('set_pause_manual_otp') ? document.getElementById('set_pause_manual_otp').checked : true,
        allow_reserve_bank: document.getElementById('set_allow_reserve_bank') ? document.getElementById('set_allow_reserve_bank').checked : false,
        default_bank_mode: document.getElementById('set_default_bank_mode') ? document.getElementById('set_default_bank_mode').value : 'online',
        diagnostic_logging_enabled: document.getElementById('set_diagnostic_logging') ? document.getElementById('set_diagnostic_logging').checked : true,
        auto_final_submit: document.getElementById('set_final_submit') ? document.getElementById('set_final_submit').checked : false,
        bank_monitor_enabled: document.getElementById('set_bank_monitor') ? document.getElementById('set_bank_monitor').checked : true,
        save_page_enabled: document.getElementById('set_save_page') ? document.getElementById('set_save_page').checked : true,
        save_html: document.getElementById('set_save_html') ? document.getElementById('set_save_html').checked : true,
        save_screenshot: document.getElementById('set_save_screenshot') ? document.getElementById('set_save_screenshot').checked : true,
        save_only_on_bank_change: document.getElementById('set_save_change_only') ? document.getElementById('set_save_change_only').checked : true,
        auto_repair_saved_pages: document.getElementById('set_auto_repair') ? document.getElementById('set_auto_repair').checked : false,"""

    if old_save_chunk in js:
        js = js.replace(old_save_chunk, new_save_chunk)
        print("SUCCESS: Updated saveSettings in script.js")
    else:
        print("NOTE: old_save_chunk not found in script.js (checking normalization)")

    # 3. Clean up final_submit line in saveSettings
    old_final_submit_line = "        final_submit: document.getElementById('set_final_submit').checked,"
    new_final_submit_line = "        final_submit: document.getElementById('set_final_submit') ? document.getElementById('set_final_submit').checked : false,"
    if old_final_submit_line in js:
        js = js.replace(old_final_submit_line, new_final_submit_line)
        print("SUCCESS: Updated final_submit fallback in saveSettings")

    with open('static/script.js', 'w', encoding='utf-8') as f:
        f.write(js)
    print("Saved static/script.js")

if __name__ == '__main__':
    clean_index_html()
    clean_script_js()
