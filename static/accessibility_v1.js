(() => {
  'use strict';

  const SHORTCUTS = {
    '1': 'dashboard',
    '2': 'add',
    '3': 'list',
    '4': 'bank-select',
    '5': 'status',
    '6': 'recover',
    '7': 'delete-req',
    '8': 'settings',
    '9': 'model-lab',
  };

  const LIVE_TARGETS = [
    '#connectionStatus',
    '#otpStatus',
    '#manualOtpStatus',
    '#activeApplicantStatus',
    '#mahanLivePill',
  ];

  let speechEnabled = localStorage.getItem('mahanbot-a11y-speech') === '1';
  let lastAnnouncement = '';
  let liveObserver = null;

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function textOf(node) {
    return String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function ensureLiveRegions() {
    if (!qs('#a11yPolite')) {
      const polite = document.createElement('div');
      polite.id = 'a11yPolite';
      polite.className = 'a11y-sr-only';
      polite.setAttribute('role', 'status');
      polite.setAttribute('aria-live', 'polite');
      polite.setAttribute('aria-atomic', 'true');
      document.body.appendChild(polite);
    }
    if (!qs('#a11yAssertive')) {
      const assertive = document.createElement('div');
      assertive.id = 'a11yAssertive';
      assertive.className = 'a11y-sr-only';
      assertive.setAttribute('role', 'alert');
      assertive.setAttribute('aria-live', 'assertive');
      assertive.setAttribute('aria-atomic', 'true');
      document.body.appendChild(assertive);
    }
  }

  function speak(text) {
    if (!speechEnabled || !('speechSynthesis' in window) || !text) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'fa-IR';
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
  }

  function announce(text, priority = 'polite', forceSpeech = false) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    if (!value || value === lastAnnouncement) return;
    lastAnnouncement = value;
    const region = qs(priority === 'assertive' ? '#a11yAssertive' : '#a11yPolite');
    if (region) {
      region.textContent = '';
      window.setTimeout(() => { region.textContent = value; }, 20);
    }
    if (forceSpeech || speechEnabled) speak(value);
  }

  function addSkipLink() {
    if (qs('.a11y-skip-link')) return;
    const main = qs('main.main-content');
    if (!main) return;
    main.id = main.id || 'mainContent';
    main.tabIndex = -1;
    main.setAttribute('role', 'main');

    const link = document.createElement('a');
    link.href = `#${main.id}`;
    link.className = 'a11y-skip-link';
    link.textContent = 'پرش به محتوای اصلی';
    link.addEventListener('click', () => window.setTimeout(() => main.focus(), 0));
    document.body.prepend(link);
  }

  function addToolbar() {
    if (qs('#a11yToolbar')) return;
    const toolbar = document.createElement('div');
    toolbar.id = 'a11yToolbar';
    toolbar.className = 'a11y-toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'ابزارهای دسترس‌پذیری');
    toolbar.innerHTML = `
      <button type="button" id="a11ySpeechToggle" class="a11y-toolbar-button" aria-pressed="${speechEnabled}">
        ${speechEnabled ? 'خاموش کردن خواندن صوتی' : 'روشن کردن خواندن صوتی'}
      </button>
      <button type="button" id="a11yRepeatStatus" class="a11y-toolbar-button">خواندن وضعیت فعلی</button>
      <button type="button" id="a11yShortcutHelp" class="a11y-toolbar-button" aria-expanded="false">راهنمای میان‌برها</button>
      <div id="a11yShortcutPanel" class="a11y-shortcut-panel" hidden>
        <strong>میان‌برها</strong>
        <span>Alt+1 داشبورد، Alt+2 ثبت متقاضی، Alt+3 فهرست، Alt+4 انتخاب بانک، Alt+5 وضعیت، Alt+8 تنظیمات، Alt+O کد یک‌بارمصرف، Alt+H همین راهنما</span>
      </div>
    `;
    const main = qs('main.main-content');
    if (main) main.prepend(toolbar);

    qs('#a11ySpeechToggle')?.addEventListener('click', (event) => {
      speechEnabled = !speechEnabled;
      localStorage.setItem('mahanbot-a11y-speech', speechEnabled ? '1' : '0');
      const button = event.currentTarget;
      button.setAttribute('aria-pressed', String(speechEnabled));
      button.textContent = speechEnabled ? 'خاموش کردن خواندن صوتی' : 'روشن کردن خواندن صوتی';
      announce(speechEnabled ? 'خواندن صوتی روشن شد' : 'خواندن صوتی خاموش شد', 'assertive', speechEnabled);
      if (!speechEnabled && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    });

    qs('#a11yRepeatStatus')?.addEventListener('click', () => announceCurrentStatus(true));
    qs('#a11yShortcutHelp')?.addEventListener('click', (event) => {
      const panel = qs('#a11yShortcutPanel');
      if (!panel) return;
      const open = panel.hidden;
      panel.hidden = !open;
      event.currentTarget.setAttribute('aria-expanded', String(open));
      announce(open ? textOf(panel) : 'راهنمای میان‌برها بسته شد');
    });
  }

  function enhanceLandmarks() {
    const sidebar = qs('nav.sidebar');
    if (sidebar) {
      sidebar.setAttribute('aria-label', 'منوی اصلی MahanBot');
      sidebar.setAttribute('role', 'navigation');
    }
    qsa('.sidebar .nav-item').forEach((item) => {
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      const label = textOf(item);
      if (label) item.setAttribute('aria-label', label);
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.click();
        }
      });
    });
  }

  function enhanceForms() {
    qsa('input, select, textarea').forEach((field) => {
      if (!field.id) return;
      const explicit = qs(`label[for="${CSS.escape(field.id)}"]`);
      const nearby = field.closest('.mb-3, .col-md-3, .col-md-6, .col-12, .input-group')?.querySelector('label');
      const labelText = textOf(explicit || nearby) || field.getAttribute('placeholder') || field.id;
      if (!field.getAttribute('aria-label') && labelText) field.setAttribute('aria-label', labelText);
      if (!field.getAttribute('autocomplete')) field.setAttribute('autocomplete', 'off');
    });

    ['otpInput', 'manualOtpInput'].forEach((id) => {
      const field = qs(`#${id}`);
      if (!field) return;
      field.setAttribute('inputmode', 'numeric');
      field.setAttribute('pattern', '[0-9]*');
      field.setAttribute('autocomplete', 'one-time-code');
      field.setAttribute('aria-describedby', 'a11yOtpHelp');
    });

    if (!qs('#a11yOtpHelp')) {
      const help = document.createElement('p');
      help.id = 'a11yOtpHelp';
      help.className = 'a11y-sr-only';
      help.textContent = 'کد یک‌بارمصرف را پس از دریافت پیامک وارد کنید. با کلید اینتر ارسال می‌شود.';
      document.body.appendChild(help);
    }

    qsa('button').forEach((button) => {
      if (!button.type) button.type = 'button';
      if (!button.getAttribute('aria-label')) {
        const label = textOf(button);
        if (label) button.setAttribute('aria-label', label);
      }
    });
  }

  function enhanceTables() {
    qsa('table').forEach((table, index) => {
      if (!table.getAttribute('aria-label')) table.setAttribute('aria-label', `جدول اطلاعات شماره ${index + 1}`);
      qsa('thead th', table).forEach((header) => header.setAttribute('scope', 'col'));
      qsa('tbody tr', table).forEach((row) => {
        const first = row.querySelector('th, td');
        if (first && first.tagName === 'TH') first.setAttribute('scope', 'row');
      });
    });
  }

  function activeViewTitle() {
    const active = qs('.view-section.active-view');
    return textOf(active?.querySelector('h1, h2, h3')) || 'صفحه فعال';
  }

  function focusActiveView() {
    const active = qs('.view-section.active-view');
    if (!active) return;
    const heading = active.querySelector('h1, h2, h3');
    const target = heading || active;
    target.tabIndex = -1;
    target.focus({ preventScroll: false });
    announce(`${activeViewTitle()} باز شد`);
  }

  function wrapSwitchView() {
    const original = window.switchView;
    if (typeof original !== 'function' || original.__a11yWrapped) return;
    const wrapped = function (...args) {
      const result = original.apply(this, args);
      window.setTimeout(() => {
        enhanceForms();
        enhanceTables();
        focusActiveView();
      }, 40);
      return result;
    };
    wrapped.__a11yWrapped = true;
    window.switchView = wrapped;
  }

  function announceCurrentStatus(forceSpeech = false) {
    const applicant = textOf(qs('#activeApplicantName'));
    const status = textOf(qs('#activeApplicantStatus'));
    const connection = textOf(qs('#connectionStatus'));
    const view = activeViewTitle();
    const parts = [`بخش فعال: ${view}`];
    if (applicant && applicant !== '--') parts.push(`متقاضی فعال: ${applicant}`);
    if (status && status !== '--') parts.push(`وضعیت: ${status}`);
    if (connection) parts.push(`اتصال: ${connection}`);
    announce(parts.join('. '), 'polite', forceSpeech);
  }

  function focusOtp() {
    const field = qs('#manualOtpInput') || qs('#otpInput');
    if (!field) {
      announce('فیلد کد یک‌بارمصرف در این صفحه وجود ندارد', 'assertive');
      return;
    }
    field.focus();
    field.select?.();
    announce('فیلد کد یک‌بارمصرف فعال شد');
  }

  function openViewByShortcut(key) {
    const view = SHORTCUTS[key];
    if (!view || typeof window.switchView !== 'function') return;
    const navItem = qsa('.sidebar .nav-item').find((item) => item.getAttribute('onclick')?.includes(`'${view}'`));
    window.switchView(view, navItem || undefined);
  }

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      const key = event.key.toLowerCase();
      if (SHORTCUTS[key]) {
        event.preventDefault();
        openViewByShortcut(key);
      } else if (key === 'o') {
        event.preventDefault();
        focusOtp();
      } else if (key === 'h') {
        event.preventDefault();
        qs('#a11yShortcutHelp')?.click();
      } else if (key === 'r') {
        event.preventDefault();
        announceCurrentStatus(true);
      }
    });
  }

  function observeLiveTargets() {
    if (liveObserver) return;
    liveObserver = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        const target = mutation.target.nodeType === Node.TEXT_NODE ? mutation.target.parentElement : mutation.target;
        const text = textOf(target);
        if (!text || text.length > 240) continue;
        const urgent = /خطا|ناموفق|قطع|متوقف|منقضی|هشدار/i.test(text);
        announce(text, urgent ? 'assertive' : 'polite');
      }
      enhanceForms();
      enhanceTables();
    });
    LIVE_TARGETS.forEach((selector) => {
      const node = qs(selector);
      if (node) liveObserver.observe(node, { childList: true, subtree: true, characterData: true, attributes: true });
    });

    const main = qs('main.main-content');
    if (main) {
      liveObserver.observe(main, { childList: true, subtree: true });
    }
  }

  function monitorSmsArrival() {
    const observer = new MutationObserver(() => {
      const banner = qs('[data-sms-notify], .sms-notify-banner, #smsNotifyBanner');
      if (!banner || banner.dataset.a11yAnnounced === '1') return;
      banner.dataset.a11yAnnounced = '1';
      announce('پیامک جدید رسید. فیلد کد یک‌بارمصرف آماده ورود است.', 'assertive', true);
      window.setTimeout(focusOtp, 100);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    document.documentElement.classList.add('mahan-a11y-ready');
    ensureLiveRegions();
    addSkipLink();
    addToolbar();
    enhanceLandmarks();
    enhanceForms();
    enhanceTables();
    wrapSwitchView();
    setupKeyboardShortcuts();
    observeLiveTargets();
    monitorSmsArrival();
    window.setTimeout(() => {
      wrapSwitchView();
      announce('حالت دسترس‌پذیری MahanBot فعال شد. برای راهنمای میان‌برها Alt و H را بزنید.');
    }, 150);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
