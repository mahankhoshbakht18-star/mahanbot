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

  function isApplicantActive(status) {
    const value = safeText(status).toLowerCase();
    return ['running', 'registering', 'selecting', 'waiting sms'].some((item) => value.includes(item));
  }

  function setCounter(id, value) {
    const target = byId(id);
    if (target) target.textContent = String(value);
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
      const otpStatusEl = byId('otpStatus');
      if (!nameEl || !statusEl || !bankListEl) return;

      clearNode(bankListEl);
      if (!user) {
        nameEl.textContent = 'بدون متقاضی فعال';
        if (nidEl) nidEl.textContent = '—';
        if (mobileEl) mobileEl.textContent = '';
        if (legacyMetaEl) legacyMetaEl.textContent = '—';
        statusEl.textContent = 'آماده';
        statusEl.className = 'badge bg-secondary';
        if (otpStatusEl) otpStatusEl.textContent = '';
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

  function installSafeDashboard() {
    if (typeof renderDashboard !== 'function') return;
    renderDashboard = function renderDashboardSafe(users) {
      const list = Array.isArray(users) ? users : [];
      const tbody = byId('activeBotsBody');
      if (!tbody) return;
      clearNode(tbody);

      let runningCount = 0;
      let successCount = 0;
      let codeCount = 0;
      let runningNid = null;
      let activeUser = null;

      list.forEach((user) => {
        const data = applicantData(user);
        const rawStatus = safeText(user?.status);
        const normalizedStatus = rawStatus.toLowerCase();
        if (data.tracking_code) codeCount += 1;
        if (normalizedStatus.includes('success')) successCount += 1;
        if (!isApplicantActive(rawStatus)) return;

        runningCount += 1;
        const nid = safeText(user?.national_id);
        if (normalizedStatus.includes('wait')) runningNid = nid;
        else if (!runningNid) runningNid = nid;
        if (!activeUser || normalizedStatus.includes('running')) activeUser = user;

        const row = document.createElement('tr');
        row.dataset.nid = nid;
        const identity = document.createElement('td');
        const strong = document.createElement('strong');
        strong.textContent = safeText(user?.full_name, 'بدون نام');
        const small = document.createElement('small');
        small.textContent = nid || '—';
        identity.append(strong, document.createElement('br'), small);
        row.appendChild(identity);
        appendStatusCell(row, rawStatus);
        actionCell(
          row,
          actionButton('توقف', 'btn btn-sm btn-danger rounded-circle', 'fas fa-power-off', () => stopBot(nid)),
        );
        tbody.appendChild(row);
      });

      if (!runningCount) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 3;
        cell.className = 'text-muted small py-3';
        cell.textContent = 'عملیات فعالی وجود ندارد.';
        row.appendChild(cell);
        tbody.appendChild(row);
      }

      window.currentActiveBotNid = runningNid;
      if (typeof renderActiveApplicantCard === 'function') renderActiveApplicantCard(activeUser);
      setCounter('stat-total', list.length);
      setCounter('stat-active', runningCount);
      setCounter('stat-success', successCount);
      setCounter('stat-codes', codeCount);
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

  function installSafeLogs() {
    if (typeof renderLogs !== 'function') return;
    renderLogs = function renderLogsSafe(clearInitial = false) {
      const terminal = byId('terminalBox');
      if (!terminal) return;
      clearNode(terminal);
      const events = Array.isArray(logEvents) ? logEvents : [];
      if (!events.length && clearInitial) {
        const empty = document.createElement('div');
        empty.className = 'text-muted';
        empty.textContent = 'رویدادی ثبت نشده است.';
        terminal.appendChild(empty);
        return;
      }

      const filtered = events.filter((event) => {
        if (logFilters.type && event.type !== logFilters.type) return false;
        if (logFilters.level && safeText(event.level).toLowerCase() !== logFilters.level) return false;
        if (logFilters.nid && !safeText(event.nid).includes(logFilters.nid)) return false;
        if (logFilters.jobId && !safeText(event.job_id).includes(logFilters.jobId)) return false;
        if (logFilters.text) {
          const haystack = `${safeText(event.message)} ${safeText(event.status)} ${safeText(event.name)} ${safeText(event.value)}`.toLowerCase();
          if (!haystack.includes(logFilters.text)) return false;
        }
        return true;
      });

      if (!filtered.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted';
        empty.textContent = 'رویدادی مطابق فیلتر پیدا نشد.';
        terminal.appendChild(empty);
        return;
      }

      const levelColors = { error: '#ef5350', warning: '#ffca28', success: '#66bb6a', info: '#42a5f5' };
      filtered.forEach((event) => {
        const row = document.createElement('div');
        const time = document.createElement('span');
        time.style.color = '#888';
        time.textContent = `[${event.ts ? new Date(Number(event.ts) * 1000).toLocaleTimeString('fa-IR') : ''}] `;
        const type = document.createElement('span');
        type.style.color = '#90a4ae';
        type.textContent = `(${safeText(event.type, 'event')}) `;
        const nid = document.createElement('span');
        nid.style.color = '#00e5ff';
        nid.textContent = `${safeText(event.nid, 'system')} `;
        const job = document.createElement('span');
        job.style.color = '#9ccc65';
        job.textContent = event.job_id ? `#${safeText(event.job_id).slice(0, 8)} ` : '';
        const level = document.createElement('span');
        const levelName = safeText(event.level).toLowerCase();
        level.style.color = levelColors[levelName] || '#cfd8dc';
        level.textContent = levelName ? `[${levelName}] ` : '';
        const message = document.createElement('span');
        if (event.type === 'job_status') {
          message.textContent = `status=${safeText(event.status)}${event.detail ? ` (${safeText(event.detail)})` : ''}`;
        } else if (event.type === 'metric') {
          message.textContent = `${safeText(event.name)}: ${safeText(event.value)}`;
        } else {
          message.textContent = safeText(event.message, '—');
        }
        row.append(time, type, nid, job, level, message);
        terminal.appendChild(row);
      });
      terminal.scrollTop = terminal.scrollHeight;
    };
  }

  function installInputNormalization() {
    const nid = byId('inpNid');
    const otpFields = [byId('otpInput')].filter(Boolean);
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
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('.nav-item')) document.body.classList.remove('mahan-sidebar-open');
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
    installSafeDashboard();
    installSafeLists();
    installSafeLogs();
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
