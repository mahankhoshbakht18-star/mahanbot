(() => {
  'use strict';

  const REQUEST_TIMEOUT_MS = 20000;

  function byId(id) {
    return document.getElementById(id);
  }

  function safeText(value, fallback = '') {
    const text = String(value ?? '').trim();
    return text || fallback;
  }

  function normalizeDigits(value) {
    return String(value ?? '')
      .replace(/[۰-۹]/g, (char) => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(char)))
      .replace(/[٠-٩]/g, (char) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(char)));
  }

  function clearNode(node) {
    if (node) node.replaceChildren();
  }

  function appendTextCell(row, value, className = '') {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = safeText(value, '—');
    row.appendChild(cell);
    return cell;
  }

  function statusBadge(status) {
    try {
      return typeof getStatusBadge === 'function' ? getStatusBadge(status) : 'bg-secondary';
    } catch (_) {
      return 'bg-secondary';
    }
  }

  function statusText(status) {
    try {
      return typeof translateStatus === 'function' ? translateStatus(status) : safeText(status, '—');
    } catch (_) {
      return safeText(status, '—');
    }
  }

  function appendStatusCell(row, status) {
    const cell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `badge ${statusBadge(status)}`;
    badge.textContent = statusText(status);
    cell.appendChild(badge);
    row.appendChild(cell);
    return cell;
  }

  function actionButton(label, className, iconClass, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.addEventListener('click', handler);
    if (iconClass) {
      const icon = document.createElement('i');
      icon.className = iconClass;
      button.appendChild(icon);
      button.append(' ');
    }
    button.append(label);
    return button;
  }

  function actionCell(row, button) {
    const cell = document.createElement('td');
    cell.appendChild(button);
    row.appendChild(cell);
  }

  function applicantData(user) {
    try {
      return typeof parseUserData === 'function' ? parseUserData(user?.data) : (user?.data || {});
    } catch (_) {
      return {};
    }
  }

  async function hardenedApiCall(path, method = 'GET', body) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const options = {
      method: String(method || 'GET').toUpperCase(),
      signal: controller.signal,
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    };
    if (body !== undefined && body !== null) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    try {
      const response = await fetch(`${location.origin}${path}`, options);
      const contentType = String(response.headers.get('content-type') || '').toLowerCase();
      const payload = contentType.includes('application/json')
        ? await response.json().catch(() => ({}))
        : { detail: await response.text().catch(() => '') };
      if (!response.ok) {
        const detail = payload?.detail;
        const message = typeof detail === 'string'
          ? detail
          : detail?.message || payload?.message || `HTTP ${response.status}`;
        throw new Error(message);
      }
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error('مهلت پاسخ سرور تمام شد.');
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function installApiHardening() {
    if (typeof apiCall === 'function') apiCall = hardenedApiCall;
  }

  function installActiveApplicantFix() {
    if (typeof renderActiveApplicantCard !== 'function') return;
    renderActiveApplicantCard = function renderActiveApplicantCardSafe(user) {
      const nameEl = byId('activeApplicantName');
      const nidEl = byId('activeApplicantNid');
      const mobileEl = byId('activeApplicantMobile');
      const legacyMetaEl = byId('activeApplicantMeta');
      const statusEl = byId('activeApplicantStatus');
      const bankListEl = byId('activeBankList');
      const manualStatusEl = byId('manualOtpStatus');
      if (!nameEl || !statusEl || !bankListEl) return;

      clearNode(bankListEl);
      if (!user) {
        nameEl.textContent = 'بدون متقاضی فعال';
        if (nidEl) nidEl.textContent = '—';
        if (mobileEl) mobileEl.textContent = '';
        if (legacyMetaEl) legacyMetaEl.textContent = '—';
        statusEl.textContent = 'آماده';
        statusEl.className = 'badge bg-secondary';
        if (manualStatusEl) manualStatusEl.textContent = '';
        return;
      }

      const data = applicantData(user);
      const priority = Array.isArray(data.priority_banks)
        ? data.priority_banks
        : Array.isArray(data.banks) ? data.banks : [];
      const stopped = Array.isArray(data.stopped_banks) ? data.stopped_banks : [];
      const nid = safeText(user.national_id, '—');
      const mobile = safeText(data.mobile, '—');

      nameEl.textContent = safeText(user.full_name, 'بدون نام');
      if (nidEl) nidEl.textContent = `کد ملی: ${nid}`;
      if (mobileEl) mobileEl.textContent = ` · موبایل: ${mobile}`;
      if (legacyMetaEl) legacyMetaEl.textContent = `کد ملی: ${nid} | موبایل: ${mobile}`;
      statusEl.textContent = statusText(user.status);
      statusEl.className = `badge ${statusBadge(user.status)}`;

      if (!priority.length) {
        const empty = document.createElement('span');
        empty.className = 'text-muted small';
        empty.textContent = 'بانکی برای این متقاضی ثبت نشده است.';
        bankListEl.appendChild(empty);
        return;
      }

      priority.forEach((entry) => {
        const bankName = safeText(entry?.name ?? entry);
        if (!bankName) return;
        const isStopped = stopped.includes(bankName);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `bank-stop-chip ${isStopped ? 'stopped' : ''}`;
        chip.disabled = isStopped;
        chip.textContent = isStopped ? `${bankName} (متوقف)` : `توقف ${bankName}`;
        if (!isStopped && typeof stopBank === 'function') {
          chip.addEventListener('click', () => stopBank(user.national_id, bankName));
        }
        bankListEl.appendChild(chip);
      });
    };
  }

  function installSafeLists() {
    if (typeof renderMainList === 'function') {
      renderMainList = function renderMainListSafe(data) {
        const tbody = byId('applicantsListBody');
        if (!tbody) return;
        clearNode(tbody);
        (Array.isArray(data) ? data : []).forEach((user, index) => {
          const row = document.createElement('tr');
          appendTextCell(row, index + 1);
          const nameCell = appendTextCell(row, user.full_name, 'fw-bold');
          nameCell.tabIndex = 0;
          nameCell.role = 'button';
          nameCell.addEventListener('click', () => typeof editUser === 'function' && editUser(index));
          nameCell.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') nameCell.click();
          });
          appendTextCell(row, user.national_id, 'font-monospace');
          appendTextCell(row, applicantData(user).tracking_code || '—', 'font-monospace text-success');
          appendStatusCell(row, user.status);
          actionCell(row, actionButton('شروع ثبت‌نام', 'btn btn-sm btn-outline-success rounded-pill px-3', 'fas fa-play', () => startRegister(user.national_id)));
          actionCell(row, actionButton('توقف', 'btn btn-sm btn-outline-danger rounded-pill px-3', 'fas fa-stop', () => stopBot(user.national_id)));
          tbody.appendChild(row);
        });
      };
    }

    if (typeof renderBankSelectList === 'function') {
      renderBankSelectList = function renderBankSelectListSafe(data) {
        const tbody = byId('bankSelectListBody');
        if (!tbody) return;
        clearNode(tbody);
        (Array.isArray(data) ? data : []).forEach((user) => {
          const row = document.createElement('tr');
          appendTextCell(row, user.full_name);
          appendTextCell(row, user.national_id, 'font-monospace');
          appendStatusCell(row, user.status);
          actionCell(row, actionButton('شروع انتخاب', 'btn btn-sm btn-primary', '', () => startBankSelect(user.national_id)));
          actionCell(row, actionButton('توقف', 'btn btn-sm btn-danger', '', () => stopBot(user.national_id)));
          tbody.appendChild(row);
        });
      };
    }

    if (typeof renderStatusList === 'function') {
      renderStatusList = function renderStatusListSafe(data) {
        const tbody = byId('statusListBody');
        if (!tbody) return;
        clearNode(tbody);
        (Array.isArray(data) ? data : []).forEach((user) => {
          const row = document.createElement('tr');
          appendTextCell(row, user.full_name);
          appendTextCell(row, user.national_id, 'font-monospace');
          appendTextCell(row, applicantData(user).tracking_code || '—', 'font-monospace fw-bold text-success');
          appendStatusCell(row, user.status);
          actionCell(row, actionButton('شروع بررسی', 'btn btn-sm btn-info text-white shadow-sm', 'fas fa-play', () => startStatus(user.national_id)));
          actionCell(row, actionButton('توقف', 'btn btn-sm btn-danger shadow-sm', 'fas fa-stop', () => stopBot(user.national_id)));
          tbody.appendChild(row);
        });
      };
    }
  }

  function installInputNormalization() {
    const nid = byId('inpNid');
    const otpFields = [byId('otpInput'), byId('manualOtpInput')].filter(Boolean);
    if (nid) {
      nid.inputMode = 'numeric';
      nid.autocomplete = 'off';
      nid.maxLength = 10;
      nid.addEventListener('input', () => {
        nid.value = normalizeDigits(nid.value).replace(/\D/g, '').slice(0, 10);
      });
    }
    otpFields.forEach((field) => {
      field.inputMode = 'numeric';
      field.autocomplete = 'one-time-code';
      field.addEventListener('input', () => {
        field.value = normalizeDigits(field.value).replace(/\D/g, '').slice(0, 8);
      });
    });
  }

  function installMobileDelegation() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar || sidebar.dataset.runtimeDelegation === '1') return;
    sidebar.dataset.runtimeDelegation = '1';
    sidebar.addEventListener('click', (event) => {
      if (event.target.closest('.nav-item')) document.body.classList.remove('mahan-sidebar-open');
    });
  }

  function installGlobalErrorNotice() {
    window.addEventListener('unhandledrejection', (event) => {
      const message = safeText(event?.reason?.message || event?.reason, 'خطای ناشناخته');
      console.error('MahanBot request error:', message);
      const pill = byId('mahanLivePill');
      if (pill) {
        pill.textContent = `خطا: ${message.slice(0, 80)}`;
        pill.dataset.state = 'offline';
      }
    });
  }

  function init() {
    installApiHardening();
    installActiveApplicantFix();
    installSafeLists();
    installInputNormalization();
    installMobileDelegation();
    installGlobalErrorNotice();
    try {
      if (typeof renderDashboard === 'function' && typeof allUsersData !== 'undefined') renderDashboard(allUsersData);
    } catch (_) {
      // The next websocket or polling update will render the dashboard.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
