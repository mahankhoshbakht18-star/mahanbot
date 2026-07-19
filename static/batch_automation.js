(() => {
  'use strict';

  const API = '/api/v1/batch';
  let refreshTimer = null;

  const byId = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function announce(message) {
    const region = byId('batchLiveRegion');
    if (region) {
      region.textContent = '';
      window.setTimeout(() => { region.textContent = message; }, 25);
    }
    if (typeof window.mahanAnnounce === 'function') {
      window.mahanAnnounce(message, 'polite');
    }
  }

  function setBusy(isBusy) {
    document.querySelectorAll('[data-batch-action]').forEach((button) => {
      button.disabled = isBusy;
    });
  }

  function renderStatus(payload) {
    const queue = payload?.queue || {};
    const counts = queue.counts || {};
    const active = Array.isArray(queue.active) ? queue.active : [];
    const recent = Array.isArray(queue.recent) ? queue.recent : [];

    const total = byId('batchApplicantCount');
    const queued = byId('batchQueuedCount');
    const running = byId('batchRunningCount');
    const activeCount = byId('batchActiveCount');
    if (total) total.textContent = Number(payload?.applicants || 0).toLocaleString('fa-IR');
    if (queued) queued.textContent = Number(counts.queued || 0).toLocaleString('fa-IR');
    if (running) running.textContent = Number(counts.running || 0).toLocaleString('fa-IR');
    if (activeCount) activeCount.textContent = Number(active.length).toLocaleString('fa-IR');

    const list = byId('batchRecentJobs');
    if (!list) return;
    if (!recent.length) {
      list.innerHTML = '<div class="batch-empty">هنوز Job ثبت نشده است.</div>';
      return;
    }
    list.innerHTML = recent.slice(0, 12).map((job) => `
      <article class="batch-job" tabindex="0">
        <div>
          <strong>${escapeHtml(job.bot_name || '--')}</strong>
          <span dir="ltr">${escapeHtml(job.nid || '--')}</span>
        </div>
        <span class="batch-job-status" data-status="${escapeHtml(job.status || 'unknown')}">${escapeHtml(job.status || 'unknown')}</span>
      </article>
    `).join('');
  }

  async function refreshStatus() {
    try {
      const response = await fetch(`${API}/status`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderStatus(await response.json());
    } catch (error) {
      announce(`دریافت وضعیت اجرای گروهی ناموفق بود: ${String(error)}`);
    }
  }

  async function startBatch(botName) {
    const loanType = byId('batchLoanType')?.value || 'rbtnNaghdi';
    setBusy(true);
    announce(`در حال صف‌بندی عملیات ${botName} برای متقاضیان.`);
    try {
      const response = await fetch(`${API}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bot_name: botName,
          nids: [],
          loan_type: botName === 'select' ? loanType : null,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const batch = payload.batch || {};
      const message = `${Number(batch.queued?.length || 0).toLocaleString('fa-IR')} Job در صف قرار گرفت و ${Number(batch.skipped?.length || 0).toLocaleString('fa-IR')} مورد تکراری رد شد.`;
      announce(message);
      renderStatus(payload);
    } catch (error) {
      announce(`شروع اجرای گروهی ناموفق بود: ${String(error)}`);
    } finally {
      setBusy(false);
      await refreshStatus();
    }
  }

  async function cancelActive() {
    setBusy(true);
    announce('در حال ارسال فرمان توقف برای همه Jobهای فعال.');
    try {
      const response = await fetch(`${API}/cancel-active`, { method: 'POST' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      announce(`${Number(payload.cancelled?.length || 0).toLocaleString('fa-IR')} Job در حال توقف است.`);
      renderStatus(payload);
    } catch (error) {
      announce(`توقف گروهی ناموفق بود: ${String(error)}`);
    } finally {
      setBusy(false);
      await refreshStatus();
    }
  }

  function installPanel() {
    if (byId('view-batch-automation')) return;

    const nav = document.querySelector('.sidebar ul.nav');
    if (nav) {
      const item = document.createElement('li');
      item.className = 'nav-item batch-automation-nav';
      item.setAttribute('onclick', "switchView('batch-automation', this)");
      item.innerHTML = '<a class="nav-link" href="#view-batch-automation"><i class="fas fa-layer-group" aria-hidden="true"></i><span>اجرای گروهی</span></a>';
      const settings = Array.from(nav.querySelectorAll('.nav-item')).find((node) =>
        node.getAttribute('onclick')?.includes("'settings'")
      );
      if (settings) settings.before(item);
      else nav.appendChild(item);
    }

    const main = document.querySelector('main.main-content');
    if (!main) return;

    const section = document.createElement('section');
    section.id = 'view-batch-automation';
    section.className = 'view-section batch-automation-view';
    section.setAttribute('aria-labelledby', 'batchAutomationTitle');
    section.innerHTML = `
      <div id="batchLiveRegion" class="visually-hidden" role="status" aria-live="polite" aria-atomic="true"></div>
      <header class="batch-heading">
        <div>
          <span class="batch-kicker">صف یکپارچه MahanBot</span>
          <h2 id="batchAutomationTitle" tabindex="-1">اجرای گروهی یک‌کلیکی</h2>
          <p>یکی از عملیات ثبت، انتخاب بانک یا استعلام را برای تمام متقاضیان موجود در دیتابیس صف‌بندی می‌کند.</p>
        </div>
        <button type="button" class="btn btn-outline-danger" data-batch-action id="batchCancelButton">
          <i class="fas fa-stop-circle" aria-hidden="true"></i> توقف همه Jobهای فعال
        </button>
      </header>

      <div class="batch-notice" role="note">
        <i class="fas fa-universal-access" aria-hidden="true"></i>
        <div><strong>جریان یکپارچه و دسترس‌پذیر</strong><span>داشبورد، دیتابیس، صف Job، مرورگر، اعلان رسیدن پیامک و لاگ به هم متصل‌اند. در نقاط کپچا، OTP و تأیید نهایی، مرورگر و Screen Reader کنترل را به کاربر می‌دهند.</span></div>
      </div>

      <div class="batch-stats" aria-label="آمار صف اجرا">
        <article><span>متقاضیان</span><strong id="batchApplicantCount">۰</strong></article>
        <article><span>در صف</span><strong id="batchQueuedCount">۰</strong></article>
        <article><span>در حال اجرا</span><strong id="batchRunningCount">۰</strong></article>
        <article><span>کل فعال</span><strong id="batchActiveCount">۰</strong></article>
      </div>

      <div class="batch-grid">
        <section class="batch-card">
          <h3>انتخاب عملیات</h3>
          <label for="batchLoanType">نوع تسهیلات برای انتخاب بانک</label>
          <select id="batchLoanType" class="form-select">
            <option value="rbtnNaghdi">تسهیلات نقدی</option>
            <option value="rbtnKala">تسهیلات کالایی</option>
          </select>
          <div class="batch-actions">
            <button type="button" class="btn btn-primary" data-batch-action data-bot="register"><i class="fas fa-user-plus" aria-hidden="true"></i> شروع ثبت برای همه</button>
            <button type="button" class="btn btn-info" data-batch-action data-bot="select"><i class="fas fa-university" aria-hidden="true"></i> شروع انتخاب برای همه</button>
            <button type="button" class="btn btn-warning" data-batch-action data-bot="status"><i class="fas fa-search" aria-hidden="true"></i> شروع استعلام برای همه</button>
          </div>
        </section>

        <section class="batch-card">
          <div class="batch-card-title"><h3>آخرین Jobها</h3><button type="button" class="btn btn-sm btn-outline-secondary" id="batchRefreshButton">به‌روزرسانی</button></div>
          <div id="batchRecentJobs" class="batch-job-list" aria-live="polite"><div class="batch-empty">در حال دریافت وضعیت…</div></div>
        </section>
      </div>
    `;
    main.appendChild(section);

    section.querySelectorAll('[data-bot]').forEach((button) => {
      button.addEventListener('click', () => startBatch(button.dataset.bot));
    });
    byId('batchCancelButton')?.addEventListener('click', cancelActive);
    byId('batchRefreshButton')?.addEventListener('click', refreshStatus);

    refreshStatus();
    refreshTimer = window.setInterval(() => {
      if (!document.hidden && byId('view-batch-automation')?.classList.contains('active')) {
        refreshStatus();
      }
    }, 3000);
  }

  window.addEventListener('beforeunload', () => {
    if (refreshTimer) window.clearInterval(refreshTimer);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installPanel, { once: true });
  } else {
    installPanel();
  }
})();
