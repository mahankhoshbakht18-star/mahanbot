(() => {
  'use strict';

  const API_BASE = `${location.protocol}//${location.host}`;
  let selectedFile = null;
  let statusRefreshTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function setBusy(isBusy) {
    const button = byId('modelLabPredictButton');
    const loader = byId('modelLabLoader');
    if (button) button.disabled = isBusy || !selectedFile;
    if (loader) loader.hidden = !isBusy;
  }

  function setControlBusy(isBusy) {
    const loadButton = byId('modelLabLoadButton');
    const unloadButton = byId('modelLabUnloadButton');
    if (loadButton) loadButton.disabled = isBusy;
    if (unloadButton) unloadButton.disabled = isBusy;
  }

  function renderStatus(model) {
    const target = byId('modelLabStatus');
    if (!target) return;
    const available = Boolean(model?.available);
    const loaded = Boolean(model?.loaded);
    const stateClass = loaded ? 'is-ready' : available ? 'is-idle' : 'is-error';
    const stateText = loaded
      ? 'مدل به هسته بات متصل و آماده است'
      : available
        ? 'فایل مدل شناسایی شد؛ در حال آماده‌سازی'
        : 'فایل مدل پیدا نشد';
    target.className = `model-lab-status ${stateClass}`;
    target.innerHTML = `
      <div class="model-lab-status-icon"><i class="fas fa-brain"></i></div>
      <div>
        <strong>${stateText}</strong>
        <div class="model-lab-meta">
          دستگاه پردازش: ${escapeHtml(model?.device || '--')} · فایل: ${escapeHtml(model?.model_file || '--')}
        </div>
        <div class="model-lab-meta">مسیر اتصال: CaptchaService.local_test</div>
        ${model?.load_error ? `<div class="model-lab-error">${escapeHtml(model.load_error)}</div>` : ''}
      </div>
    `;
  }

  function scheduleStatusRefresh(model) {
    if (statusRefreshTimer) {
      clearTimeout(statusRefreshTimer);
      statusRefreshTimer = null;
    }
    if (model?.available && !model?.loaded && !model?.load_error && !document.hidden) {
      statusRefreshTimer = setTimeout(refreshStatus, 1500);
    }
  }

  async function refreshStatus() {
    try {
      const response = await fetch(`${API_BASE}/api/v1/model/status`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const model = payload.model || {};
      renderStatus(model);
      scheduleStatusRefresh(model);
      return model;
    } catch (error) {
      const model = { available: false, load_error: String(error) };
      renderStatus(model);
      scheduleStatusRefresh(model);
      return model;
    }
  }

  function renderPreview(file) {
    const preview = byId('modelLabPreview');
    const name = byId('modelLabFileName');
    if (!preview || !name) return;
    name.textContent = file ? `${file.name} · ${Math.ceil(file.size / 1024)} KB` : 'هنوز تصویری انتخاب نشده است';
    if (!file) {
      preview.removeAttribute('src');
      preview.hidden = true;
      return;
    }
    const reader = new FileReader();
    reader.addEventListener('load', () => {
      preview.src = String(reader.result || '');
      preview.hidden = false;
    });
    reader.readAsDataURL(file);
  }

  async function loadModel() {
    setControlBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/v1/model/load`, { method: 'POST' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      renderStatus(payload.model || {});
    } catch (error) {
      renderStatus({ available: true, loaded: false, load_error: String(error) });
    } finally {
      setControlBusy(false);
      await refreshStatus();
    }
  }

  async function predict() {
    if (!selectedFile) return;
    const resultBox = byId('modelLabResult');
    setBusy(true);
    if (resultBox) {
      resultBox.className = 'model-lab-result is-waiting';
      resultBox.innerHTML = '<span>در حال اجرای مدل روی تصویر آزمایشی…</span>';
    }
    try {
      const form = new FormData();
      form.append('image', selectedFile, selectedFile.name);
      const response = await fetch(`${API_BASE}/api/v1/model/predict`, {
        method: 'POST',
        body: form,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const result = payload.result || {};
      const percentage = Math.round(Number(result.confidence || 0) * 100);
      if (resultBox) {
        resultBox.className = 'model-lab-result is-success';
        resultBox.innerHTML = `
          <div class="model-lab-result-label">خروجی مدل محلی بات</div>
          <div class="model-lab-prediction" dir="ltr">${escapeHtml(result.prediction || '—')}</div>
          <div class="model-lab-confidence">
            <span>اطمینان تقریبی</span>
            <strong>${percentage.toLocaleString('fa-IR')}٪</strong>
          </div>
          <div class="model-lab-progress"><span style="width:${Math.max(0, Math.min(100, percentage))}%"></span></div>
          <small>مدل به هسته بات متصل است، اما نتیجه به Job یا مرورگر زنده ارسال و ثبت خودکار نمی‌شود.</small>
        `;
      }
      await refreshStatus();
    } catch (error) {
      if (resultBox) {
        resultBox.className = 'model-lab-result is-error';
        resultBox.innerHTML = `<strong>اجرای مدل ناموفق بود</strong><small>${escapeHtml(String(error))}</small>`;
      }
    } finally {
      setBusy(false);
    }
  }

  async function unloadModel() {
    setControlBusy(true);
    try {
      await fetch(`${API_BASE}/api/v1/model/unload`, { method: 'POST' });
    } finally {
      setControlBusy(false);
      await refreshStatus();
    }
  }

  function installPanel() {
    if (byId('view-model-lab')) return;

    const nav = document.querySelector('.sidebar ul.nav');
    if (nav) {
      const item = document.createElement('li');
      item.className = 'nav-item model-lab-nav';
      item.setAttribute('onclick', "switchView('model-lab', this)");
      item.innerHTML = '<a class="nav-link"><i class="fas fa-brain"></i><span>آزمایشگاه مدل</span></a>';
      const settingsItem = Array.from(nav.querySelectorAll('.nav-item')).find((node) =>
        node.getAttribute('onclick')?.includes("'settings'")
      );
      if (settingsItem) settingsItem.before(item);
      else nav.appendChild(item);
    }

    const main = document.querySelector('main.main-content');
    if (!main) return;
    const section = document.createElement('div');
    section.id = 'view-model-lab';
    section.className = 'view-section model-lab-view';
    section.innerHTML = `
      <div class="model-lab-heading">
        <div>
          <span class="model-lab-kicker">پردازش محلی</span>
          <h2>آزمایشگاه مدل</h2>
          <p>مدل اختصاصی از مسیر پروژه بارگذاری و در هسته محلی بات اجرا می‌شود.</p>
        </div>
        <div class="model-lab-actions">
          <button id="modelLabLoadButton" class="btn btn-primary" type="button">
            <i class="fas fa-plug"></i> بارگذاری مدل
          </button>
          <button id="modelLabUnloadButton" class="btn btn-outline-secondary" type="button">
            <i class="fas fa-power-off"></i> آزادسازی حافظه
          </button>
        </div>
      </div>

      <div class="model-lab-notice">
        <i class="fas fa-shield-alt"></i>
        <div>
          <strong>اتصال مدل به بات فعال است؛ جریان زنده همچنان دستی است</strong>
          <span>مدل برای ارزیابی محلی در دسترس است و خروجی آن بدون تأیید اپراتور وارد Job بانکی یا مرورگر زنده نمی‌شود.</span>
        </div>
      </div>

      <div id="modelLabStatus" class="model-lab-status is-idle"></div>

      <div class="model-lab-grid">
        <section class="model-lab-card">
          <div class="model-lab-card-title">
            <span><i class="fas fa-image"></i></span>
            <div><h3>تصویر آزمایشی</h3><p>PNG، JPG، WEBP یا BMP تا حجم ۲ مگابایت</p></div>
          </div>
          <label class="model-lab-drop" for="modelLabFileInput">
            <input id="modelLabFileInput" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" hidden>
            <img id="modelLabPreview" alt="پیش‌نمایش تصویر آزمایشی" hidden>
            <span class="model-lab-drop-icon"><i class="fas fa-cloud-upload-alt"></i></span>
            <strong>برای انتخاب تصویر بزنید</strong>
            <small id="modelLabFileName">هنوز تصویری انتخاب نشده است</small>
          </label>
          <button id="modelLabPredictButton" class="btn btn-primary model-lab-run" type="button" disabled>
            <span id="modelLabLoader" class="spinner-border spinner-border-sm" hidden></span>
            <i class="fas fa-play"></i> اجرای مدل محلی
          </button>
        </section>

        <section class="model-lab-card">
          <div class="model-lab-card-title">
            <span><i class="fas fa-chart-line"></i></span>
            <div><h3>نتیجه ارزیابی</h3><p>خروجی CTC و اطمینان تقریبی مدل</p></div>
          </div>
          <div id="modelLabResult" class="model-lab-result is-empty">
            <i class="fas fa-flask"></i>
            <strong>آماده آزمایش</strong>
            <small>پس از انتخاب تصویر، نتیجه اینجا نمایش داده می‌شود.</small>
          </div>
        </section>
      </div>
    `;
    main.appendChild(section);

    const input = byId('modelLabFileInput');
    input?.addEventListener('change', () => {
      const file = input.files?.[0] || null;
      if (file && file.size > 2 * 1024 * 1024) {
        selectedFile = null;
        input.value = '';
        renderPreview(null);
        const result = byId('modelLabResult');
        if (result) {
          result.className = 'model-lab-result is-error';
          result.innerHTML = '<strong>حجم تصویر بیش از ۲ مگابایت است.</strong>';
        }
      } else {
        selectedFile = file;
        renderPreview(file);
      }
      setBusy(false);
    });
    byId('modelLabLoadButton')?.addEventListener('click', loadModel);
    byId('modelLabPredictButton')?.addEventListener('click', predict);
    byId('modelLabUnloadButton')?.addEventListener('click', unloadModel);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refreshStatus();
    });
    refreshStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installPanel, { once: true });
  } else {
    installPanel();
  }
})();
