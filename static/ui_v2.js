(() => {
  'use strict';

  const HEALTH_TEXT = /health\s*check|healthcheck|چک\s*هلث|بررسی\s*سلامت/i;
  const MAX_TERMINAL_ROWS = 320;
  let terminalObserver = null;
  let tableObserver = null;

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function removeObsoleteHealthUi() {
    qsa('[data-healthcheck], #healthcheck, #healthCheck, .healthcheck, .health-check').forEach((node) => node.remove());
    qsa('button, a, .card, .alert, .nav-item, .setting-row').forEach((node) => {
      const text = String(node.textContent || '').trim();
      if (text && text.length < 90 && HEALTH_TEXT.test(text)) node.remove();
    });
  }

  function updateBranding() {
    document.title = 'MahanBot | مرکز عملیات';
    const title = qs('.sidebar-header h4');
    if (title) {
      title.innerHTML = 'MahanBot <span class="badge">ONE</span>';
    }
    const icon = qs('.sidebar-header > i');
    if (icon) icon.className = 'fas fa-bolt';

    const replacements = new Map([
      ['داشبورد فرماندهی', 'داشبورد عملیات'],
      ['لیست کل متقاضیان', 'مدیریت متقاضیان'],
      ['تنظیمات پیشرفته', 'تنظیمات سامانه'],
      ['Start Register', 'شروع ثبت‌نام'],
      ['STOP Register', 'توقف ثبت‌نام'],
      ['Start Select', 'شروع انتخاب'],
      ['STOP Select', 'توقف انتخاب'],
      ['Start Status', 'شروع بررسی'],
      ['STOP Status', 'توقف بررسی'],
      ['Test Launch', 'آزمایش مرورگر'],
      ['Fast (headless)', 'اجرای سریع'],
      ['Debug (headed + slowMo 100ms)', 'حالت بررسی'],
    ]);

    qsa('th, button, .nav-link, h2, h5').forEach((node) => {
      const value = String(node.textContent || '').trim();
      const replacement = replacements.get(value);
      if (replacement) {
        if (node.children.length === 0) node.textContent = replacement;
        else {
          const textNode = Array.from(node.childNodes).find((item) => item.nodeType === Node.TEXT_NODE && item.textContent.trim());
          if (textNode) textNode.textContent = ` ${replacement}`;
        }
      }
    });
  }

  function addPageIntro() {
    const main = qs('.main-content');
    if (!main || qs('.mahan-page-intro', main)) return;
    const intro = document.createElement('section');
    intro.className = 'mahan-page-intro';
    intro.innerHTML = `
      <div>
        <strong>مرکز یکپارچه عملیات MahanBot</strong>
        <span>متقاضیان، مرورگر و اعلان پیامک در یک داشبورد سریع</span>
      </div>
      <div class="mahan-live-pill" id="mahanLivePill">سامانه آماده</div>
    `;
    main.prepend(intro);
  }

  function setupMobileMenu() {
    if (qs('.mahan-menu-toggle')) return;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'mahan-menu-toggle';
    toggle.setAttribute('aria-label', 'باز کردن منو');
    toggle.innerHTML = '<i class="fas fa-bars"></i>';

    const backdrop = document.createElement('div');
    backdrop.className = 'mahan-sidebar-backdrop';

    const close = () => document.body.classList.remove('mahan-sidebar-open');
    toggle.addEventListener('click', () => document.body.classList.toggle('mahan-sidebar-open'));
    backdrop.addEventListener('click', close);
    qsa('.sidebar .nav-item').forEach((item) => item.addEventListener('click', close));
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') close();
    });

    document.body.append(toggle, backdrop);
  }

  function trimTerminal() {
    const terminal = qs('#terminalBox');
    if (!terminal) return;
    while (terminal.children.length > MAX_TERMINAL_ROWS) {
      terminal.firstElementChild?.remove();
    }
  }

  function observeTerminal() {
    const terminal = qs('#terminalBox');
    if (!terminal || terminalObserver) return;
    terminalObserver = new MutationObserver(() => {
      if (terminal.children.length > MAX_TERMINAL_ROWS) {
        requestAnimationFrame(trimTerminal);
      }
    });
    terminalObserver.observe(terminal, { childList: true });
    trimTerminal();
  }

  function labelResponsiveTables() {
    qsa('table').forEach((table) => {
      const headers = qsa('thead th', table).map((th) => String(th.textContent || '').trim());
      if (!headers.length) return;
      qsa('tbody tr', table).forEach((row) => {
        qsa('td', row).forEach((cell, index) => {
          if (headers[index]) cell.dataset.label = headers[index];
        });
      });
    });
  }

  function observeTables() {
    if (tableObserver) return;
    const main = qs('.main-content');
    if (!main) return;
    let queued = false;
    tableObserver = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        labelResponsiveTables();
        updateLiveStatus();
        queued = false;
      });
    });
    tableObserver.observe(main, { childList: true, subtree: true });
    labelResponsiveTables();
  }

  function updateLiveStatus() {
    const pill = qs('#mahanLivePill');
    const connection = qs('#connectionStatus');
    if (!pill || !connection) return;
    const text = String(connection.textContent || '').trim();
    pill.textContent = text || 'سامانه آماده';
    pill.dataset.state = /قطع|disconnected|خطا/i.test(text) ? 'offline' : 'online';
  }

  function improveAccessibility() {
    qsa('button:not([type])').forEach((button) => button.type = 'button');
    qsa('input, select, textarea').forEach((field) => {
      if (!field.getAttribute('aria-label')) {
        const label = field.closest('.mb-3, .col-md-3, .col-md-6, .col-12')?.querySelector('label');
        const fallback = field.placeholder || field.id || 'فیلد فرم';
        field.setAttribute('aria-label', String(label?.textContent || fallback).trim());
      }
    });
  }

  function setupVisibilityPerformance() {
    document.addEventListener('visibilitychange', () => {
      try {
        if (document.hidden && typeof window.stopPolling === 'function') {
          window.stopPolling();
        } else if (!document.hidden && typeof window.isWebSocketOpen === 'function' && !window.isWebSocketOpen()) {
          if (typeof window.startPolling === 'function') window.startPolling(8000);
        }
      } catch (_) {
        // The dashboard remains usable even if the legacy script changes.
      }
    });
  }

  function setupConnectionObserver() {
    const connection = qs('#connectionStatus');
    if (!connection) return;
    new MutationObserver(updateLiveStatus).observe(connection, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
    });
    updateLiveStatus();
  }

  function preventDoubleSubmit() {
    document.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      const text = String(button.textContent || '');
      if (!/شروع|ذخیره|ارسال|توقف|حذف|بررسی/.test(text)) return;
      button.classList.add('mahan-clicked');
      window.setTimeout(() => button.classList.remove('mahan-clicked'), 420);
    }, { passive: true });
  }

  function init() {
    document.body.classList.add('mahan-ui-ready');
    removeObsoleteHealthUi();
    updateBranding();
    addPageIntro();
    setupMobileMenu();
    observeTerminal();
    observeTables();
    improveAccessibility();
    setupVisibilityPerformance();
    setupConnectionObserver();
    preventDoubleSubmit();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
