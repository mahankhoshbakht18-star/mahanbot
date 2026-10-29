const httpBase = `${location.protocol}//${location.host}`;
const wsBase = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
const WS_RECONNECT_MIN_MS = 1000;
const WS_RECONNECT_MAX_MS = 10000;
const WS_PING_INTERVAL_MS = 25000;
const POLL_INTERVAL_DISCONNECTED_MS = 5000;
const MESSAGES = window.MESSAGES_FA || {};
const warnedMessageKeys = new Set();

let dashboardInterval = null;

let editingUserId = null;
let submissionDefaults = { enabled: false, interval: 20 };

function setSubmissionDefaults(config) {
    submissionDefaults = {
        enabled: Boolean(config.auto_final_submit ?? config.final_submit ?? false),
        interval: Number(config.bank_capacity_check_interval ?? config.bank_capacity_recheck_seconds ?? 20)
    };
    syncApplicantSubmissionControls();
}

function syncApplicantSubmissionControls() {
    const inherit = document.getElementById('inpInheritFinalSubmit');
    const checkbox = document.getElementById('inpAutoFinalSubmit');
    const interval = document.getElementById('inpFinalSubmitInterval');
    if (!inherit || !checkbox) return;
    checkbox.disabled = inherit.checked;
    if (inherit.checked) checkbox.checked = submissionDefaults.enabled;
    if (interval) interval.placeholder = `خالی: عمومی (${submissionDefaults.interval} ثانیه)`;
}

async function updateApplicantSubmission(nid, patch) {
    try {
        await apiCall(`/applicants/${encodeURIComponent(nid)}/submission-settings`, 'PATCH', patch);
        const user = allUsersData.find(user => String(user.national_id) === String(nid));
        if (user) user.data = { ...parseUserData(user.data), ...patch };
        if (currentViewId === 'bank-select') renderBankSelectList(allUsersData);
    } catch (error) {
        alert(error.message || 'ذخیره تنظیم ثبت نهایی ناموفق بود.');
        if (currentViewId === 'bank-select') renderBankSelectList(allUsersData);
    }
}

let allUsersData = [];

let wsClient = null;

let logEvents = [];

let browserProfile = null;

let allowedDomainsState = { env: [], db: [], effective: [] };

let currentViewId = 'dashboard';

const jobIdByNid = new Map();
const pendingStarts = new Set();

const LOG_BUFFER_LIMIT = 500;

const logFilters = {

    text: '',

    nid: '',

    jobId: '',

    level: '',

    type: ''

};

const BANK_OPTIONS = [
    'ملی',
    'صادرات',
    'تجارت',
    'ملت',
    'سپه',
    'کشاورزی',
    'مسکن',
    'رفاه کارگران',
    'توسعه تعاون',
    'توسعه صادرات',
    'صنعت و معدن',
    'پست بانک',
    'اقتصاد نوین',
    'پارسیان',
    'پاسارگاد',
    'کارآفرین',
    'سامان',
    'سینا',
    'سرمایه',
    'شهر',
    'دی',
    'گردشگری',
    'ایران زمین',
    'آینده',
    'خاورمیانه',
    'ایران و ونزوئلا',
    'قرض الحسنه مهر ایران',
    'قرض الحسنه رسالت',
    'موسسه اعتباری ملل',
    'موسسه اعتباری نور'
];

let priorityBanks = [];

let favoriteBanks = [];
let connectionState = 'DISCONNECTED'; // DISCONNECTED | CONNECTING | CONNECTED | DEGRADED
let wsReconnectDelay = WS_RECONNECT_MIN_MS;
let wsReconnectTimer = null;
let wsHeartbeatTimer = null;
let lastWsEventAt = null;
let lastHttpError = null;
let applicantsHash = null;
let pollTimer = null;
let pollIntervalMs = null;
let pollAbortController = null;
let pollInFlight = false;
let renderDebounceTimer = null;


document.addEventListener("DOMContentLoaded", () => {

    switchView('dashboard');

    startClock();

    setupLogFilters();

    connectWebSocket();

    loadBrowserProfile();
    loadBrowserDisplayMode();

    initBankSelectors();

    pollApplicants();
    loadSubscriptionManager();
    loadAppearanceSettings();
    updateDebugPanel();
    setInterval(updateEmbeddedBrowser, 900);

    if(document.getElementById('otpInput')){

        document.getElementById('otpInput').addEventListener('keypress', function (e) {

            if (e.key === 'Enter') sendOtp(this.value);

        });

    }

});

async function loadSubscriptionManager() {
    try {
        const meResponse = await fetch('/auth/me');
        if (meResponse.status === 401) { location.replace('/login'); return; }
        const me = await meResponse.json();
        if (!me.is_admin) return;
        document.getElementById('subscriptionPanel').hidden = false; loadSmsGatewayConfig();
        const users = await (await fetch('/admin/users')).json();
        const body = document.getElementById('subscriptionUsers');
        body.innerHTML = users.map(user => {
            const date = new Date(Number(user.expires_at) * 1000).toLocaleString('fa-IR');
            const active = Boolean(user.active) && Number(user.expires_at) * 1000 > Date.now();
            return `<tr><td>${escapeHtml(user.username)}</td><td>${user.is_admin ? 'مدیر' : 'مشتری'}</td><td>${date}</td><td><span class="badge ${active ? 'bg-success' : 'bg-danger'}">${active ? 'فعال' : 'غیرفعال/منقضی'}</span></td><td><button class="btn btn-sm btn-light" onclick="setSubscriptionState('${encodeURIComponent(user.username)}',${active ? 'false' : 'true'})">${active ? 'غیرفعال' : 'فعال'}</button></td></tr>`;
        }).join('');
    } catch (error) { console.warn('Subscription manager:', error); }
}

async function saveSubscriptionUser() {
    const payload = {username: subUsername.value.trim(), password: subPassword.value, days: Number(subDays.value), is_admin: subAdmin.checked};
    const response = await fetch('/admin/users', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) { alert(result.detail || 'ذخیره اشتراک ناموفق بود'); return; }
    subPassword.value = '';
    await loadSubscriptionManager();
}

async function setSubscriptionState(encodedUsername, active) {
    const username = decodeURIComponent(encodedUsername);
    const response = await fetch(`/admin/users/${encodeURIComponent(username)}/state`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({active})});
    if (!response.ok) { const result = await response.json(); alert(result.detail || 'تغییر وضعیت ناموفق بود'); return; }
    await loadSubscriptionManager();
}

async function logoutApp() { await fetch('/logout', {method:'POST'}); location.replace('/login'); }

async function loadSmsGatewayConfig() {
    try {
        const resp = await fetch('/admin/sms-config');
        if (!resp.ok) return;
        const cfg = await resp.json();
        const p = document.getElementById('smsGatewayPanel');
        if (p) p.hidden = false;
        if (document.getElementById('sms_otp_enabled')) document.getElementById('sms_otp_enabled').checked = Boolean(cfg.otp_auth_enabled);
        if (document.getElementById('sms_provider_select')) document.getElementById('sms_provider_select').value = cfg.sms_provider || 'mock';
        if (document.getElementById('sms_sender_line_input')) document.getElementById('sms_sender_line_input').value = cfg.sms_sender_line || '';
        if (document.getElementById('sms_pattern_code_input')) document.getElementById('sms_pattern_code_input').value = cfg.sms_pattern_code || '';
        if (document.getElementById('sms_otp_template_input')) document.getElementById('sms_otp_template_input').value = cfg.sms_otp_template || '?? ???? ?? ?????????: {code}\n??????: ? ?????';
        if (document.getElementById('sms_api_key_input') && cfg.has_api_key) {
            document.getElementById('sms_api_key_input').placeholder = '???????????????? (??? ???)';
        }
    } catch (e) {
        console.warn('loadSmsGatewayConfig error:', e);
    }
}

async function saveSmsGatewayConfig() {
    const alertBox = document.getElementById('smsGatewayAlert');
    try {
        const payload = {
            otp_auth_enabled: document.getElementById('sms_otp_enabled').checked,
            sms_provider: document.getElementById('sms_provider_select').value,
            sms_api_key: document.getElementById('sms_api_key_input').value.trim(),
            sms_sender_line: document.getElementById('sms_sender_line_input').value.trim(),
            sms_pattern_code: document.getElementById('sms_pattern_code_input').value.trim(),
            sms_otp_template: document.getElementById('sms_otp_template_input').value.trim(),
        };
        const resp = await fetch('/admin/sms-config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.detail || '??? ?? ????? ???????');
        if (alertBox) {
            alertBox.className = 'small mt-2 alert alert-success';
            alertBox.textContent = res.message || '??????? ??? ????? ????? ??.';
            alertBox.style.display = 'block';
            setTimeout(() => alertBox.style.display = 'none', 4000);
        }
        await loadSmsGatewayConfig();
    } catch (err) {
        if (alertBox) {
            alertBox.className = 'small mt-2 alert alert-danger';
            alertBox.textContent = err.message;
            alertBox.style.display = 'block';
        }
    }
}

async function testSendSmsPrompt() {
    const mobile = prompt('????? ?????? ?????? ?? ???? ?????? (????: 09123456789):');
    if (!mobile) return;
    const alertBox = document.getElementById('smsGatewayAlert');
    try {
        const resp = await fetch('/admin/sms-test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mobile: mobile.trim()})
        });
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.detail || '??? ?? ????? ????? ???????');
        alert(res.message || '????? ??????? ?? ?????? ????? ??.');
    } catch (err) {
        alert('???: ' + err.message);
    }
}

async function loadBrowserDisplayMode() {
    try {
        const settings = await (await fetch(`${httpBase}/settings`)).json();
        setSubmissionDefaults(settings);
        applyBrowserDisplayMode(settings.browser_display_mode || 'external');
    } catch (_) {
        applyBrowserDisplayMode('external');
    }
}

function applyBrowserDisplayMode(mode) {
    const panel = document.querySelector('.browser-live-panel');
    if (panel) panel.style.display = mode === 'embedded' ? 'block' : 'none';
}

function updateEmbeddedBrowser() {
    const image = document.getElementById('embeddedBrowserImage');
    const empty = document.getElementById('embeddedBrowserEmpty');
    const address = document.getElementById('embeddedBrowserAddress');
    const nid = window.currentActiveBotNid;
    if (!image || !empty || !address) return;
    if (!nid) {
        image.removeAttribute('src');
        image.style.display = 'none';
        empty.style.display = 'flex';
        address.textContent = 'در انتظار شروع بات…';
        return;
    }
    const nextUrl = `/static/live_browser/${encodeURIComponent(nid)}.jpg?t=${Date.now()}`;
    const probe = new Image();
    probe.onload = () => {
        image.src = nextUrl;
        image.style.display = 'block';
        empty.style.display = 'none';
        address.textContent = `جلسه فعال متقاضی ${nid}`;
    };
    probe.onerror = () => {
        if (!image.getAttribute('src')) {
            image.style.display = 'none';
            empty.style.display = 'flex';
        }
        address.textContent = `در حال اتصال به مرورگر متقاضی ${nid}…`;
    };
    probe.src = nextUrl;
}



function getMessage(path, fallback = '') {

    const parts = path.split('.');

    let current = MESSAGES;

    for (const part of parts) {

        if (current && Object.prototype.hasOwnProperty.call(current, part)) {

            current = current[part];

        } else {

            if (!warnedMessageKeys.has(path)) {

                console.warn(`Missing message key: ${path}`);

                warnedMessageKeys.add(path);

            }

            return fallback;

        }

    }

    if (typeof current === 'string') {

        return current;

    }

    return fallback;

}



function startClock() {
    setInterval(() => {
        const el = document.getElementById('clock');
        if(el) el.innerText = new Date().toLocaleTimeString('fa-IR');
    }, 1000);
}

function isWebSocketOpen() {
    return wsClient && wsClient.readyState === WebSocket.OPEN;
}

function setConnectionState(state) {
    connectionState = state;
    const badge = document.getElementById('connectionStatus');
    if (badge) {
        const map = {
            CONNECTED: { cls: 'bg-success', text: getMessage('connection.connected', 'Connected') },
            CONNECTING: { cls: 'bg-warning text-dark', text: getMessage('connection.connecting', 'Connecting...') },
            DEGRADED: { cls: 'bg-warning text-dark', text: getMessage('connection.degraded', 'Degraded') },
            DISCONNECTED: { cls: 'bg-danger', text: getMessage('connection.disconnected', 'Disconnected') },
        };
        const meta = map[state] || map.DISCONNECTED;
        badge.className = `badge ${meta.cls}`;
        badge.innerText = meta.text;
    }

    if (state === 'CONNECTED') {
        stopPolling();
    } else if (currentViewId === 'dashboard') {
        startPolling(POLL_INTERVAL_DISCONNECTED_MS);
    }
    updateDebugPanel();
}


function scheduleRenderLogs() {
    if (renderDebounceTimer) clearTimeout(renderDebounceTimer);
    renderDebounceTimer = setTimeout(() => renderLogs(), 150);
}

function updateDebugPanel() {
    const wsEl = document.getElementById('debugWsState');
    const lastWsEl = document.getElementById('debugLastWs');
    const httpEl = document.getElementById('debugLastHttp');
    if (wsEl) wsEl.textContent = connectionState;
    if (lastWsEl) lastWsEl.textContent = lastWsEventAt ? new Date(lastWsEventAt).toLocaleTimeString('fa-IR') : '-';
    if (httpEl) httpEl.textContent = lastHttpError || '-';
}

function startPolling(intervalMs = POLL_INTERVAL_DISCONNECTED_MS) {
    if (isWebSocketOpen()) return;
    if (pollTimer && pollIntervalMs === intervalMs) return;
    stopPolling();
    pollIntervalMs = intervalMs;
    pollTimer = setInterval(() => pollApplicants(), intervalMs);
    pollApplicants();
    updateDebugPanel();
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    pollIntervalMs = null;
    if (pollAbortController) {
        pollAbortController.abort();
        pollAbortController = null;
    }
}

async function pollApplicants() {
    if (pollInFlight) {
        if (pollAbortController) pollAbortController.abort();
    }
    pollAbortController = new AbortController();
    pollInFlight = true;
    try {
        const res = await fetch(`${httpBase}/applicants`, { signal: pollAbortController.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        lastHttpError = null;
        const hash = JSON.stringify(data);
        if (hash !== applicantsHash) {
            applicantsHash = hash;
            allUsersData = data;
            renderDashboard(data);
        }
        if (!isWebSocketOpen()) setConnectionState('DEGRADED');
    } catch (err) {
        if (err && err.name === 'AbortError') {
            return;
        }
        lastHttpError = String(err);
        if (!isWebSocketOpen()) setConnectionState('DEGRADED');
    } finally {
        pollInFlight = false;
        updateDebugPanel();
    }
}


function switchView(viewId, navEl) {

    currentViewId = viewId;
    if (viewId === 'license' && window.MahanLicense) {
        window.MahanLicense.refresh();
        window.MahanLicense.loadManager();
    }

    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active-view'));

    const target = document.getElementById(`view-${viewId}`);

    if(target) target.classList.add('active-view');

    

    if(navEl) {

        document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));

        navEl.querySelector('.nav-link').classList.add('active');

    }



    if(viewId === 'dashboard') {
        if (connectionState !== 'CONNECTED') {
            startPolling(POLL_INTERVAL_DISCONNECTED_MS);
        } else {
            stopPolling();
        }
    } else {
        stopPolling();
    }


        if(viewId === 'queue') {
        loadQueueData();
        loadOtpQueueData();
    }

    if(viewId === 'list') fetchApplicants('list');

    if(viewId === 'radar') {
        loadRadarData();
    }

    if(viewId === 'bank-select') {
        fetchApplicants('bank-select');
        loadCapacityStatus();
    }

    if(viewId === 'saved-pages') {
        loadSavedPages();
        startCapacityCountdown();
    } else {
        stopCapacityCountdown();
    }

    if(viewId === 'status') fetchApplicants('status');

    if(viewId === 'recover') fetchApplicants('recover');

    if(viewId === 'delete-req') fetchApplicants('delete-req');

    if(viewId === 'settings') {

        loadSettings();

        loadBrowserProfile();

        loadAllowedDomains();

        initSmsGsmSettings();

    }

    

    if(viewId === 'add' && !editingUserId) resetAddForm();

}



// ---   ---

function parseUserData(userData) {

    if (!userData) return {};

    if (typeof userData === 'object') return userData;

    try { return JSON.parse(userData); } 

    catch (e) { return {}; }

}



function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

function maskSensitive(value) {
    const text = String(value || '');
    return text.length > 4 ? `${text.slice(0, 2)}${'*'.repeat(text.length - 4)}${text.slice(-2)}` : '*'.repeat(text.length);
}

function runtimeTime(value) {
    return value ? new Date(Number(value) * 1000).toLocaleTimeString('fa-IR') : '-';
}

function parseBanksInput(rawValue) {

    if (!rawValue) return [];

    return rawValue

        .split(/[\n,]+/)

        .map((entry) => entry.trim())

        .filter((entry) => entry.length > 0);

}



function formatBanksInput(banks) {

    if (!banks) return '';

    if (Array.isArray(banks)) return banks.join('\n');

    if (typeof banks === 'string') return banks;

    return '';

}



function initBankSelectors() {
    populateCbiAllBanksDropdown();
    const optionsEl = document.getElementById('bankOptions');
    const priorityEl = document.getElementById('priorityBanksList');
    const favoriteEl = document.getElementById('favoriteBanksList');
    if (!optionsEl || !priorityEl || !favoriteEl) return;

    renderBankOptions();
    renderPriorityBanks();
    renderFavoriteBanks();
    setupDragAndDrop(priorityEl);
}

function populateCbiAllBanksDropdown() {
    const sel = document.getElementById('cbiAllBanksDropdown');
    if (!sel) return;
    sel.innerHTML = '<option value="">-- برای افزودن، نام بانک را از لیست ۳۰ بانک رسمی انتخاب کنید --</option>';
    BANK_OPTIONS.forEach(bank => {
        const opt = document.createElement('option');
        opt.value = bank;
        opt.textContent = `بانک ${bank}`;
        sel.appendChild(opt);
    });
}

function addBankFromDropdown() {
    const sel = document.getElementById('cbiAllBanksDropdown');
    if (!sel || !sel.value) {
        showToast('لطفاً ابتدا یک بانک از لیست کشویی انتخاب کنید.', 'warning');
        return;
    }
    const bankName = sel.value;
    if (!priorityBanks.includes(bankName)) {
        priorityBanks.push(bankName);
        renderPriorityBanks();
        renderBankOptions();
        showToast(`بانک «${bankName}» با موفقیت به اولویت‌ها اضافه شد.`, 'success');
    } else {
        showToast(`بانک «${bankName}» در اولویت‌ها قرار دارد.`, 'info');
    }
    sel.value = '';
}



function renderBankOptions() {

    const optionsEl = document.getElementById('bankOptions');

    if (!optionsEl) return;

    optionsEl.innerHTML = '';

    BANK_OPTIONS.forEach((bank) => {

        const chip = document.createElement('button');

        chip.type = 'button';

        chip.className = `bank-chip ${priorityBanks.includes(bank) ? 'active' : ''}`;

        chip.textContent = bank;

        chip.addEventListener('click', () => {

            if (!priorityBanks.includes(bank)) {

                priorityBanks.push(bank);

                renderPriorityBanks();

            }

            renderBankOptions();

        });

        optionsEl.appendChild(chip);

    });

}



function renderPriorityBanks() {

    const priorityEl = document.getElementById('priorityBanksList');

    if (!priorityEl) return;

    priorityEl.innerHTML = '';

    priorityBanks.forEach((bank) => {

        const item = document.createElement('li');

        item.className = 'bank-priority-item list-group-item';

        item.draggable = true;

        item.dataset.bank = bank;

        item.innerHTML = `

            <span>${bank}</span>

            <button class="btn btn-sm btn-outline-danger">حذف</button>

        `;

        item.querySelector('button').addEventListener('click', () => {

            priorityBanks = priorityBanks.filter((b) => b !== bank);

            renderPriorityBanks();

            renderBankOptions();

        });

        priorityEl.appendChild(item);

    });

}



function renderFavoriteBanks() {

    const favoriteEl = document.getElementById('favoriteBanksList');

    if (!favoriteEl) return;

    favoriteEl.innerHTML = '';

    BANK_OPTIONS.forEach((bank) => {

        const chip = document.createElement('button');

        chip.type = 'button';

        chip.className = `bank-chip ${favoriteBanks.includes(bank) ? 'active' : ''}`;

        chip.textContent = bank;

        chip.addEventListener('click', () => {

            if (favoriteBanks.includes(bank)) {

                favoriteBanks = favoriteBanks.filter((b) => b !== bank);

            } else {

                favoriteBanks.push(bank);

            }

            renderFavoriteBanks();

        });

        favoriteEl.appendChild(chip);

    });

}



function setupDragAndDrop(container) {

    if (!container) return;

    container.addEventListener('dragstart', (event) => {

        const target = event.target;

        if (target && target.classList.contains('bank-priority-item')) {

            target.classList.add('dragging');

        }

    });

    container.addEventListener('dragend', (event) => {

        const target = event.target;

        if (target && target.classList.contains('bank-priority-item')) {

            target.classList.remove('dragging');

        }

    });

    container.addEventListener('dragover', (event) => {

        event.preventDefault();

        const dragging = container.querySelector('.dragging');

        if (!dragging) return;

        const afterElement = getDragAfterElement(container, event.clientY);

        if (afterElement == null) {

            container.appendChild(dragging);

        } else {

            container.insertBefore(dragging, afterElement);

        }

    });

    container.addEventListener('drop', () => {

        const newOrder = [];

        container.querySelectorAll('.bank-priority-item').forEach((item) => {

            if (item.dataset.bank) newOrder.push(item.dataset.bank);

        });

        priorityBanks = newOrder;

        renderBankOptions();

    });

}



function getDragAfterElement(container, y) {

    const draggableElements = [...container.querySelectorAll('.bank-priority-item:not(.dragging)')];

    return draggableElements.reduce((closest, child) => {

        const box = child.getBoundingClientRect();

        const offset = y - box.top - box.height / 2;

        if (offset < 0 && offset > closest.offset) {

            return { offset, element: child };

        }

        return closest;

    }, { offset: Number.NEGATIVE_INFINITY }).element;

}



async function fetchDashboardData() {
    await pollApplicants();
}


function renderDashboard(users) {

    const tbody = document.getElementById('activeBotsBody');

    if(!tbody) return;

    tbody.innerHTML = '';

    let runningCount = 0, successCount = 0, codeCount = 0, runningNid = null;
    let otpWaitingCount = 0, bankWaitingCount = 0;
    let activeUser = null;

    users.forEach(user => {
        let d = parseUserData(user.data);
        if(d.tracking_code) codeCount++;
        const normalizedStatus = String(user.status || '').toLowerCase();
        if(normalizedStatus.includes('success')) successCount++;
        if(normalizedStatus.includes('otp') || normalizedStatus.includes('sms') || normalizedStatus.includes('پیامک') || normalizedStatus.includes('wait')) otpWaitingCount++;
        if(normalizedStatus.includes('bank') || normalizedStatus.includes('بانک') || normalizedStatus.includes('select') || normalizedStatus.includes('انتخاب')) bankWaitingCount++;

        const isActive = ['running', 'registering', 'selecting', 'waiting sms', 'waiting otp']
            .some(s => normalizedStatus.includes(s)) || jobIdByNid.has(String(user.national_id));

        if (isActive) {
            runningCount++;
            if(user.status.includes('Wait')) runningNid = user.national_id;
            else if (!runningNid) runningNid = user.national_id;

            if (!activeUser || user.status.includes('Running')) {
                activeUser = user;
            }

            const tr = document.createElement('tr');
            tr.dataset.nid = user.national_id;
            tr.innerHTML = `
                <td><strong>${user.full_name}</strong><br><small class="font-monospace">${formatNid(user.national_id)}</small></td>
                <td><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>
                <td><button class="btn btn-sm btn-danger rounded-circle" onclick="stopBot('${user.national_id}')"><i class="fas fa-power-off"></i></button></td>
            `;
            tbody.appendChild(tr);
        }
    });

    if(runningCount===0) tbody.innerHTML = `<tr><td colspan="3" class="text-muted small py-3">${getMessage('labels.inactive', '')}</td></tr>`;

    renderOtpRecipients(users);
    const chosen = users.find(user => String(user.national_id) === manualOtpTargetNid);
    if (chosen) activeUser = chosen;
    window.currentActiveBotNid = activeUser ? String(activeUser.national_id) : null;
    renderActiveApplicantCard(activeUser);
    updateProcessStepper(activeUser);

    if(document.getElementById('stat-total')) document.getElementById('stat-total').innerText = users.length;
    if(document.getElementById('stat-active')) document.getElementById('stat-active').innerText = runningCount;
    if(document.getElementById('stat-success')) document.getElementById('stat-success').innerText = successCount;
    if(document.getElementById('stat-codes')) document.getElementById('stat-codes').innerText = codeCount;
    if(document.getElementById('stat-otp')) document.getElementById('stat-otp').innerText = otpWaitingCount;
    if(document.getElementById('stat-bank')) document.getElementById('stat-bank').innerText = bankWaitingCount;
}



function renderActiveApplicantCard(user) {

    const nameEl = document.getElementById('activeApplicantName');

    const metaEl = document.getElementById('activeApplicantMeta');

    const statusEl = document.getElementById('activeApplicantStatus');

    const bankListEl = document.getElementById('activeBankList');

    const manualStatusEl = document.getElementById('manualOtpStatus');

    if (!nameEl || !metaEl || !statusEl || !bankListEl) return;



    bankListEl.innerHTML = '';

    if (!user) {

        nameEl.textContent = 'بدون کاربر فعال';

        metaEl.textContent = '---';

        statusEl.textContent = '---';

        statusEl.className = 'badge bg-secondary';

        if (manualStatusEl) manualStatusEl.textContent = '';

        return;

    }



    const data = parseUserData(user.data);

    const priority = data.priority_banks || data.banks || [];

    const stopped = data.stopped_banks || [];

    const mobile = data.mobile || '-';



    nameEl.textContent = user.full_name || '---';
    metaEl.textContent = `کد ملی: ${formatNid(user.national_id)} | موبایل: ${formatMobile(mobile)}`;

    statusEl.textContent = translateStatus(user.status);

    statusEl.className = `badge ${getStatusBadge(user.status)}`;



    if (!priority.length) {

        bankListEl.innerHTML = '<span class="text-muted small">هیچ بانکی ثبت نشده است.</span>';

        return;

    }



    priority.forEach((bank) => {

        const bankName = bank?.name || bank;

        const chip = document.createElement('span');

        const isStopped = stopped.includes(bankName);

        chip.className = `bank-stop-chip ${isStopped ? 'stopped' : ''}`;

        chip.textContent = isStopped ? `${bankName} (متوقف)` : `توقف ${bankName}`;

        if (!isStopped) {

            chip.addEventListener('click', () => stopBank(user.national_id, bankName));

        }

        bankListEl.appendChild(chip);

    });

}



function isOtpWaiting(user) {
    const runtime = user.runtime || {};
    const step = String(runtime.current_step || '').toUpperCase();
    if (/(BANK|CAPACITY|FINAL|RESERVE|DONE|STOPPED|MANUAL_ACTION_REQUIRED)/.test(step)) return false;
    return /^(STEP_2_OTP|OTP_FORM|WAITING_SMS|OTP_REQUIRED|OTP_MANUAL_REQUIRED|MANUAL_OTP_REQUIRED)$/.test(step)
        || /waiting (sms|otp)|منتظر پیامک/i.test(String(user.status || ''));
}

let manualOtpTargetNid = null;
let manualOtpSending = false;

function normalizePersianDigits(value) {
    return value.replace(/[۰-۹]/g, digit => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
        .replace(/[٠-٩]/g, digit => String('٠١٢٣٤٥٦٧٨٩'.indexOf(digit)));
}

function renderOtpRecipients(users) {
    const select = document.getElementById('manualOtpRecipient');
    if (!select) return;
    const waiters = users.filter(isOtpWaiting);
    const previous = manualOtpTargetNid;
    if (!waiters.some(user => String(user.national_id) === manualOtpTargetNid)) {
        manualOtpTargetNid = waiters.length === 1 ? String(waiters[0].national_id) : null;
    }
    select.replaceChildren(new Option('متقاضی منتظر پیامک را انتخاب کنید', ''));
    waiters.forEach(user => select.add(new Option(`${user.full_name} (${formatNid(user.national_id)})`, String(user.national_id))));
    select.value = manualOtpTargetNid || '';
    if (previous !== manualOtpTargetNid) {
        const mInput = document.getElementById('manualOtpInput');
        if (mInput) mInput.value = '';
        const oInput = document.getElementById('otpInput');
        if (oInput) oInput.value = '';
        const mStatus = document.getElementById('manualOtpStatus');
        if (mStatus) mStatus.textContent = '';
    }
    const send = document.getElementById('manualOtpSend');
    if (send) send.disabled = !manualOtpTargetNid || manualOtpSending;
    select.disabled = manualOtpSending;
    const mInput = document.getElementById('manualOtpInput');
    if (mInput) mInput.disabled = manualOtpSending;
    const oInput = document.getElementById('otpInput');
    if (oInput) oInput.disabled = manualOtpSending;
}

function selectOtpRecipient(nid) {
    manualOtpTargetNid = nid || null;
    const mInput = document.getElementById('manualOtpInput');
    if (mInput) mInput.value = '';
    const oInput = document.getElementById('otpInput');
    if (oInput) oInput.value = '';
    const user = allUsersData.find(user => String(user.national_id) === manualOtpTargetNid);
    if (user) {
        window.currentActiveBotNid = manualOtpTargetNid;
        renderActiveApplicantCard(user);
        updateProcessStepper(user);
    }
    renderOtpRecipients(allUsersData);
}

async function submitManualOtp(code, inputId, statusId) {
    const input = document.getElementById(inputId) || document.getElementById('manualOtpInput');
    const status = document.getElementById(statusId) || document.getElementById('manualOtpStatus');
    // Bind the recipient before awaiting HTTP; later snapshots cannot redirect it.
    const nid = manualOtpTargetNid;
    const recipient = allUsersData.find(user => String(user.national_id) === nid && isOtpWaiting(user));
    if (!nid || !recipient) {
        if (status) status.textContent = 'گیرنده کد را از متقاضیان منتظر پیامک انتخاب کنید.';
        return;
    }
    const clean = normalizePersianDigits(String(code || '')).trim();
    if (!/^[0-9]{4,8}$/.test(clean) || manualOtpSending) return;
    manualOtpSending = true;
    renderOtpRecipients(allUsersData);
    if (status) status.textContent = 'در حال ارسال...';
    try {
        await apiCall('/manual_otp', 'POST', {nid, code: clean});
        if (status) status.textContent = `کد برای ${recipient.full_name} ارسال شد.`;
        if (manualOtpTargetNid === nid && input) input.value = '';
    } catch (error) {
        if (status) status.textContent = error.message || 'ارسال کد ناموفق بود؛ دوباره بررسی کنید.';
    } finally {
        manualOtpSending = false;
        renderOtpRecipients(allUsersData);
    }
}

async function sendOtp(code) {
    return submitManualOtp(code, 'manualOtpInput', 'manualOtpStatus');
}

async function sendOtpManualFallback(code) {
    return sendOtp(code);
}

async function sendManualOtp() {
    const inputEl = document.getElementById('manualOtpInput');
    return submitManualOtp(inputEl ? inputEl.value : '', 'manualOtpInput', 'manualOtpStatus');
}


async function fetchApplicants(mode) {

    try {

        const res = await fetch(`${httpBase}/applicants`);

        const data = await res.json();

        allUsersData = data;



        if(mode === 'list') renderMainList(data);

        else if(mode === 'bank-select') renderBankSelectList(data);

        else if(mode === 'status') renderStatusList(data);

        else if(mode === 'recover') renderRecoverList(data);

        else if(mode === 'delete-req') renderDeleteReqList(data);

    } catch(e){console.error(e);}

}



function renderStatusList(data) {

    const tbody = document.getElementById('statusListBody');

    if(!tbody) return;

    tbody.innerHTML = '';

    

    data.forEach(user => {

        let d = parseUserData(user.data);

        let code = d.tracking_code || '-';

        

        tbody.innerHTML += `

            <tr>

                <td>${user.full_name}</td>

                <td class="font-monospace">${user.national_id}</td>

                <td class="font-monospace fw-bold text-success">${code}</td>

                <td><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>

                <td>

                    <button class="btn btn-sm btn-info text-white shadow-sm" onclick="startStatus('${user.national_id}')">

                        <i class="fas fa-play me-1"></i> شروع استعلام

                    </button>

                </td>

                <td>

                    <button class="btn btn-sm btn-danger shadow-sm" onclick="stopBot('${user.national_id}')">

                        <i class="fas fa-stop me-1"></i> توقف

                    </button>

                </td>

            </tr>

        `;

    });

}



function renderMainList(data) {
    const tbody = document.getElementById('applicantsListBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    data.forEach((user, idx) => {
        const d = parseUserData(user.data);
        const code = d.tracking_code || '-';
        const hasCode = code && code !== '-';
        const codeBadge = hasCode
            ? `<span class="badge bg-success-subtle text-success fw-bold px-2 py-1 font-monospace">${escapeHtml(code)}</span><button class="btn btn-link btn-sm p-0 ms-1 text-danger" title="پاکسازی کد رهگیری و ثبت مجدد" onclick="clearApplicantTracking('${user.national_id}')"><i class="fas fa-rotate-left small"></i></button>`
            : `<span class="text-secondary fw-bold">-</span>`;

        tbody.innerHTML += `
            <tr>
                <td class="text-center fw-bold" style="color: #64748B; font-size: 12px;">${idx + 1}</td>
                <td onclick="editUser(${idx})" class="applicant-cell" style="cursor:pointer;" title="ویرایش متقاضی">
                    <span class="applicant-name-link">
                        ${escapeHtml(user.full_name)}
                        <i class="fas fa-pen-to-square small ms-1 text-muted opacity-75"></i>
                    </span>
                </td>
                <td class="font-monospace text-center fw-semibold" style="color: #E2E8F0; font-size: 13px; letter-spacing: 0.5px;">${formatNid(user.national_id)}</td>
                <td class="font-monospace text-center">${codeBadge}</td>
                <td class="text-center"><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>
                <td class="text-center"><button class="btn btn-sm btn-outline-success rounded-pill px-3" onclick="startRegister('${user.national_id}')"><i class="fas fa-play me-1"></i>شروع ثبت‌نام</button></td>
                <td class="text-center"><button class="btn btn-sm btn-outline-warning rounded-pill px-3" onclick="stopBot('${user.national_id}')"><i class="fas fa-stop me-1"></i>توقف</button></td>
                <td class="text-center"><button class="btn btn-sm btn-outline-danger rounded-circle p-2" title="حذف متقاضی" onclick="deleteApplicant('${user.national_id}')"><i class="fas fa-trash"></i></button></td>
            </tr>
        `;
    });
}



let capacityMonitorsData = {};

async function loadCapacityStatus() {
    try {
        const res = await apiCall('/api/capacity/status', 'GET');
        if (res && res.status === 'ok') {
            const map = {};
            (res.monitors || []).forEach(m => {
                map[m.nid] = m;
            });
            capacityMonitorsData = map;

            const sum = res.summary || {};
            const cfg = res.config || {};
            if (typeof cfg.auto_final_submit === 'boolean') setSubmissionDefaults(cfg);
            
            const totalEl = document.getElementById('capStatTotal');
            if (totalEl) totalEl.innerText = (allUsersData && allUsersData.length) || sum.total_monitors || 0;
            
            const waitEl = document.getElementById('capStatWaiting');
            if (waitEl) waitEl.innerText = sum.waiting_capacity_count || 0;
            
            const subEl = document.getElementById('capStatSubmitting');
            if (subEl) subEl.innerText = sum.active_count || 0;
            
            const succEl = document.getElementById('capStatSuccess');
            if (succEl && allUsersData) {
                const succCount = allUsersData.filter(u => {
                    const d = parseUserData(u.data);
                    return d.tracking_code || (u.runtime && u.runtime.tracking_code);
                }).length;
                succEl.innerText = succCount;
            }

            const intervalInput = document.getElementById('capIntervalInput');
            if (intervalInput && cfg.bank_capacity_check_interval) {
                intervalInput.value = cfg.bank_capacity_check_interval;
            }
            const concInput = document.getElementById('capConcurrencyInput');
            if (concInput && cfg.max_concurrency) {
                concInput.value = cfg.max_concurrency;
            }

            if (currentViewId === 'bank-select') {
                renderBankSelectList(allUsersData);
            }
        }
    } catch (e) {
        console.warn('Failed to load capacity status:', e);
    }
}

async function startAllCapacity() {
    if (!confirm('پایش و استعلام ظرفیت برای تمامی متقاضیان فعال آغاز شود؟')) return;
    try {
        const res = await apiCall('/api/capacity/start-all', 'POST');
        alert(`فرمان شروع صادر شد: ${res.started_count || 0} متقاضی آغاز شد، ${res.skipped_count || 0} در حال اجرا یا قبلاً ثبت‌شده.`);
        await loadCapacityStatus();
        fetchDashboardData();
    checkDbHealth();
    } catch (e) {
        alert(e.message || 'خطا در شروع همگانی پایش');
    }
}

async function stopAllCapacity() {
    if (!confirm('تمامی عملیات‌های پایش و شکار ظرفیت متوقف شوند؟')) return;
    try {
        const res = await apiCall('/api/capacity/stop-all', 'POST');
        alert(`تمامی پایش‌ها متوقف شدند (${res.cancelled_count || 0} مورد لغو شد).`);
        await loadCapacityStatus();
        fetchDashboardData();
    } catch (e) {
        alert(e.message || 'خطا در توقف همگانی پایش');
    }
}

async function updateCapacityInterval() {
    const input = document.getElementById('capIntervalInput');
    const val = parseFloat(input?.value || 5);
    if (!Number.isFinite(val) || val < 1 || val > 600) {
        alert('فاصله تلاش باید عددی بین ۱ تا ۶۰۰ ثانیه باشد.');
        return;
    }
    try {
        await apiCall('/api/capacity/interval', 'POST', { interval_seconds: val });
        alert(`فاصله پایش با موفقیت روی ${val} ثانیه تنظیم شد.`);
        loadCapacityStatus();
    } catch (e) {
        alert(e.message || 'خطا در ثبت فاصله');
    }
}

async function updateCapacityConcurrency() {
    const input = document.getElementById('capConcurrencyInput');
    const val = parseInt(input?.value || 4, 10);
    if (isNaN(val) || val < 1 || val > 32) {
        alert('حداکثر متقاضی همزمان باید بین ۱ تا ۳۲ باشد.');
        return;
    }
    try {
        await apiCall('/api/capacity/concurrency', 'POST', { max_concurrency: val });
        alert(`سقف همزمانی با موفقیت روی ${val} کارگر تنظیم شد.`);
        loadCapacityStatus();
    } catch (e) {
        alert(e.message || 'خطا در ثبت سقف همزمانی');
    }
}

async function pauseApplicant(nid) {
    try {
        await apiCall(`/api/capacity/pause/${nid}`, 'POST');
        await loadCapacityStatus();
    } catch (e) {
        alert(e.message || 'خطا در توقف موقت متقاضی');
    }
}

async function resumeApplicant(nid) {
    try {
        await apiCall(`/api/capacity/resume/${nid}`, 'POST');
        await loadCapacityStatus();
    } catch (e) {
        alert(e.message || 'خطا در ادامه پایش متقاضی');
    }
}

function getCapacityStateBadge(state) {
    if (!state) return 'badge badge-status-neutral';
    const s = String(state).toUpperCase();
    switch (s) {
        case 'WAITING_CAPACITY':
            return 'badge badge-status-warning';
        case 'SUBMITTING':
            return 'badge badge-status-info';
        case 'SUCCESS':
            return 'badge badge-status-success';
        case 'BANK_SELECTED':
        case 'NON_PRESENTIAL_SELECTED':
        case 'PAGE_3_READY':
            return 'badge badge-status-running';
        case 'SESSION_EXPIRED':
            return 'badge badge-status-danger';
        case 'PAUSED':
            return 'badge badge-status-neutral';
        case 'ERROR':
            return 'badge badge-status-danger';
        default:
            return 'badge badge-status-info';
    }
}

function translateCapacityState(state) {
    if (!state) return '-';
    switch (state) {
        case 'WAITING_CAPACITY': return 'در انتظار سهمیه';
        case 'SUBMITTING': return 'در حال ارسال نهایی';
        case 'SUCCESS': return 'ثبت موفقیت‌آمیز';
        case 'BANK_SELECTED': return 'بانک انتخاب شد';
        case 'NON_PRESENTIAL_SELECTED': return 'غیرحضوری انتخاب شد';
        case 'PAGE_3_READY': return 'صفحه ۳ آماده';
        case 'SESSION_EXPIRED': return 'نشست منقضی';
        case 'PAUSED': return 'متوقف موقت';
        case 'ERROR': return 'خطای موقت / پس‌نشست';
        default: return state;
    }
}

function formatWaitingDuration(seconds) {
    if (!seconds || seconds <= 0) return '-';
    const s = Math.round(seconds);
    if (s < 60) return `${s} ثانیه`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${m}د و ${rem}ث`;
}

function editApplicantByNid(nid) {
    const idx = (allUsersData || []).findIndex(u => u.national_id === nid);
    if (idx !== -1) {
        editUser(idx);
    }
}

function setBankSelectDisplayMode(mode) {
    const btnCards = document.getElementById('btnModeCards');
    const btnTable = document.getElementById('btnModeTable');
    const secActive = document.getElementById('sectionActiveSnipers');
    const secStandby = document.getElementById('sectionStandbyApplicants');
    const secTable = document.getElementById('sectionBankTableWrapper');
    if (secTable) {
        if (mode === 'cards') {
            btnCards?.classList.add('btn-primary', 'active');
            btnCards?.classList.remove('btn-outline-secondary');
            btnTable?.classList.remove('btn-primary', 'active');
            btnTable?.classList.add('btn-outline-secondary');
            if (secActive) secActive.style.display = 'block';
            if (secStandby) secStandby.style.display = 'block';
            secTable.style.display = 'none';
        } else {
            btnTable?.classList.add('btn-primary', 'active');
            btnTable?.classList.remove('btn-outline-secondary');
            btnCards?.classList.remove('btn-primary', 'active');
            btnCards?.classList.add('btn-outline-secondary');
            if (secActive) secActive.style.display = 'none';
            if (secStandby) secStandby.style.display = 'none';
            secTable.style.display = 'block';
        }
    } else {
        if (secActive) secActive.style.display = 'block';
        if (secStandby) secStandby.style.display = 'block';
    }
}

async function pauseAllCapacity() {
    if (!confirm('آیا مایل به مکث تمام پایش‌های فعال هستید؟')) return;
    try {
        const activeUsers = (allUsersData || []).filter(u => {
            const mon = capacityMonitorsData[u.national_id] || {};
            return mon.session_active || mon.state === 'WAITING_CAPACITY' || u.runtime?.current_step === 'CAPACITY_FULL';
        });
        for (const u of activeUsers) {
            await apiCall(`/api/capacity/pause/${u.national_id}`, 'POST').catch(() => {});
        }
        await loadCapacityStatus();
        showToast('پایش‌های فعال متوقف (مکث) شدند.', 'info');
    } catch (e) {
        alert(e.message || 'خطا در مکث همگانی');
    }
}

async function resumeAllCapacity() {
    try {
        const pausedUsers = (allUsersData || []).filter(u => {
            const mon = capacityMonitorsData[u.national_id] || {};
            return mon.paused || mon.state === 'PAUSED';
        });
        for (const u of pausedUsers) {
            await apiCall(`/api/capacity/resume/${u.national_id}`, 'POST').catch(() => {});
        }
        await loadCapacityStatus();
        showToast('پایش متقاضیان با موفقیت ادامه یافت.', 'success');
    } catch (e) {
        alert(e.message || 'خطا در ادامه همگانی');
    }
}

function renderBankSelectList(data) {
    if (document.activeElement?.matches('[data-final-interval-nid]')) return;
    const tbody = document.getElementById('bankSelectListBody');
    const snipersContainer = document.getElementById('activeSnipersContainer');
    const standbyContainer = document.getElementById('standbyApplicantsContainer');
    const snipersCountEl = document.getElementById('activeSnipersCount');
    const standbyCountEl = document.getElementById('standbyApplicantsCount');

    if (tbody) tbody.innerHTML = '';
    let snipersHtml = '';
    let standbyHtml = '';
    let snipersCount = 0;
    let standbyCount = 0;

    (data || []).forEach((user, idx) => {
        const nid = user.national_id;
        const runtime = user.runtime || {};
        const udata = parseUserData(user.data);
        const finalEnabled = typeof udata.auto_final_submit === 'boolean' ? udata.auto_final_submit : submissionDefaults.enabled;
        const customInterval = udata.final_submit_interval_seconds ?? '';
        const mon = capacityMonitorsData[nid] || {};

        const city = mon.city || udata.city || '-';
        const province = mon.state_province || udata.state || '-';
        const bank = mon.selected_bank || runtime.target_bank || udata.primary_bank || (udata.priority_banks && udata.priority_banks[0]) || '-';
        const mode = mon.selected_mode || runtime.selected_mode || udata.bank_mode || 'online';
        const isOnline = mode === 'online';
        const modeText = isOnline 
            ? '<span class="badge badge-status-running"><i class="fa-solid fa-laptop me-1"></i>غیرحضوری</span>' 
            : '<span class="badge badge-status-neutral"><i class="fa-solid fa-building me-1"></i>حضوری</span>';

        const capState = mon.state || runtime.capacity_monitor_state || (runtime.status === 'BANK_CAPACITY_FULL' ? 'WAITING_CAPACITY' : (runtime.status || user.status));
        const badgeCls = getCapacityStateBadge(capState);
        const stateText = translateCapacityState(capState);

        const duration = mon.waiting_duration_seconds || runtime.capacity_waiting_duration || 0;
        const checks = mon.checks_count || runtime.capacity_checks_count || 0;
        const interval = mon.interval_seconds || runtime.capacity_interval || 5.0;

        const lastMsg = mon.last_system_message || runtime.capacity_last_message || runtime.last_error || 'در انتظار پاسخ سامانه...';
        const sessionActive = mon.session_active !== undefined ? mon.session_active : (runtime.browser_status === 'OPEN');
        const sessionBadge = sessionActive 
            ? '<span class="badge badge-status-success"><i class="fa-solid fa-circle-check me-1"></i>زنده و معتبر</span>'
            : (capState === 'SESSION_EXPIRED' ? '<span class="badge badge-status-danger"><i class="fa-solid fa-circle-xmark me-1"></i>منقضی‌شده</span>' : '<span class="badge badge-status-neutral">آماده</span>');

        const trackCode = mon.tracking_code || runtime.tracking_code || udata.tracking_code || '';
        const isPaused = mon.paused || (capState === 'PAUSED');

        // Populate standard table row (preserves all regression test hooks)
        if (tbody) {
            tbody.innerHTML += `
                <tr>
                    <td>${idx + 1}</td>
                    <td>
                        <strong>${escapeHtml(user.full_name)}</strong>
                        <div class="font-monospace small text-muted">${formatNid(nid)}</div>
                        <div class="bank-row-actions mt-2 d-flex gap-1 flex-wrap">
                            <button class="btn btn-sm btn-primary" onclick="startBankSelect('${nid}')">شروع</button>
                            <button class="btn btn-sm btn-warning" onclick="restartBankSelect('${nid}')">راه‌اندازی مجدد</button>
                            <button class="btn btn-sm btn-danger" onclick="stopBot('${nid}')">توقف</button>
                        </div>
                    </td>
                    <td>
                        <div>${escapeHtml(province)}</div>
                        <small class="text-muted">${escapeHtml(city)}</small>
                    </td>
                    <td>
                        <strong>${escapeHtml(bank)}</strong>
                    </td>
                    <td>${modeText}</td>
                    <td>
                        <span class="${badgeCls}">${stateText}</span>
                    </td>
                    <td class="font-monospace">${formatWaitingDuration(duration)}</td>
                    <td class="font-monospace text-center">
                        <span class="badge badge-status-info">${checks}</span>
                    </td>
                    <td class="font-monospace small text-center">${interval}s</td>
                    <td>
                        <span class="small" title="${escapeHtml(lastMsg)}" style="display:inline-block; max-width:180px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                            ${escapeHtml(lastMsg)}
                        </span>
                    </td>
                    <td>${sessionBadge}</td>
                    <td>
                        ${trackCode ? `<span class="badge bg-success font-monospace mb-1 d-block"><i class="fa-solid fa-check me-1"></i>${trackCode}</span>` : '<span class="badge badge-status-neutral mb-1 d-block">-</span>'}
                        <div class="d-flex gap-1 flex-wrap">
                            ${isPaused 
                                ? `<button class="btn btn-sm btn-outline-success" onclick="resumeApplicant('${nid}')" title="ادامه"><i class="fa-solid fa-play me-1"></i>ادامه</button>`
                                : `<button class="btn btn-sm btn-outline-secondary" onclick="pauseApplicant('${nid}')" title="مکث"><i class="fa-solid fa-pause me-1"></i>مکث</button>`
                            }
                        </div>
                    </td>
                </tr>
            `;
        }

        // Determine if user is in Active Tier (Tier 1) vs Standby Tier (Tier 2)
        const isActiveSniper = sessionActive || capState === 'WAITING_CAPACITY' || capState === 'SUBMITTING' || runtime.current_step === 'CAPACITY_FULL' || isPaused;

        if (isActiveSniper) {
            snipersCount++;
            snipersHtml += `
                <div class="sniper-card ${isPaused ? 'is-paused' : 'is-active-monitoring'}">
                    <div class="sniper-card-header">
                        <div class="sniper-applicant-info">
                            <strong>${escapeHtml(user.full_name)}</strong>
                            <span class="nid-pill"><i class="fa-regular fa-id-card me-1"></i>${formatNid(nid)}</span>
                        </div>
                        <div class="d-flex align-items-center gap-2">
                            <span class="${badgeCls} px-2 py-1">${stateText}</span>
                            ${!isPaused ? '<span class="radar-pulse" title="پایش بلادرنگ فعال است"></span>' : '<span class="badge bg-warning text-dark"><i class="fa-solid fa-pause"></i></span>'}
                        </div>
                    </div>
                    <div class="sniper-card-body">
                        <div class="sniper-target-row">
                            <span class="sniper-bank-name">
                                <i class="fa-solid fa-building-columns"></i>
                                ${escapeHtml(bank)}
                            </span>
                            ${modeText}
                        </div>
                        <div class="sniper-metrics-grid">
                            <div class="sniper-metric-item">
                                <small>موقعیت متقاضی</small>
                                <strong>${escapeHtml(province)} / ${escapeHtml(city)}</strong>
                            </div>
                            <div class="sniper-metric-item">
                                <small>مدت و تعداد استعلام</small>
                                <strong>${formatWaitingDuration(duration)} (${checks} بار)</strong>
                            </div>
                        </div>
                        <div class="sniper-system-msg" title="${escapeHtml(lastMsg)}">
                            <i class="fa-solid fa-circle-info ms-1 text-primary"></i>
                            ${escapeHtml(lastMsg)}
                        </div>
                    </div>
                    <div class="sniper-card-actions">
                        ${isPaused 
                            ? `<button class="btn-sniper-action btn-sniper-resume" onclick="resumeApplicant('${nid}')" title="ادامه پایش"><i class="fa-solid fa-play"></i><span>ادامه پایش</span></button>`
                            : `<button class="btn-sniper-action btn-sniper-pause" onclick="pauseApplicant('${nid}')" title="مکث موقت پایش"><i class="fa-solid fa-pause"></i><span>مکث پایش</span></button>`
                        }
                        <button class="btn-sniper-action btn-sniper-start" onclick="startBankSelect('${nid}')" title="استعلام فوری بدون انتظار"><i class="fa-solid fa-rotate-right"></i><span>استعلام مجدد</span></button>
                        <button class="btn-sniper-action btn-sniper-stop" onclick="stopBot('${nid}')" title="توقف و بستن نشست مرورگر"><i class="fa-solid fa-stop"></i><span>توقف کامل</span></button>
                        <button class="btn-sniper-action btn-sniper-console" onclick="switchView('dashboard')" title="مشاهده کنسول و لاگ‌های زنده"><i class="fa-solid fa-terminal"></i><span>کنسول</span></button>
                        <button class="btn-sniper-action btn-sniper-settings" onclick="editApplicantByNid('${nid}')" title="ویرایش اطلاعات و اولویت‌های متقاضی"><i class="fa-solid fa-gear"></i><span>تنظیمات</span></button>
                    </div>
                </div>
            `;
        } else {
            standbyCount++;
            standbyHtml += `
                <div class="standby-card">
                    <div class="standby-card-top">
                        <div class="standby-card-info">
                            <strong>${escapeHtml(user.full_name)}</strong>
                            <small class="font-monospace text-muted">${formatNid(nid)}</small>
                            <div class="small mt-1 text-light">
                                <i class="fa-solid fa-location-dot text-muted me-1"></i>${escapeHtml(province)} / ${escapeHtml(city)}
                            </div>
                        </div>
                        <div class="text-end">
                            <span class="badge badge-status-neutral mb-1 d-block">${escapeHtml(bank)}</span>
                            ${modeText}
                        </div>
                    </div>
                    <div class="standby-card-bottom">
                        <button class="btn btn-sm btn-primary flex-fill" onclick="startBankSelect('${nid}')">
                            <i class="fa-solid fa-play ms-1"></i> شروع پایش صفحه ۳
                        </button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="editApplicantByNid('${nid}')" title="ویرایش مشخصات و اولویت‌ها">
                            <i class="fa-solid fa-sliders"></i>
                        </button>
                    </div>
                </div>
            `;
        }
    });

    if (snipersContainer) {
        snipersContainer.innerHTML = snipersHtml || `
            <div class="text-muted small p-3 text-center w-100 border rounded" style="background:#0D203D; border-color:#1E3A5F !important;">
                در حال حاضر هیچ متقاضی فعالی در مرحله پایش بلادرنگ صفحه سوم قرار ندارد. با کلیک روی «شروع پایش صفحه ۳» از فهرست زیر، پایش را آغاز کنید.
            </div>
        `;
    }
    if (standbyContainer) {
        standbyContainer.innerHTML = standbyHtml || `
            <div class="text-muted small p-3 text-center w-100 border rounded" style="background:#0D203D; border-color:#1E3A5F !important;">
                همه متقاضیان هم‌اکنون در سطح پایش فعال قرار دارند یا متقاضی جدیدی ثبت نشده است.
            </div>
        `;
    }
    if (snipersCountEl) snipersCountEl.innerText = `${snipersCount} فعال`;
    if (standbyCountEl) standbyCountEl.innerText = `${standbyCount} متقاضی`;
}



function renderSimpleList(data, elId, actionFn, txt, cls) {

    const tbody = document.getElementById(elId);

    if(!tbody) return;

    tbody.innerHTML = '';

    data.forEach(user => {

        tbody.innerHTML += `<tr><td>${user.full_name}</td><td class="font-monospace">${formatNid(user.national_id)}</td><td><button class="btn btn-sm ${cls}" onclick="${actionFn}('${user.national_id}')">${txt}</button></td></tr>`;

    });

}



async function startStatus(nid) {

    try {

        const response = await apiCall(`/jobs/start`, 'POST', {bot_name: 'status', nid: nid});

        if (response?.status && response.status !== 'queued') {

            console.warn('Unexpected status from status bot start:', response);

        }

        setUserStatus(nid, 'Running');

        switchView('dashboard');

    } catch(e) {

        console.warn('Status bot start failed', e);

    }

}



async function actionRecover(nid) {

    try {

        const response = await apiCall('/jobs/start', 'POST', {bot_name: 'recovery', nid: nid});

        if (response?.status && response.status !== 'queued') {

            console.warn('Unexpected status from recovery bot start:', response);

        }

        setUserStatus(nid, 'Running');

        switchView('dashboard');

    } catch (e) {

        console.warn('Recovery bot start failed', e);

    }

}

function actionDeleteReq(nid) { if(confirm(getMessage('alerts.delete_confirm', 'حذف ثبت‌نام این متقاضی را صریحاً تأیید می‌کنید؟'))) apiCall(`/bot/action/delete-request/${nid}`, 'POST', {confirmed:true}); }

async function deleteApplicant(nid) {
    if (!confirm('پرونده این متقاضی از پنل حذف شود؟ این عملیات قابل بازگشت نیست.')) return;
    const response = await fetch(`${httpBase}/applicants/${encodeURIComponent(nid)}`, {method: 'DELETE'});
    if (!response.ok) {
        alert('حذف پرونده انجام نشد. ابتدا بات فعال متقاضی را متوقف کنید.');
        return;
    }
    allUsersData = allUsersData.filter((user) => user.national_id !== nid);
    renderMainList(allUsersData);
    renderDashboard(allUsersData);
}



function editUser(idx) {

    const user = allUsersData[idx];

    if(!user) return;

    editingUserId = user.id;

    document.getElementById('formTitle').innerText = `: ${user.full_name}`;

    document.getElementById('btnResetForm').style.display = 'block';

    

    let d = parseUserData(user.data); 

    

    document.getElementById('inpName').value = user.full_name || '';

    document.getElementById('inpNid').value = user.national_id || '';

    document.getElementById('inpMobile').value = d.mobile || '';

    document.getElementById('inpSpouseNationalId').value = d.spouse_national_id || (String(d.spouse || '').match(/^\d{10}$/) ? d.spouse : '');

    document.getElementById('inpPhone').value = d.phone || '';

    document.getElementById('inpZip').value = d.postal_code || d.zip_code || '';
    if (document.getElementById('inpLoanType')) {
        document.getElementById('inpLoanType').value = d.loan_type || 'marriage';
        toggleLoanTypeFields();
    }
    if (document.getElementById('inpChildNationalId')) {
        document.getElementById('inpChildNationalId').value = d.child_national_id || '';
    }
    if (document.getElementById('cd_d')) document.getElementById('cd_d').value = d.child_birth_d || '';
    if (document.getElementById('cd_m')) document.getElementById('cd_m').value = d.child_birth_m || '';
    if (document.getElementById('cd_y')) document.getElementById('cd_y').value = d.child_birth_y || '';
    if (document.getElementById('inpBankMode')) {
        document.getElementById('inpBankMode').value = d.bank_mode || '';
        toggleBranchFieldByMode();
    }
    if (document.getElementById('inpPriorityBranches')) {
        const br = d.priority_branches || (d.branch_code ? [d.branch_code] : []);
        document.getElementById('inpPriorityBranches').value = Array.isArray(br) ? br.join('، ') : String(br || '');
    }
    if (document.getElementById('inpAllowReserve')) {
        document.getElementById('inpAllowReserve').checked = Boolean(d.allow_reserve);
    }

    if (document.getElementById('inpInheritFinalSubmit')) {
        document.getElementById('inpInheritFinalSubmit').checked = typeof d.auto_final_submit !== 'boolean';
        document.getElementById('inpAutoFinalSubmit').checked = d.auto_final_submit === true;
        document.getElementById('inpFinalSubmitInterval').value = d.final_submit_interval_seconds ?? '';
        syncApplicantSubmissionControls();
    }
    document.getElementById('inpState').value = d.state || '';
    if (d.state) {
        onProvinceSelected(d.state, d.city || '');
    } else {
        const cEl = document.getElementById('inpCity');
        if (cEl) {
            cEl.innerHTML = '<option value="">-- ابتدا استان را انتخاب کنید --</option>' + (d.city ? `<option value="${escapeHtml(d.city)}" selected>${escapeHtml(d.city)}</option>` : '');
        }
    }

    document.getElementById('inpMilitary').value = d.military_status || '1';

    document.getElementById('inpTrackingCode').value = d.tracking_code || '';

    

    document.getElementById('bd_d').value = d.birth_d || ''; 

    document.getElementById('bd_m').value = d.birth_m || ''; 

    document.getElementById('bd_y').value = d.birth_y || '';

    

    document.getElementById('md_d').value = d.marriage_d || ''; 

    document.getElementById('md_m').value = d.marriage_m || ''; 

    document.getElementById('md_y').value = d.marriage_y || '';

    

    priorityBanks = d.priority_banks || d.banks || [];

    favoriteBanks = d.favorite_banks || [];

    renderBankOptions();

    renderPriorityBanks();

    renderFavoriteBanks();

    

    switchView('add');

    document.getElementById('view-add').scrollIntoView({ behavior: 'smooth' });

}



function resetAddForm() {

    editingUserId = null;

    document.getElementById('addForm').reset();
    syncApplicantSubmissionControls();
    if (document.getElementById('inpAllowReserve')) document.getElementById('inpAllowReserve').checked = true;

    document.getElementById('formTitle').innerText = "  ";

    document.getElementById('btnResetForm').style.display = 'none';

    priorityBanks = [];

    favoriteBanks = [];

    renderBankOptions();

    renderPriorityBanks();

    renderFavoriteBanks();

}



async function submitNewApplicant() {

    const nid = document.getElementById('inpNid').value;

    const name = document.getElementById('inpName').value;



    if(!/^\d{10}$/.test(nid) || !name) return alert('نام و کد ملی ۱۰ رقمی الزامی است.');
    const spouseNationalId = document.getElementById('inpSpouseNationalId').value.trim();
    if (spouseNationalId && !/^\d{10}$/.test(spouseNationalId)) return alert('کد ملی همسر باید دقیقاً ۱۰ رقم باشد.');



    const formData = {

        mobile: document.getElementById('inpMobile').value,

        spouse_national_id: spouseNationalId,

        phone: document.getElementById('inpPhone').value,

        zip_code: document.getElementById('inpZip').value.trim(),
        postal_code: document.getElementById('inpZip').value.trim(),
        loan_type: document.getElementById('inpLoanType') ? document.getElementById('inpLoanType').value : 'marriage',
        child_national_id: document.getElementById('inpChildNationalId') ? document.getElementById('inpChildNationalId').value.trim() : '',
        child_birth_d: document.getElementById('cd_d') ? document.getElementById('cd_d').value : '',
        child_birth_m: document.getElementById('cd_m') ? document.getElementById('cd_m').value : '',
        child_birth_y: document.getElementById('cd_y') ? document.getElementById('cd_y').value : '',
        auto_final_submit: document.getElementById('inpInheritFinalSubmit') ? (document.getElementById('inpInheritFinalSubmit').checked ? null : document.getElementById('inpAutoFinalSubmit').checked) : null,
        final_submit_interval_seconds: (document.getElementById('inpFinalSubmitInterval') && document.getElementById('inpFinalSubmitInterval').value !== '') ? Number(document.getElementById('inpFinalSubmitInterval').value) : null,
        bank_mode: document.getElementById('inpBankMode') ? document.getElementById('inpBankMode').value : '',
        allow_reserve: document.getElementById('inpAllowReserve') ? document.getElementById('inpAllowReserve').checked : false,
        priority_branches: document.getElementById('inpPriorityBranches') && document.getElementById('inpBankMode')?.value !== 'online'
            ? document.getElementById('inpPriorityBranches').value.split(/[،,\n]+/).map(s => s.trim()).filter(Boolean)
            : [],

        state: document.getElementById('inpState').value,

        city: document.getElementById('inpCity').value,

        military_status: document.getElementById('inpMilitary').value,

        tracking_code: document.getElementById('inpTrackingCode').value,

        birth_d: document.getElementById('bd_d').value,

        birth_m: document.getElementById('bd_m').value,

        birth_y: document.getElementById('bd_y').value,

        marriage_d: document.getElementById('md_d').value,

        marriage_m: document.getElementById('md_m').value,

        marriage_y: document.getElementById('md_y').value,

        priority_banks: priorityBanks,

        favorite_banks: favoriteBanks,

        banks: priorityBanks

    };



    const payload = {

        id: editingUserId,

        full_name: name,

        national_id: nid,

        data: formData

    };



    await apiCall('/applicants', 'POST', payload);

    alert(getMessage('alerts.save_success', '    .'));

    if(editingUserId) resetAddForm();

    switchView('list');

}



// ==========================================

// ***     ***

// ==========================================



async function loadSettings() {

    try {

        const s = await (await fetch(`${httpBase}/settings`)).json();

        

        //  

        document.getElementById('set_captcha').value = s.captcha_delay || 0.1;

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
        document.getElementById('set_final_submit').checked = Boolean(s.auto_final_submit ?? s.final_submit ?? false);

        

        document.getElementById('set_useproxy').checked = s.use_proxy || false;

        document.getElementById('set_proxylist').value = s.proxy_list || '';

        if (document.getElementById('set_gsm_modem')) {
            document.getElementById('set_gsm_modem').checked = s.gsm_modem_enabled === true;
        }
        if (document.getElementById('gsmBaudSelect')) {
            document.getElementById('gsmBaudSelect').value = String(s.gsm_modem_baudrate || '115200');
        }

        if (document.getElementById('set_sniper_mode')) {
            document.getElementById('set_sniper_mode').checked = s.sniper_mode_enabled === true;
        }
        if (document.getElementById('set_sniper_interval')) {
            document.getElementById('set_sniper_interval').value = Math.max(5, Number(s.sniper_interval_ms ?? 5000) / 1000);
        }
        if (document.getElementById('set_auto_switch_bank')) {
            document.getElementById('set_auto_switch_bank').checked = s.auto_switch_alternative_bank !== false;
        }
        if (document.getElementById('set_fallback_in_person')) {
            document.getElementById('set_fallback_in_person').checked = s.fallback_to_in_person === true;
        }
        if (document.getElementById('set_bank_cooldown')) {
            document.getElementById('set_bank_cooldown').value = s.bank_cooldown_seconds ?? 30;
        }

        if (document.getElementById('set_max_concurrency')) {
            document.getElementById('set_max_concurrency').value = s.max_concurrency ?? 4;
        }
        if (document.getElementById('set_anti_detection')) {
            document.getElementById('set_anti_detection').checked = s.anti_detection_enabled !== false;
        }
        if (document.getElementById('set_proxy_strategy')) {
            document.getElementById('set_proxy_strategy').value = s.proxy_rotation_strategy || 'round_robin';
        }

    } catch(e) {}

}



async function loadBrowserProfile() {

    try {

        const profile = await (await fetch(`${httpBase}/settings/browser-profile`)).json();

        browserProfile = profile;

        if(document.getElementById('browserSelect')) {

            document.getElementById('browserSelect').value = profile.browser || 'chromium';

            document.getElementById('browserHeadless').checked = !!profile.headless;

            document.getElementById('browserSlowMo').value = profile.slow_mo_ms ?? 0;

            document.getElementById('browserTimeout').value = profile.timeout_ms ?? 30000;

            document.getElementById('browserViewportWidth').value = profile.window_width ?? profile.viewport?.width ?? 540;

            document.getElementById('browserViewportHeight').value = profile.window_height ?? profile.viewport?.height ?? 680;

            if (document.getElementById('browserWindowPosition')) {
                document.getElementById('browserWindowPosition').value = profile.window_position || 'top_right';
            }
            if (document.getElementById('browserRememberPosition')) {
                document.getElementById('browserRememberPosition').checked = profile.remember_position !== false;
            }

            document.getElementById('browserUserDataDir').value = profile.user_data_dir || '';

            document.getElementById('browserProxy').value = profile.proxy || '';

        }

        updateBrowserProfileSummary(profile);

    } catch(e) {}

}



function updateBrowserProfileSummary(profile) {

    const summary = document.getElementById('browserProfileSummary');

    const label = document.getElementById('currentBrowserLabel');

    if (summary) {

        summary.textContent = `Browser: ${profile.browser} | Headless: ${profile.headless ? 'Yes' : 'No'} | SlowMo: ${profile.slow_mo_ms}ms`;

    }

    if (label) {

        label.textContent = `${profile.browser || '--'}${profile.headless ? ' (headless)' : ''}`;

    }

}



async function saveBrowserProfile() {

    const payload = {

        browser: document.getElementById('browserSelect').value,

        headless: document.getElementById('browserHeadless').checked,

        slow_mo_ms: parseInt(document.getElementById('browserSlowMo').value || '0', 10),

        timeout_ms: parseInt(document.getElementById('browserTimeout').value || '30000', 10),

        window_width: parseInt(document.getElementById('browserViewportWidth').value || '540', 10),
        window_height: parseInt(document.getElementById('browserViewportHeight').value || '680', 10),
        window_position: document.getElementById('browserWindowPosition')?.value || 'top_right',
        start_maximized: false,
        always_on_top: false,
        remember_position: document.getElementById('browserRememberPosition') ? document.getElementById('browserRememberPosition').checked : true,
        viewport: {

            width: parseInt(document.getElementById('browserViewportWidth').value || '540', 10),

            height: parseInt(document.getElementById('browserViewportHeight').value || '680', 10)

        },

        user_data_dir: document.getElementById('browserUserDataDir').value || null,

        proxy: document.getElementById('browserProxy').value || null

    };

    const res = await fetch(`${httpBase}/settings/browser-profile`, {

        method: 'PUT',

        headers: {'Content-Type': 'application/json'},

        body: JSON.stringify(payload)

    });

    if(res.ok) {

        const saved = await res.json();

        browserProfile = saved;

        updateBrowserProfileSummary(saved);

        alert(getMessage('alerts.browser_settings_saved', '   .'));

    } else {

        alert(getMessage('alerts.browser_settings_failed', '    .'));

    }

}



function applyBrowserPreset(preset) {

    if(preset === 'fast') {

        document.getElementById('browserHeadless').checked = true;

        document.getElementById('browserSlowMo').value = 0;

    } else if(preset === 'debug') {

        document.getElementById('browserHeadless').checked = false;

        document.getElementById('browserSlowMo').value = 100;

    }

}



async function testBrowserLaunch() {

    const res = await fetch(`${httpBase}/browser/test-launch`, {method: 'POST'});

    if(res.ok) {

        alert(getMessage('alerts.test_launch_ok', 'Test launch  .'));

    } else {

        alert(getMessage('alerts.test_launch_failed', 'Test launch    .'));

    }

}



async function saveSettings() {

    //    (  )

    const payload = {

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
        auto_repair_saved_pages: document.getElementById('set_auto_repair').checked,

        headless: document.getElementById('set_headless').checked,
        browser_headless: document.getElementById('set_headless').checked,
        browser_window_width: parseInt(document.getElementById('browserViewportWidth')?.value || '540', 10),
        browser_window_height: parseInt(document.getElementById('browserViewportHeight')?.value || '680', 10),
        browser_window_position: document.getElementById('browserWindowPosition')?.value || 'top_right',
        browser_start_maximized: false,
        browser_always_on_top: false,
        browser_remember_position: document.getElementById('browserRememberPosition') ? document.getElementById('browserRememberPosition').checked : true,
        browser_display_mode: document.getElementById('set_browser_display_mode').value,

        

        captcha_mode: 'model',

        final_submit: document.getElementById('set_final_submit').checked,

        

        use_proxy: document.getElementById('set_useproxy').checked,

        proxy_list: document.getElementById('set_proxylist').value,

        gsm_modem_enabled: document.getElementById('set_gsm_modem') ? document.getElementById('set_gsm_modem').checked : false,
        gsm_modem_port: document.getElementById('gsmPortSelect') ? document.getElementById('gsmPortSelect').value : 'COM3',
        gsm_modem_baudrate: document.getElementById('gsmBaudSelect') ? parseInt(document.getElementById('gsmBaudSelect').value, 10) : 115200,

        sniper_mode_enabled: document.getElementById('set_sniper_mode') ? document.getElementById('set_sniper_mode').checked : false,
        sniper_interval_ms: Math.max(5, Number(document.getElementById('set_sniper_interval')?.value || 5)) * 1000,
        auto_switch_alternative_bank: document.getElementById('set_auto_switch_bank') ? document.getElementById('set_auto_switch_bank').checked : true,
        bank_cooldown_seconds: document.getElementById('set_bank_cooldown') ? parseInt(document.getElementById('set_bank_cooldown').value, 10) : 30,
        browser_page_zoom: document.getElementById('set_browser_page_zoom') ? parseFloat(document.getElementById('set_browser_page_zoom').value) : 1.25,
        focus_view_enabled: document.getElementById('set_focus_view_enabled') ? document.getElementById('set_focus_view_enabled').checked : true,
        fallback_to_in_person: document.getElementById('set_fallback_in_person') ? document.getElementById('set_fallback_in_person').checked : false,
        max_concurrency: document.getElementById('set_max_concurrency') ? parseInt(document.getElementById('set_max_concurrency').value, 10) : 4,
        anti_detection_enabled: document.getElementById('set_anti_detection') ? document.getElementById('set_anti_detection').checked : true,
        proxy_rotation_strategy: document.getElementById('set_proxy_strategy') ? document.getElementById('set_proxy_strategy').value : 'round_robin'

    };

    

    await apiCall('/settings', 'POST', payload);
    setSubmissionDefaults(payload);

    applyBrowserDisplayMode(payload.browser_display_mode);

    alert(getMessage('alerts.settings_saved', 'تنظیمات با موفقیت ذخیره شد.'));

}

async function testProxyPool() {
    const btn = document.getElementById('btnTestProxies');
    const container = document.getElementById('proxyTestResults');
    if (!container) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin ms-1"></i> در حال آزمایش...';
    }
    container.innerHTML = '<div class="alert alert-info py-2 px-3 small my-2"><i class="fa-solid fa-spinner fa-spin ms-1"></i> در حال بررسی و تست اتصال پروکسی‌ها...</div>';

    try {
        const res = await fetch(`${httpBase}/proxies/test`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ timeout: 3.5 })
        });
        const data = await res.json();

        if (!data.results || data.results.length === 0) {
            container.innerHTML = '<div class="alert alert-warning py-2 px-3 small my-2">هیچ پروکسی معتبری در لیست یافت نشد یا پروکسی‌ها غیرفعال هستند.</div>';
            return;
        }

        let html = `<div class="mt-2 p-2 rounded" style="background:var(--bg-card, #f8fafc); border:1px solid var(--border-soft, #e2e8f0);">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <span class="small fw-bold">نتیجه آزمایش سلامت (${data.total} پروکسی: <span class="text-success">${data.healthy} سالم</span> | <span class="text-danger">${data.failed} خطا</span>)</span>
            </div>
            <div class="table-responsive" style="max-height:220px; overflow-y:auto;">
                <table class="table table-sm table-borderless align-middle mb-0" style="font-size:0.83rem;">
                    <thead>
                        <tr class="text-muted" style="border-bottom:1px solid var(--border-soft, #cbd5e1);">
                            <th>آدرس پروکسی</th>
                            <th>وضعیت</th>
                            <th>پینگ</th>
                            <th>توضیحات</th>
                        </tr>
                    </thead>
                    <tbody>`;

        for (const item of data.results) {
            const isOk = item.status === 'healthy';
            const badgeClass = isOk ? 'badge bg-success-subtle text-success' : 'badge bg-danger-subtle text-danger';
            const statusIcon = isOk ? '<i class="fa-solid fa-circle-check ms-1"></i>سالم' : '<i class="fa-solid fa-circle-xmark ms-1"></i>خطا';
            const latencyStr = item.latency_ms > 0 ? `${item.latency_ms} ms` : '-';
            const errorMsg = item.error ? `<span class="text-muted small">${item.error}</span>` : '<span class="text-success small">آماده اتصال</span>';

            html += `<tr>
                <td dir="ltr" class="font-monospace text-start">${item.proxy}</td>
                <td><span class="${badgeClass}">${statusIcon}</span></td>
                <td><span class="fw-bold">${latencyStr}</span></td>
                <td>${errorMsg}</td>
            </tr>`;
        }

        html += `</tbody></table></div></div>`;
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger py-2 px-3 small my-2">خطا در ارتباط با سرور تست پروکسی: ${err.message}</div>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-bolt ms-1"></i> آزمایش سلامت پروکسی‌ها';
        }
    }
}



async function loadAllowedDomains() {

    const statusEl = document.getElementById('allowedDomainsStatus');

    if (statusEl) statusEl.textContent = '';

    try {

        const res = await fetch(`${httpBase}/settings/allowed-domains`);

        if (!res.ok) throw new Error('Failed');

        allowedDomainsState = await res.json();

        renderAllowedDomains();

    } catch (e) {

        if (statusEl) {

            statusEl.textContent = getMessage('alerts.allowed_domains_failed', '    .');

            statusEl.className = 'small text-danger';

        }

    }

}



function renderAllowedDomains() {

    const envBox = document.getElementById('allowedDomainsEnv');

    const effectiveBox = document.getElementById('allowedDomainsEffective');

    const dbBox = document.getElementById('allowedDomainsDbList');

    const statusEl = document.getElementById('allowedDomainsStatus');

    if (statusEl) {

        statusEl.textContent = '';

        statusEl.className = 'small text-muted';

    }



    const renderList = (container, items, emptyText) => {

        if (!container) return;

        if (!items || !items.length) {

            container.innerHTML = `<span class="text-muted">${emptyText}</span>`;

            return;

        }

        container.innerHTML = items.map(item => `<span class="badge bg-secondary me-1 mb-1">${item}</span>`).join('');

    };



    renderList(envBox, allowedDomainsState.env || [], '   .');

    renderList(effectiveBox, allowedDomainsState.effective || [], '   .');



    if (dbBox) {

        if (!allowedDomainsState.db || !allowedDomainsState.db.length) {

            dbBox.innerHTML = '<span class="text-muted">   .</span>';

        } else {

            dbBox.innerHTML = allowedDomainsState.db.map((domain, idx) => `

                <span class="badge bg-light text-dark border me-1 mb-1">

                    ${domain}

                    <i class="fas fa-times text-danger ms-1" style="cursor:pointer" onclick="removeAllowedDomain(${idx})"></i>

                </span>

            `).join('');

        }

    }

}



function addAllowedDomain() {

    const input = document.getElementById('allowedDomainInput');

    if (!input) return;

    const value = input.value.trim().toLowerCase();

    if (!value) return;

    if (!allowedDomainsState.db.includes(value)) {

        allowedDomainsState.db.push(value);

        renderAllowedDomains();

    }

    input.value = '';

}



function removeAllowedDomain(index) {

    allowedDomainsState.db.splice(index, 1);

    renderAllowedDomains();

}



async function saveAllowedDomains() {

    const statusEl = document.getElementById('allowedDomainsStatus');

    if (statusEl) {

        statusEl.textContent = getMessage('alerts.allowed_domains_saving', '   ...');

        statusEl.className = 'small text-muted';

    }

    try {

        const res = await fetch(`${httpBase}/settings/allowed-domains`, {

            method: 'PUT',

            headers: {'Content-Type': 'application/json'},

            body: JSON.stringify({domains: allowedDomainsState.db})

        });

        const data = await res.json();

        if (!res.ok) {

            const message = data?.detail?.details?.invalid_domains

                ? `${getMessage('alerts.allowed_domains_invalid', ' ')}: ${data.detail.details.invalid_domains.join(', ')}`

                : getMessage('alerts.allowed_domains_failed', '    .');

            if (statusEl) {

                statusEl.textContent = message;

                statusEl.className = 'small text-danger';

            }

            return;

        }

        allowedDomainsState = data;

        renderAllowedDomains();

        if (statusEl) {

            statusEl.textContent = getMessage('alerts.allowed_domains_saved', '   .');

            statusEl.className = 'small text-success';

        }

    } catch (e) {

        if (statusEl) {

            statusEl.textContent = getMessage('alerts.allowed_domains_failed', '    .');

            statusEl.className = 'small text-danger';

        }

    }

}



async function testAllowlistUrl() {

    const input = document.getElementById('allowlistTestUrl');

    const resultEl = document.getElementById('allowlistTestResult');

    if (!input || !resultEl) return;

    const url = input.value.trim();

    if (!url) return;

    resultEl.textContent = getMessage('alerts.allowlist_checking', '  ...');

    resultEl.className = 'small text-muted';

    try {

        const res = await fetch(`${httpBase}/settings/allowed-domains/check`, {

            method: 'POST',

            headers: {'Content-Type': 'application/json'},

            body: JSON.stringify({url})

        });

        const data = await res.json();

        if (!res.ok) {

            resultEl.textContent = getMessage('alerts.allowlist_check_failed', '   .');

            resultEl.className = 'small text-danger';

            return;

        }

        if (data.allowed) {

            resultEl.textContent = getMessage('alerts.allowlist_allowed', '   .');

            resultEl.className = 'small text-success';

        } else {

            resultEl.textContent = getMessage('alerts.allowlist_blocked', '     .');

            resultEl.className = 'small text-warning';

        }

    } catch (e) {

        resultEl.textContent = getMessage('alerts.allowlist_check_failed', '   .');

        resultEl.className = 'small text-danger';

    }

}



//   

function translateStatus(s) {
    const fallback = getMessage('status.ready', 'آماده');
    if (!s) return fallback;
    const raw = String(s).trim();
    const low = raw.toLowerCase();
    if (raw === 'منتظر CAPTCHA' || low.includes('captcha_manual')) return 'منتظر CAPTCHA';
    if (raw === 'منتظر پیامک' || low.includes('waiting sms') || low.includes('otp_form') || low.includes('otp_required')) return 'منتظر پیامک';
    if (raw === 'انتخاب بانک' || low.includes('bank_select') || low.includes('monitoring') || low.includes('selecting_bank')) return 'انتخاب بانک';
    if (raw === 'ظرفیت تکمیل' || low.includes('bank_capacity_full') || low.includes('capacity_full')) return 'ظرفیت تکمیل';
    if (raw === 'نیازمند ورود مجدد' || low.includes('session_expired')) return 'نیازمند ورود مجدد';
    if (raw === 'ثبت نهایی موفق' || low.includes('final_submit_confirmed') || low.includes('succ')) return 'ثبت نهایی موفق';
    if (low.includes('run')) return getMessage('status.running', 'در حال اجرا');
    if (low.includes('wait')) return getMessage('status.waiting_sms', 'منتظر پیامک');
    if (low.includes('stop')) return getMessage('status.stopped', 'متوقف');
    return raw;
}

function toggleLoanTypeFields() {
    const loanType = document.getElementById('inpLoanType')?.value || 'marriage';
    const isChild = loanType === 'childbearing';
    const spouseLbl = document.getElementById('lblSpouseNid');
    const childLbl = document.getElementById('lblChildNid');
    const marriageBlock = document.getElementById('marriageDateBlock');
    const childBlock = document.getElementById('childBirthDateBlock');
    if (spouseLbl) spouseLbl.style.display = isChild ? 'none' : '';
    if (childLbl) childLbl.style.display = isChild ? '' : 'none';
    if (marriageBlock) marriageBlock.style.display = isChild ? 'none' : '';
    if (childBlock) childBlock.style.display = isChild ? '' : 'none';
}

function toggleBranchFieldByMode() {
    const mode = document.getElementById('inpBankMode')?.value || '';
    const branchInput = document.getElementById('inpPriorityBranches');
    if (!branchInput) return;
    if (mode === 'only_online' || mode === 'online') {
        branchInput.value = '';
        branchInput.disabled = true;
        branchInput.placeholder = 'در روش فقط غیرحضوری نیازی به تعیین شعبه نیست';
    } else {
        branchInput.disabled = false;
        branchInput.placeholder = 'اختیاری: کد یا نام شعبه (در صورت خالی بودن، اولین شعبه انتخاب می‌شود)';
    }
}

async function uploadApplicantsExcel() {
    const fileInput = document.getElementById('excelUploadInput');
    const resEl = document.getElementById('excelImportResult');
    if (!fileInput || !fileInput.files.length) {
        if (resEl) resEl.innerHTML = '<span class="text-danger">لطفاً ابتدا فایل اکسل را انتخاب کنید.</span>';
        return;
    }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    if (resEl) resEl.innerHTML = '<span class="text-muted">در حال بارگذاری و اعتبارسنجی...</span>';
    try {
        const res = await fetch(`${httpBase}/applicants/import-excel`, { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) {
            if (resEl) resEl.innerHTML = `<span class="text-danger">${escapeHtml(data.detail || 'خطا در بارگذاری اکسل')}</span>`;
            return;
        }
        let html = `<span class="text-success fw-bold">تعداد ${data.imported || 0} پرونده معتبر ذخیره شد.</span>`;
        if (Array.isArray(data.errors) && data.errors.length > 0) {
            const errLines = data.errors.map(e => `ردیف ${e.row} (${escapeHtml(e.full_name)}): ${escapeHtml((e.errors || []).join('، '))}`).join('<br>');
            html += `<div class="text-danger mt-1"><strong>خطاهای ردیف‌های ناقص:</strong><br>${errLines}</div>`;
        }
        if (resEl) resEl.innerHTML = html;
        await pollApplicants();
    } catch (err) {
        if (resEl) resEl.innerHTML = `<span class="text-danger">${escapeHtml(err.message)}</span>`;
    }
}

function getStatusBadge(s) {
    if (!s) return 'badge-status-neutral';
    const lower = String(s).toLowerCase();
    if (lower.includes('succ') || lower.includes('موفق') || lower.includes('تکمیل شد') || lower.includes('رهگیری')) return 'badge-status-success';
    if (lower.includes('stop') || lower.includes('توقف') || lower.includes('خطا') || lower.includes('error') || lower.includes('رد') || lower.includes('منقضی')) return 'badge-status-danger';
    if (lower.includes('wait') || lower.includes('انتظار') || lower.includes('sms') || lower.includes('پیامک') || lower.includes('otp') || lower.includes('شروع مجدد') || lower.includes('سهمیه')) return 'badge-status-warning';
    if (lower.includes('run') || lower.includes('اجرا') || lower.includes('ثبت') || lower.includes('ورود') || lower.includes('پایش') || lower.includes('انتخاب') || lower.includes('select') || lower.includes('register')) return 'badge-status-running';
    return 'badge-status-info';
}

async function startBankSelect(nid) {
    if (pendingStarts.has(nid)) return;
    pendingStarts.add(nid);
    try {
        await apiCall('/jobs/start', 'POST', {bot_name: 'select', nid: nid});
        setUserStatus(nid, 'Running');
        switchView('dashboard');
    } catch (error) {
        alert(error.message || 'شروع ربات ناموفق بود.');
    } finally {
        pendingStarts.delete(nid);
    }

}

async function restartBankSelect(nid) {
    if (pendingStarts.has(nid)) return;
    pendingStarts.add(nid);
    try {
        await apiCall('/jobs/restart', 'POST', {bot_name: 'select', nid: nid});
        setUserStatus(nid, 'Running');
        switchView('dashboard');
    } catch (error) {
        alert(error.message || 'راه‌اندازی مجدد ربات ناموفق بود.');
    } finally {
        pendingStarts.delete(nid);
    }

}

async function repairSavedPages() {
    const status = document.getElementById('savedPagesRepairResult');
    if (status) status.textContent = 'در حال بررسی...';
    try {
        const result = await apiCall('/saved-pages/repair', 'POST', {});
        if (status) {
            status.textContent = `پوشه: ${result.folders || 0}، مانیفست ترمیم‌شده: ${result.manifests_repaired || 0}، فایل بازیابی‌شده: ${result.orphan_files_added || 0}، فایل مفقود: ${(result.missing_files || []).length}، تکراری: ${(result.duplicates || []).length}`;
        }
    } catch (error) {
        if (status) status.textContent = error.message || 'ترمیم ناموفق بود.';
    }
}

async function startRegister(nid) {

    await apiCall('/jobs/start', 'POST', {bot_name: 'register', nid: nid});

    setUserStatus(nid, 'Running');

    switchView('dashboard');

}

async function stopBot(nid) {

    setUserStatus(nid, 'Stopped');

    refreshCurrentView();

    markDashboardStopped(nid);

    const jobId = jobIdByNid.get(nid);

    try {

        if (jobId) {

            await apiCall(`/jobs/cancel/${jobId}`, 'POST');

        } else {

            console.warn(`No job id tracked for ${nid}, falling back to legacy stop endpoint.`);

            await apiCall(`/bot/stop/${nid}`, 'POST');

        }

    } catch (e) {

        console.warn('Stop request failed', e);

    }

    setTimeout(fetchDashboardData, 1000);

}

async function stopBank(nid, bankName) {

    try {

        await apiCall(`/applicants/${nid}/banks/stop`, 'POST', {bank: bankName});

        fetchDashboardData();

    } catch (e) {

        console.warn('Stop bank failed', e);

    }

}

async function apiCall(u, m, b) {
    const options = {method: m, headers: {'Content-Type': 'application/json'}};
    if (b !== undefined) options.body = JSON.stringify(b);
    const response = await fetch(httpBase + u, options);
    let payload = {};
    try { payload = await response.json(); } catch (e) {}
    if (!response.ok) {
        throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
    }
    return payload;
}



function setupLogFilters() {

    const textInput = document.getElementById('logFilterText');

    const nidInput = document.getElementById('logFilterNid');

    const jobInput = document.getElementById('logFilterJob');

    const typeSelect = document.getElementById('logFilterType');

    const levelSelect = document.getElementById('logFilterLevel');

    const clearBtn = document.getElementById('logClearBtn');



    if(textInput) textInput.addEventListener('input', () => { logFilters.text = textInput.value.trim().toLowerCase(); renderLogs(); });

    if(nidInput) nidInput.addEventListener('input', () => { logFilters.nid = nidInput.value.trim(); renderLogs(); });

    if(jobInput) jobInput.addEventListener('input', () => { logFilters.jobId = jobInput.value.trim(); renderLogs(); });

    if(typeSelect) typeSelect.addEventListener('change', () => { logFilters.type = typeSelect.value; renderLogs(); });

    if(levelSelect) levelSelect.addEventListener('change', () => { logFilters.level = levelSelect.value; renderLogs(); });

    if(clearBtn) clearBtn.addEventListener('click', () => {

        logEvents = [];

        renderLogs(true);

    });

}



function connectWebSocket() {
    if (wsClient && (wsClient.readyState === WebSocket.OPEN || wsClient.readyState === WebSocket.CONNECTING)) {
        return;
    }
    if (wsReconnectTimer) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
    }
    if (wsClient) {
        try {
            wsClient.onopen = null;
            wsClient.onmessage = null;
            wsClient.onerror = null;
            wsClient.onclose = null;
        } catch (e) {}
        try {
            if (wsClient.readyState === WebSocket.OPEN || wsClient.readyState === WebSocket.CONNECTING) {
                wsClient.close(1000, 'reconnect');
            }
        } catch (e) {}
    }

    setConnectionState('CONNECTING');
    wsClient = new WebSocket(`${wsBase}/ws`);
    const socketRef = wsClient;

    wsClient.onopen = () => {
        if (wsClient !== socketRef) return;
        setConnectionState('CONNECTED');
        wsReconnectDelay = WS_RECONNECT_MIN_MS;
        lastWsEventAt = Date.now();
        stopPolling();
        if (wsHeartbeatTimer) clearInterval(wsHeartbeatTimer);
        wsHeartbeatTimer = setInterval(() => {
            if (!isWebSocketOpen()) return;
            const now = Date.now();
            if (lastWsEventAt && now - lastWsEventAt > WS_PING_INTERVAL_MS * 2) {
                setConnectionState('DEGRADED');
            }
            try {
                wsClient.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
            } catch (e) {}
        }, WS_PING_INTERVAL_MS);
    };

    wsClient.onmessage = (event) => {
        if (wsClient !== socketRef) return;
        try {
            const payload = JSON.parse(event.data);
            if (payload?.type === 'ping') {
                try {
                    wsClient.send(JSON.stringify({ type: 'pong', ts: Date.now() }));
                } catch (e) {}
                return;
            }
            if (payload?.type === 'pong') return;
            lastWsEventAt = Date.now();
            if (connectionState !== 'CONNECTED') setConnectionState('CONNECTED');
            handleSocketEvent(payload);
        } catch (e) {
            console.warn('Invalid WS payload', e);
        } finally {
            updateDebugPanel();
        }
    };

    wsClient.onerror = () => {
        if (wsClient !== socketRef) return;
        try { wsClient.close(); } catch (e) {}
    };

    wsClient.onclose = (event) => {
        if (wsClient !== socketRef) return;
        if (wsHeartbeatTimer) {
            clearInterval(wsHeartbeatTimer);
            wsHeartbeatTimer = null;
        }
        wsClient = null;
        setConnectionState('DISCONNECTED');
        startPolling(POLL_INTERVAL_DISCONNECTED_MS);
        const delay = Math.min(wsReconnectDelay, WS_RECONNECT_MAX_MS);
        wsReconnectTimer = setTimeout(connectWebSocket, delay);
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_MS);
    };
}


function applyApplicantUpdate(payload) {
    if (!payload) return;
    if (payload.type === 'applicants_snapshot' && Array.isArray(payload.applicants)) {
        allUsersData = payload.applicants;
        applicantsHash = JSON.stringify(allUsersData);
        refreshCurrentView();
        if (currentViewId === 'dashboard') renderDashboard(allUsersData);
        return;
    }
    if (payload.type !== 'applicant_updated') return;
    const nid = payload.national_id || payload.nid;
    if (!nid) return;
    const idx = allUsersData.findIndex((u) => u.national_id === nid);
    const record = {
        id: payload.id,
        full_name: payload.full_name || '',
        national_id: payload.national_id || nid,
        status: payload.status || 'Ready',
        last_log: payload.last_log || '-',
        data: payload.data || {},
        runtime: payload.runtime || {},
    };
    if (idx >= 0) {
        allUsersData[idx] = { ...allUsersData[idx], ...record };
    } else {
        allUsersData.unshift(record);
    }
    if (currentViewId === 'dashboard') renderDashboard(allUsersData);
    refreshCurrentView();
}

function handleSocketEvent(payload) {

    if (!payload || !payload.type) return;

    trackJobForNid(payload);
    applyApplicantUpdate(payload);

    if (payload.type === 'queue_snapshot') {
        if (payload.stats) renderQueueStats(payload.stats);
        if (Array.isArray(payload.items)) {
            window.cachedQueueItems = payload.items;
            if (typeof filterQueueTable === 'function') filterQueueTable();
        }
    } else if (payload.type === 'fsm_state_changed') {
        if (typeof currentViewId !== 'undefined' && currentViewId === 'queue') {
            loadQueueData();
        }
    } else if (payload.type === 'otp_request_created' || payload.type === 'otp_delivered') {
        if (typeof currentViewId !== 'undefined' && currentViewId === 'queue') {
            loadOtpQueueData();
        }
    }

    logEvents.push(payload);
    if (logEvents.length > LOG_BUFFER_LIMIT) {
        logEvents.shift();
    }
    scheduleRenderLogs();

    // Phase 4: Real-time Sound & Toast Alert Dispatching
    const evType = (payload.type || '').toLowerCase();
    const evCode = (payload.event_code || payload.event || '').toUpperCase();

    if (evType === 'favorite_found' || evType === 'quota_available' || evCode === 'BANK_OPTION_FOUND') {
        const bankName = payload.bank || payload.detail || payload.result || 'بانک منتخب';
        showFavoriteToast({
            bank: bankName,
            detail: payload.detail || payload.message,
            nid: payload.nid
        });
        playAlertSound('quota');
    } else if (evType === 'registration_confirmed' || evCode === 'FINAL_SUBMIT_CONFIRMED' || evCode === 'BANK_FINAL_SUBMIT_CONFIRMED') {
        const tracking = payload.tracking_code || payload.result || '';
        showMahanToast(
            'ثبت نام قطعی انجام شد! 🎉',
            `درخواست تسهیلات با موفقیت در سامانه ثبت شد.${tracking ? ' کد پیگیری: ' + tracking : ''}`,
            'success',
            { icon: 'fa-trophy', delay: 10000 }
        );
        playAlertSound('success');
        triggerCelebrationConfetti();
    } else if (evType === 'otp_required' || evCode === 'OTP_REQUIRED') {
        showMahanToast(
            'نیاز به کد پیامک (OTP) 📱',
            payload.message || 'سامانه در انتظار ورود کد تایید پیامکی است.',
            'info',
            { icon: 'fa-comment-sms', delay: 8000 }
        );
        playAlertSound('action');
    } else if (evType === 'capacity_full' || evCode === 'BANK_CAPACITY_FULL') {
        showMahanToast(
            'تکمیل ظرفیت بانک ⚠️',
            payload.message || payload.detail || 'ظرفیت پذیرش بانک در این استان تکمیل است.',
            'warning',
            { icon: 'fa-triangle-exclamation', delay: 6000 }
        );
        playAlertSound('warning');
    }



}

function trackJobForNid(payload) {

    const nid = payload.nid;

    const jobId = payload.job_id;

    if (!nid || !jobId) return;

    jobIdByNid.set(nid, jobId);

    if (payload.type === 'job_status') {

        const status = (payload.status || '').toLowerCase();

        if (['queued', 'running'].includes(status)) {
            window.currentActiveBotNid = String(nid);
        }

        if (['stopped', 'cancelled', 'failed', 'completed', 'success'].includes(status)) {

            jobIdByNid.delete(nid);

            if (String(window.currentActiveBotNid || '') === String(nid)) {
                window.currentActiveBotNid = null;
            }

        }

    }

}



function setUserStatus(nid, status) {

    if (!nid || !allUsersData.length) return;

    const user = allUsersData.find(item => item.national_id === nid);

    if (user) {

        user.status = status;

    }

}



function refreshCurrentView() {

    if (!allUsersData.length) return;

    if (currentViewId === 'list') renderMainList(allUsersData);

    if (currentViewId === 'bank-select') {
        renderBankSelectList(allUsersData);
        loadCapacityStatus();
    }

    if (currentViewId === 'saved-pages') renderSavedPagesTable(filteredSavedPages);

    if (currentViewId === 'status') renderStatusList(allUsersData);

    if (currentViewId === 'recover') renderRecoverList(allUsersData);

    if (currentViewId === 'delete-req') renderDeleteReqList(allUsersData);

}



function renderLogs(clearInitial = false) {

    const term = document.getElementById('terminalBox');

    if (!term) return;

    term.innerHTML = '';

    if (!logEvents.length && clearInitial) {

        term.innerHTML = '<div class="text-muted">No log events.</div>';

        return;

    }

    const filtered = logEvents.filter((event) => {

        if (logFilters.type && event.type !== logFilters.type) return false;

        if (logFilters.level && (event.level || '').toLowerCase() !== logFilters.level) return false;

        if (logFilters.nid && (event.nid || '').indexOf(logFilters.nid) === -1) return false;

        if (logFilters.jobId && (event.job_id || '').indexOf(logFilters.jobId) === -1) return false;

        if (logFilters.text) {

            const haystack = `${event.message || ''} ${event.status || ''} ${event.name || ''} ${event.value || ''}`.toLowerCase();

            if (!haystack.includes(logFilters.text)) return false;

        }

        return true;

    });



    if (!filtered.length) {

        term.innerHTML = '<div class="text-muted">  .</div>';

        return;

    }



    filtered.forEach((event) => {

        const div = document.createElement('div');

        const timeText = event.ts ? new Date(event.ts * 1000).toLocaleTimeString('fa-IR') : '';

        const nidLabel = event.nid ? `<span style="color:#0d9488;font-weight:700">${maskSensitive(event.nid)}</span>` : '<span style="color:#0d9488;font-weight:700">system</span>';

        const jobLabel = event.job_id ? `<span style="color:#9ccc65">#${event.job_id.slice(0, 8)}</span>` : '';

        const levelLabel = event.level ? `<span style="color:${getLevelColor(event.level)}">[${event.level}]</span>` : '';

        const typeLabel = `<span style="color:#90a4ae">(${event.type})</span>`;



        let message = '';

        if (event.type === 'log') {

            message = event.message || '';

        } else if (event.type === 'job_status') {

            message = `status=${event.status || ''}${event.detail ? ` (${event.detail})` : ''}`;

        } else if (event.type === 'metric') {

            message = `${event.name}: ${event.value}`;

        }



        div.innerHTML = `<span style="color:#666">[${timeText}]</span> ${typeLabel} ${nidLabel} ${jobLabel} ${levelLabel} ${message}`;

        term.appendChild(div);

    });



    term.scrollTop = term.scrollHeight;

}



function isSoundAlertsEnabled() {
    const val = localStorage.getItem('mahanbot_sound_alerts');
    if (val !== null) return val === 'true';
    const toggle = document.getElementById('soundAlertsToggle');
    return toggle ? toggle.checked : true;
}

function toggleSoundAlerts(enabled) {
    localStorage.setItem('mahanbot_sound_alerts', String(enabled));
    if (enabled) {
        playAlertSound('quota');
        showMahanToast('هشدارهای صوتی فعال شد', 'آلارم هنگام یافتن سهمیه، دریافت پیامک و ثبت نهایی پخش خواهد شد.', 'info', { icon: 'fa-volume-high', delay: 3000 });
    }
}

function playAlertSound(type = 'quota') {
    if (!isSoundAlertsEnabled()) return;

    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        if (ctx.state === 'suspended') {
            ctx.resume();
        }

        const now = ctx.currentTime;

        // Frequencies for each sound mode
        // quota: C5 -> E5 -> G5 (Ascending C Major chord arpeggio)
        // action: A4 -> A5 (Double ping)
        // success: C5 -> E5 -> G5 -> C6 (Triumphant fanfare)
        // warning: E4 -> C4 (Gentle descending reminder)
        let tones = [
            { f: 523.25, start: 0, dur: 0.12 },
            { f: 659.25, start: 0.10, dur: 0.12 },
            { f: 783.99, start: 0.20, dur: 0.25 },
        ];

        if (type === 'action') {
            tones = [
                { f: 587.33, start: 0, dur: 0.10 },
                { f: 880.00, start: 0.12, dur: 0.22 },
            ];
        } else if (type === 'success') {
            tones = [
                { f: 523.25, start: 0, dur: 0.10 },
                { f: 659.25, start: 0.08, dur: 0.10 },
                { f: 783.99, start: 0.16, dur: 0.12 },
                { f: 1046.50, start: 0.26, dur: 0.35 },
            ];
        } else if (type === 'warning') {
            tones = [
                { f: 349.23, start: 0, dur: 0.15 },
                { f: 261.63, start: 0.13, dur: 0.25 },
            ];
        }

        const masterGain = ctx.createGain();
        masterGain.gain.setValueAtTime(0.25, now);
        masterGain.connect(ctx.destination);

        let maxEnd = 0;
        tones.forEach(t => {
            const osc = ctx.createOscillator();
            const toneGain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(t.f, now + t.start);

            // Envelope to prevent audible clicks
            toneGain.gain.setValueAtTime(0.0001, now + t.start);
            toneGain.gain.exponentialRampToValueAtTime(0.3, now + t.start + 0.02);
            toneGain.gain.exponentialRampToValueAtTime(0.0001, now + t.start + t.dur);

            osc.connect(toneGain);
            toneGain.connect(masterGain);

            osc.start(now + t.start);
            osc.stop(now + t.start + t.dur);
            if (t.start + t.dur > maxEnd) maxEnd = t.start + t.dur;
        });

        setTimeout(() => {
            try { ctx.close(); } catch (e) {}
        }, (maxEnd + 0.1) * 1000);

    } catch (e) {
        console.warn('Audio alert skipped:', e);
    }
}

function testAudioAlert(type = 'quota') {
    playAlertSound(type);
    const labels = {
        quota: 'آلارم سهمیه (یافتن بانک)',
        action: 'آلارم پیامک / اقدام دستی',
        success: 'آلارم ثبت موفق قطعی',
        warning: 'آلارم تکمیل ظرفیت',
    };
    showMahanToast(
        labels[type] || 'تست صدای آلارم',
        'صوت آزمایشی با موفقیت در مرورگر پخش شد.',
        type === 'success' ? 'success' : (type === 'warning' ? 'warning' : 'info'),
        { icon: 'fa-bell', delay: 3500 }
    );
}

function showMahanToast(title, message, type = 'info', options = {}) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const delay = options.delay || 6000;
    const icon = options.icon || (type === 'success' ? 'fa-circle-check' : (type === 'warning' ? 'fa-triangle-exclamation' : 'fa-circle-info'));
    
    // Theme palette mappings
    const bgMap = {
        success: 'linear-gradient(135deg, #ECFDF5 0%, #D1FAE5 100%)',
        warning: 'linear-gradient(135deg, #FFFBEB 0%, #FEF3C7 100%)',
        info: 'linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%)',
        danger: 'linear-gradient(135deg, #FFF1F2 0%, #FFE4E6 100%)',
    };
    const borderMap = {
        success: '#10B981',
        warning: '#F59E0B',
        info: '#3B82F6',
        danger: '#EF4444',
    };
    const colorMap = {
        success: '#065F46',
        warning: '#92400E',
        info: '#1E40AF',
        danger: '#991B1B',
    };

    const bg = bgMap[type] || bgMap.info;
    const border = borderMap[type] || borderMap.info;
    const textCol = colorMap[type] || colorMap.info;

    const toastEl = document.createElement('div');
    toastEl.className = 'toast border-0 mb-2 shadow-lg';
    toastEl.style.cssText = `background: ${bg}; border-right: 4px solid ${border} !important; border-radius: 12px; min-width: 300px; color: ${textCol}; backdrop-filter: blur(8px);`;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');

    toastEl.innerHTML = `
        <div class="d-flex align-items-center p-3">
            <div class="me-2" style="font-size: 1.35rem; color: ${border};">
                <i class="fa-solid ${icon}"></i>
            </div>
            <div class="toast-body p-0 flex-grow-1 ms-2">
                <div class="fw-bold mb-1" style="font-size: 0.92rem;">${title}</div>
                <div class="small opacity-75" style="line-height: 1.4;">${message}</div>
            </div>
            <button type="button" class="btn-close ms-2" data-bs-dismiss="toast" aria-label="بستن"></button>
        </div>
    `;

    container.appendChild(toastEl);
    if (window.bootstrap && bootstrap.Toast) {
        const toast = new bootstrap.Toast(toastEl, { delay: delay });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    } else {
        setTimeout(() => toastEl.remove(), delay);
    }
}

function showFavoriteToast(payload) {
    const bankName = payload.bank || payload.detail || 'بانک مورد نظر';
    const nidText = payload.nid ? ` (متقاضی: ${payload.nid})` : '';
    showMahanToast(
        'سهمیه بانک باز شد! 🎯',
        `گزینه «${bankName}» در دسترس قرار گرفت.${nidText}`,
        'warning',
        { icon: 'fa-crosshairs', delay: 8000 }
    );
}

async function downloadDiagnosticZip() {
    showMahanToast(
        'آماده‌سازی بسته عیب‌یابی...',
        'فایل‌های لاگ، وضعیت سیستم، اسکرین‌شات‌ها و صفحات در حال بسته‌بندی هستند.',
        'info',
        { icon: 'fa-file-zipper', delay: 4000 }
    );

    try {
        const url = `${httpBase}/diagnostic/export-zip?include_saved_pages=true`;
        const res = await fetch(url);
        if (!res.ok) {
            throw new Error(`خطای سرور: ${res.status}`);
        }
        const blob = await res.blob();
        const a = document.createElement('a');
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.href = window.URL.createObjectURL(blob);
        a.download = `mahanbot-diagnostic-${timestamp}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => window.URL.revokeObjectURL(a.href), 1000);

        showMahanToast(
            'دانلود با موفقیت آغاز شد ✅',
            'بسته زیپ عیب‌یابی شامل تمام لاگ‌ها، اسکرین‌شات‌ها و اطلاعات تشخیصی دریافت گردید.',
            'success',
            { icon: 'fa-circle-check', delay: 5000 }
        );
    } catch (err) {
        showMahanToast(
            'خطا در دانلود بسته تشخیصی ❌',
            err.message || 'امکان ارتباط با سرور وجود ندارد.',
            'danger',
            { icon: 'fa-triangle-exclamation', delay: 6000 }
        );
    }
}

function markDashboardStopped(nid) {

    const row = document.querySelector(`#activeBotsBody tr[data-nid="${nid}"]`);

    if (!row) return;

    const badge = row.querySelector('.badge');

    if (badge) {

        badge.className = `badge ${getStatusBadge('stopped')}`;

        badge.textContent = translateStatus('stopped');

    }

    const button = row.querySelector('button');

    if (button) {

        button.disabled = true;

        button.classList.add('disabled');

    }

}



function getLevelColor(level) {

    const palette = {

        success: '#059669',

        warning: '#d97706',

        error: '#e11d48',

        info: '#0d9488',

        stopped: '#e11d48',

        stopping: '#d97706',

        registering: '#0284c7',

        selecting: '#0d9488',

        'waiting sms': '#d97706'

    };

    return palette[level.toLowerCase()] || '#cfd8dc';
}

/* ═══════════════════════════════════════════════════════════════════════════
   MAHANBOT v1.8.0 Modern UI/UX Interactive Enhancements
   ═══════════════════════════════════════════════════════════════════════════ */

window.maskSensitiveData = true;

function toggleSensitiveMasking() {
    window.maskSensitiveData = !window.maskSensitiveData;
    const icon = document.getElementById('maskToggleIcon');
    const text = document.getElementById('maskToggleText');
    if (icon && text) {
        if (window.maskSensitiveData) {
            icon.className = 'fa-solid fa-eye-slash';
            text.textContent = 'پنهان‌سازی اطلاعات';
        } else {
            icon.className = 'fa-solid fa-eye';
            text.textContent = 'نمایش اطلاعات';
        }
    }
    refreshCurrentView();
    if (currentViewId === 'dashboard') {
        renderDashboard(allUsersData);
    }
}

function formatNid(nid) {
    if (!nid) return '-';
    const s = String(nid).trim();
    if (!window.maskSensitiveData || s.length < 6) return s;
    return `${s.slice(0, 2)}******${s.slice(-2)}`;
}

function formatMobile(m) {
    if (!m) return '-';
    const s = String(m).trim();
    if (!window.maskSensitiveData || s.length < 8) return s;
    return `${s.slice(0, 4)}***${s.slice(-4)}`;
}

function updateProcessStepper(activeUser) {
    const badge = document.getElementById('stepperApplicantBadge');
    const step1 = document.getElementById('stepper-step-1');
    const step2 = document.getElementById('stepper-step-2');
    const step3 = document.getElementById('stepper-step-3');
    const step4 = document.getElementById('stepper-step-4');
    const step5 = document.getElementById('stepper-step-5');
    const line1 = document.getElementById('stepper-line-1');
    const line2 = document.getElementById('stepper-line-2');
    const line3 = document.getElementById('stepper-line-3');
    const line4 = document.getElementById('stepper-line-4');

    if (!step1 || !step2 || !step3 || !step4 || !step5) return;

    const allSteps = [step1, step2, step3, step4, step5];
    const allLines = [line1, line2, line3, line4];

    allSteps.forEach(s => s.classList.remove('active', 'completed'));
    allLines.forEach(l => l && l.classList.remove('completed'));

    if (!activeUser) {
        if (badge) badge.innerHTML = '<i class="fa-solid fa-circle-dot ms-1 text-muted"></i> در انتظار پرونده فعال';
        return;
    }

    const name = activeUser.full_name || 'کاربر فعال';
    const status = String(activeUser.status || '').toLowerCase();
    if (badge) {
        badge.innerHTML = `<i class="fa-solid fa-bolt ms-1 text-primary"></i> ${name} (${translateStatus(activeUser.status)})`;
    }

    if (status.includes('success') || status.includes('کد رهگیری') || (activeUser.data && parseUserData(activeUser.data).tracking_code)) {
        allSteps.forEach(s => s.classList.add('completed'));
        allLines.forEach(l => l && l.classList.add('completed'));
    } else if (status.includes('bank') || status.includes('بانک') || status.includes('select') || status.includes('انتخاب') || status.includes('capacity')) {
        step1.classList.add('completed');
        step2.classList.add('completed');
        step3.classList.add('completed');
        step4.classList.add('active');
        if (line1) line1.classList.add('completed');
        if (line2) line2.classList.add('completed');
        if (line3) line3.classList.add('completed');
    } else if (status.includes('otp') || status.includes('sms') || status.includes('پیامک') || status.includes('wait')) {
        step1.classList.add('completed');
        step2.classList.add('completed');
        step3.classList.add('active');
        if (line1) line1.classList.add('completed');
        if (line2) line2.classList.add('completed');
    } else if (status.includes('register') || status.includes('captcha') || status.includes('کپچا') || status.includes('هویت')) {
        step1.classList.add('completed');
        step2.classList.add('active');
        if (line1) line1.classList.add('completed');
    } else {
        step1.classList.add('active');
    }
}

function selectVisualLoanType(type) {
    const mCard = document.getElementById('loanCardMarriage');
    const cCard = document.getElementById('loanCardChild');
    const sel = document.getElementById('inpLoanType');
    if (mCard && cCard) {
        if (type === 'marriage') {
            mCard.classList.add('active');
            cCard.classList.remove('active');
        } else {
            mCard.classList.remove('active');
            cCard.classList.add('active');
        }
    }
    if (sel && sel.value !== type) {
        sel.value = type;
        toggleLoanTypeFields();
    }
}

function syncVisualLoanCard() {
    const sel = document.getElementById('inpLoanType');
    if (sel) selectVisualLoanType(sel.value);
}

function selectVisualBankMode(mode) {
    const oCard = document.getElementById('bankModeCardOnline');
    const pCard = document.getElementById('bankModeCardInPerson');
    const sel = document.getElementById('inpBankMode');
    if (oCard && pCard) {
        if (mode === 'online') {
            oCard.classList.add('active');
            pCard.classList.remove('active');
        } else {
            oCard.classList.remove('active');
            pCard.classList.add('active');
        }
    }
    if (sel && sel.value !== mode) {
        sel.value = mode;
        toggleBranchFieldByMode();
    }
}

function syncVisualBankMode() {
    const sel = document.getElementById('inpBankMode');
    if (sel) selectVisualBankMode(sel.value || 'online');
}

let confettiAnimationId = null;
function triggerCelebrationConfetti() {
    const canvas = document.getElementById('confettiCanvas');
    if (!canvas) return;
    const soundEnabled = document.getElementById('confettiSoundToggle')?.checked !== false;
    if (soundEnabled) {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'triangle';
            osc.frequency.setValueAtTime(523.25, ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(659.25, ctx.currentTime + 0.15);
            osc.frequency.exponentialRampToValueAtTime(783.99, ctx.currentTime + 0.3);
            gain.gain.setValueAtTime(0.12, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.6);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.65);
        } catch (e) {}
    }

    canvas.style.display = 'block';
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const colors = ['#8B5CF6', '#F472B6', '#FFB38A', '#60A5FA', '#34D399', '#F59E0B', '#FDE047'];
    const particles = [];
    for (let i = 0; i < 110; i++) {
        particles.push({
            x: canvas.width * 0.5 + (Math.random() - 0.5) * 240,
            y: canvas.height * 0.4 + (Math.random() - 0.5) * 120,
            vx: (Math.random() - 0.5) * 14,
            vy: Math.random() * -12 - 4,
            size: Math.random() * 8 + 4,
            color: colors[Math.floor(Math.random() * colors.length)],
            rotation: Math.random() * 360,
            vRot: (Math.random() - 0.5) * 10,
            opacity: 1
        });
    }

    let start = Date.now();
    if (confettiAnimationId) cancelAnimationFrame(confettiAnimationId);

    function renderConfetti() {
        const elapsed = Date.now() - start;
        if (elapsed > 4000) {
            canvas.style.display = 'none';
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            return;
        }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const fade = elapsed > 2800 ? (4000 - elapsed) / 1200 : 1;

        particles.forEach(p => {
            p.x += p.vx;
            p.y += p.vy;
            p.vy += 0.35;
            p.rotation += p.vRot;
            ctx.save();
            ctx.translate(p.x, p.y);
            ctx.rotate((p.rotation * Math.PI) / 180);
            ctx.globalAlpha = Math.max(0, p.opacity * fade);
            ctx.fillStyle = p.color;
            ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.7);
            ctx.restore();
        });

        confettiAnimationId = requestAnimationFrame(renderConfetti);
    }
    confettiAnimationId = requestAnimationFrame(renderConfetti);
}

function switchDashboardScheme(schemeKey) {
    if (!schemeKey) schemeKey = 'scheme-c';
    document.documentElement.dataset.theme = schemeKey;
    document.querySelectorAll('.scheme-pill').forEach(pill => pill.classList.remove('active'));
    const targetPill = document.getElementById(`pill-${schemeKey}`);
    if (targetPill) targetPill.classList.add('active');
    const sel = document.getElementById('themeColorSelect');
    if (sel && sel.value !== schemeKey) sel.value = schemeKey;
    try { localStorage.setItem('mahan_theme', schemeKey); } catch (e) {}
    showToast(`طرح بصری «${getSchemeDisplayName(schemeKey)}» فعال شد.`, 'success');
}

function getSchemeDisplayName(schemeKey) {
    const map = {
        'scheme-c': 'طرح C (پیشنهادی) — ماهان سلطنتی',
        'scheme-a': 'طرح A — فیروزه‌ای مرواریدی و طلایی',
        'scheme-b': 'طرح B — رز لوکس و فیروزه‌ای',
        'default': 'طرح C (پیشنهادی) — ماهان سلطنتی'
    };
    return map[schemeKey] || schemeKey;
}

function applyThemePreset(preset) {
    if (!preset || preset === 'default') preset = 'scheme-c';
    document.documentElement.dataset.theme = preset;
    document.querySelectorAll('.scheme-pill').forEach(pill => pill.classList.remove('active'));
    const targetPill = document.getElementById(`pill-${preset}`);
    if (targetPill) targetPill.classList.add('active');
    const sel = document.getElementById('themeColorSelect');
    if (sel && sel.value !== preset) sel.value = preset;
    try { localStorage.setItem('mahan_theme', preset); } catch (e) {}
}

function toggleReducedMotion(enabled) {
    if (enabled) {
        document.body.classList.add('reduced-motion');
    } else {
        document.body.classList.remove('reduced-motion');
    }
    const chk = document.getElementById('reducedMotionToggle');
    if (chk && chk.checked !== enabled) chk.checked = enabled;
    try { localStorage.setItem('mahan_reduced_motion', enabled ? 'true' : 'false'); } catch (e) {}
}

function applyFontSize(size) {
    const map = { normal: '13.5px', medium: '15px', large: '16.5px' };
    document.body.style.fontSize = map[size] || '13.5px';
    const sel = document.getElementById('fontSizeSelect');
    if (sel && sel.value !== size) sel.value = size;
    try { localStorage.setItem('mahan_font_size', size); } catch (e) {}
}

function loadAppearanceSettings() {
    try {
        const theme = localStorage.getItem('mahan_theme') || 'scheme-c';
        applyThemePreset(theme);
        const reduced = localStorage.getItem('mahan_reduced_motion') === 'true';
        toggleReducedMotion(reduced);
        const size = localStorage.getItem('mahan_font_size') || 'normal';
        applyFontSize(size);
    } catch (e) {}
}

function filterApplicantsTable(viewType) {
    if (viewType === 'list') {
        const input = document.getElementById('listSearchInput');
        const query = (input?.value || '').trim().toLowerCase();
        const rows = document.querySelectorAll('#applicantsListBody tr');
        rows.forEach(r => {
            const text = r.textContent.toLowerCase();
            r.style.display = (!query || text.includes(query)) ? '' : 'none';
        });
    } else if (viewType === 'bank-select') {
        const input = document.getElementById('bankSelectSearchInput');
        const query = (input?.value || '').trim().toLowerCase();
        const cards = document.querySelectorAll('#standbyApplicantsContainer .standby-card, #activeSnipersContainer .standby-card, #activeSnipersContainer .active-sniper-card, #activeSnipersContainer .sniper-card');
        cards.forEach(c => {
            const text = c.textContent.toLowerCase();
            c.style.display = (!query || text.includes(query)) ? '' : 'none';
        });
        const rows = document.querySelectorAll('#bankSelectListBody tr');
        rows.forEach(r => {
            const text = r.textContent.toLowerCase();
            r.style.display = (!query || text.includes(query)) ? '' : 'none';
        });
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
   MAHANBOT v1.8.0 Saved Pages & Bank Recovery Management
   ═══════════════════════════════════════════════════════════════════════════ */

let allSavedPages = [];
let filteredSavedPages = [];
let capacityCountdownTimer = null;
let capacitySecondsLeft = 20;
let capacityCheckInterval = 20;
let currentHealthFolder = '';

async function loadSavedPages() {
    const tbody = document.getElementById('savedPagesListBody');
    if (!allSavedPages.length && tbody) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted"><i class="fa-solid fa-spinner fa-spin ms-2"></i> در حال بارگذاری اطلاعات صفحات ذخیره‌شده...</td></tr>';
    }
    try {
        const res = await apiCall('/saved-pages', 'GET');
        if (res && res.status === 'ok') {
            capacityCheckInterval = Math.max(5, Number(res.capacity_check_interval) || 20);
            const checkLabel = document.getElementById('capacityCheckLabel');
            if (checkLabel) checkLabel.textContent = res.check_method === 'native_submit_retry' ? 'شکار سهمیه: فاصله ارسال درخواست غیرحضوری' : 'بازخوانی وضعیت نشست';
            capacitySecondsLeft = Math.min(capacitySecondsLeft, capacityCheckInterval);
            allSavedPages = res.saved_pages || [];
            filteredSavedPages = [...allSavedPages];

            // Update stats
            const statTot = document.getElementById('savedStatTotal');
            const statRes = document.getElementById('savedStatResumable');
            const statWait = document.getElementById('savedStatWaiting');
            const statRep = document.getElementById('savedStatNeedsRepair');
            if (statTot) statTot.textContent = (res.total_saved || 0).toLocaleString('fa-IR');
            if (statRes) statRes.textContent = (res.resumable_count || 0).toLocaleString('fa-IR');
            if (statWait) statWait.textContent = (res.waiting_capacity_count || 0).toLocaleString('fa-IR');
            if (statRep) statRep.textContent = (res.needs_repair_count || 0).toLocaleString('fa-IR');

            filterSavedPages();
        }
    } catch (err) {
        console.error('loadSavedPages error:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-danger"><i class="fa-solid fa-triangle-exclamation ms-2"></i> خطا در دریافت اطلاعات صفحات ذخیره‌شده: ${escapeHtml(err.message || String(err))}</td></tr>`;
        }
    }
}

function filterSavedPages() {
    const q = (document.getElementById('savedPagesSearch')?.value || '').trim().toLowerCase();
    const filter = document.getElementById('savedPagesFilter')?.value || 'all';

    filteredSavedPages = allSavedPages.filter(p => {
        // Text search
        const nameMatch = (p.full_name || '').toLowerCase().includes(q);
        const nidMatch = (p.nid || '').toLowerCase().includes(q);
        const folderMatch = (p.folder || '').toLowerCase().includes(q);
        const textOk = !q || nameMatch || nidMatch || folderMatch;

        if (!textOk) return false;

        if (filter === 'all') return true;
        if (filter === 'resumable') return p.can_resume && p.state !== 'CAPACITY_FULL';
        if (filter === 'capacity') return p.state === 'CAPACITY_FULL';
        if (filter === 'confirmed') return p.state === 'REGISTRATION_CONFIRMED' || !!p.tracking_code;
        if (filter === 'repair') return p.state === 'NEEDS_REPAIR' || !p.manifest_valid;
        return true;
    });

    const badge = document.getElementById('savedPagesCountBadge');
    if (badge) badge.textContent = `${filteredSavedPages.length.toLocaleString('fa-IR')} مورد`;

    renderSavedPagesTable(filteredSavedPages);
}

function renderSavedPagesTable(pages) {
    const tbody = document.getElementById('savedPagesListBody');
    if (!tbody) return;

    if (!pages || !pages.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted"><i class="fa-regular fa-folder-open fa-2x d-block mb-2 text-secondary"></i> موردی برای نمایش یافت نشد.</td></tr>';
        return;
    }

    tbody.innerHTML = pages.map(p => {
        const maskedNid = formatNid(p.nid);
        const bankName = (typeof p.selected_bank === 'object' && p.selected_bank ? (p.selected_bank.bank_name || p.selected_bank.text) : p.selected_bank) || 'تعیین نشده';
        const modeBadge = p.selected_mode === 'online' ? '<span class="badge badge-purple me-1">غیرحضوری</span>' : (p.selected_mode === 'in_person' ? '<span class="badge bg-secondary me-1">حضوری</span>' : '');
        const reserveBadge = p.is_reserve ? '<span class="badge badge-peach me-1">رزرو</span>' : '';
        const dateStr = p.last_updated ? new Date(p.last_updated * 1000).toLocaleDateString('fa-IR', { hour: '2-digit', minute: '2-digit' }) : '-';
        const errText = p.error_message ? `<span class="text-danger small d-block text-truncate" style="max-width:200px" title="${escapeHtml(p.error_message)}"><i class="fa-solid fa-triangle-exclamation ms-1"></i>${escapeHtml(p.error_message)}</span>` : '<span class="text-muted small">عادی</span>';

        const viewBtn = p.latest_html ? `<button class="btn btn-sm btn-outline-primary me-1" title="مشاهده اسنپ‌شات" onclick="viewSavedPage('${escapeHtml(p.folder)}', '${escapeHtml(p.latest_html || '')}', '${escapeHtml(p.latest_png || '')}')"><i class="fa-solid fa-eye"></i></button>` : '';
        const healthBtn = `<button class="btn btn-sm btn-outline-info me-1" title="بررسی سلامت" onclick="healthCheckSavedPage('${escapeHtml(p.folder)}')"><i class="fa-solid fa-heart-pulse"></i></button>`;
        const repairBtn = `<button class="btn btn-sm btn-outline-warning me-1" title="تعمیر Checkpoint و مانیفست" onclick="repairSavedPageSingle('${escapeHtml(p.folder)}')"><i class="fa-solid fa-screwdriver-wrench"></i></button>`;
        const resumeBtn = p.can_resume && p.nid ? `<button class="btn btn-sm btn-success" title="ادامه عملیات بات" onclick="resumeSavedPage('${escapeHtml(p.nid)}')"><i class="fa-solid fa-play"></i></button>` : `<button class="btn btn-sm btn-light text-muted" disabled title="غیرقابل ادامه"><i class="fa-solid fa-play"></i></button>`;
        const reauthBtn = p.nid && p.requires_reauthentication ? `<button class="btn btn-sm btn-outline-danger" title="ورود مجدد و بازیابی تنظیمات" onclick="reauthenticateSavedPage('${escapeHtml(p.nid)}')"><i class="fa-solid fa-right-to-bracket"></i></button>` : '';
        const pauseBtn = p.live_session?.live ? `<button class="btn btn-sm btn-outline-secondary" title="توقف پایش و حفظ نشست" onclick="pauseSavedPage('${escapeHtml(p.nid)}')"><i class="fa-solid fa-pause"></i></button>` : '';
        const logBtn = p.nid ? `<button class="btn btn-sm btn-outline-info" title="مشاهده لاگ" onclick="showSavedPageLogs('${escapeHtml(p.nid)}')"><i class="fa-solid fa-list"></i></button>` : '';

        return `<tr>
            <td>
                <div class="fw-bold">${escapeHtml(p.full_name || p.folder)}</div>
                <small class="text-muted">${escapeHtml(p.folder)}</small>
            </td>
            <td><code class="text-primary">${escapeHtml(maskedNid)}</code></td>
            <td><span class="small">${escapeHtml(p.province || '-')} / ${escapeHtml(p.city || '-')}</span></td>
            <td>
                <span class="badge bg-light text-dark">${p.snapshot_count} فایل</span>
                <small class="d-block text-muted" style="font-size:11px">${dateStr}</small>
            </td>
            <td>
                <div>${modeBadge}${reserveBadge}<strong>${escapeHtml(bankName)}</strong></div>
            </td>
            <td>
                <span class="badge ${p.badge_class || 'bg-secondary'}" style="${p.badge_color ? 'color:' + p.badge_color : ''}">${escapeHtml(p.status_fa || p.state)}</span>
            </td>
            <td>${errText}</td>
            <td class="text-nowrap">
                ${viewBtn}
                ${healthBtn}
                ${repairBtn}
                ${resumeBtn}
                ${pauseBtn}
                ${reauthBtn}
                ${logBtn}
            </td>
        </tr>`;
    }).join('');
}

async function viewSavedPage(folder, htmlFile, pngFile) {
    const modalEl = document.getElementById('savedPageModal');
    if (!modalEl) return;

    const applicantEl = document.getElementById('spModalApplicant');
    const badgeEl = document.getElementById('spModalBadge');
    const subEl = document.getElementById('spModalSub');
    const iframe = document.getElementById('spHtmlFrame');
    const img = document.getElementById('spPngImg');
    const noPng = document.getElementById('spNoPng');
    const directLink = document.getElementById('spDirectLink');

    const pageObj = allSavedPages.find(p => p.folder === folder) || {};
    if (applicantEl) applicantEl.textContent = pageObj.full_name || folder;
    if (badgeEl) {
        badgeEl.className = `badge ms-2 ${pageObj.badge_class || 'bg-secondary'}`;
        badgeEl.textContent = pageObj.status_fa || pageObj.state || 'نامشخص';
    }
    if (subEl) subEl.textContent = `پوشه: ${folder} | کدملی: ${formatNid(pageObj.nid)} | آخرین اسنپ‌شات: ${htmlFile || 'ندارد'}`;

    if (iframe) {
        if (htmlFile) {
            iframe.src = `/saved-pages/${encodeURIComponent(folder)}/snapshot/${encodeURIComponent(htmlFile)}`;
        } else {
            iframe.src = 'about:blank';
        }
    }

    if (directLink && htmlFile) {
        directLink.href = `/saved-pages/${encodeURIComponent(folder)}/snapshot/${encodeURIComponent(htmlFile)}`;
        directLink.style.display = 'inline-flex';
    } else if (directLink) {
        directLink.style.display = 'none';
    }

    if (img && noPng) {
        if (pngFile) {
            img.src = `/saved-pages/${encodeURIComponent(folder)}/snapshot/${encodeURIComponent(pngFile)}`;
            img.style.display = 'inline-block';
            noPng.style.display = 'none';
        } else {
            img.style.display = 'none';
            noPng.style.display = 'block';
        }
    }

    // Trigger analysis tab fetch
    fetchSavedPageAnalysis(folder);

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
}

async function fetchSavedPageAnalysis(folder) {
    const loading = document.getElementById('spAnalysisLoading');
    const content = document.getElementById('spAnalysisContent');
    if (loading) loading.style.display = 'block';
    if (content) content.style.display = 'none';

    try {
        const res = await apiCall(`/saved-pages/${encodeURIComponent(folder)}/analysis`, 'GET');
        if (res && res.status === 'ok') {
            const an = res.analysis || {};
            const anApp = document.getElementById('spAnApplicant');
            const anLoc = document.getElementById('spAnLocation');
            const anBank = document.getElementById('spAnBank');
            const anMode = document.getElementById('spAnMode');
            const alertBox = document.getElementById('spAnAlertBox');
            const alertText = document.getElementById('spAnAlertText');
            const banksTable = document.getElementById('spAnBanksTable');

            if (anApp) anApp.textContent = `${an.applicant_name || '-'} (${formatNid(an.applicant_nid)})`;
            if (anLoc) anLoc.textContent = `${an.province || '-'} / ${an.city || '-'}`;
            if (anBank) anBank.textContent = an.selected_bank ? (an.selected_bank.bank_name || an.selected_bank.text) : 'انتخاب نشده';
            if (anMode) anMode.textContent = an.selected_mode === 'online' ? 'غیرحضوری (آنلاین)' : (an.selected_mode === 'in_person' ? 'حضوری' : 'پیش‌فرض');

            if (an.error_message && alertBox && alertText) {
                alertBox.style.display = 'block';
                alertText.textContent = an.error_message;
            } else if (alertBox) {
                alertBox.style.display = 'none';
            }

            if (banksTable) {
                const banks = an.available_banks || [];
                if (!banks.length) {
                    banksTable.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-2">هیچ گزینه‌ای در dropdown یافت نشد.</td></tr>';
                } else {
                    banksTable.innerHTML = banks.map(b => {
                        const capBadge = b.is_reserve ? '<span class="badge badge-peach">رزرو / در انتظار ظرفیت</span>' : '<span class="badge badge-success">دارای ظرفیت ثبت‌نام</span>';
                        const selMarker = b.is_selected ? '<span class="badge bg-primary ms-1">انتخاب‌شده</span>' : '';
                        return `<tr>
                            <td><code>${escapeHtml(b.value)}</code></td>
                            <td><strong>${escapeHtml(b.bank_name)}</strong> ${selMarker}</td>
                            <td>${capBadge}</td>
                            <td>${b.queue_count !== null && b.queue_count !== undefined ? b.queue_count.toLocaleString('fa-IR') + ' نفر' : '-'}</td>
                            <td><small class="text-muted">${b.is_reserve ? 'رزرو' : 'عادی'}</small></td>
                        </tr>`;
                    }).join('');
                }
            }

            if (loading) loading.style.display = 'none';
            if (content) content.style.display = 'block';
        }
    } catch (e) {
        if (loading) loading.innerHTML = `<span class="text-danger">خطا در بارگذاری تحلیل: ${escapeHtml(e.message || String(e))}</span>`;
    }
}

async function healthCheckSavedPage(folder) {
    currentHealthFolder = folder;
    const modalEl = document.getElementById('healthCheckModal');
    if (!modalEl) return;

    const loading = document.getElementById('hcLoading');
    const content = document.getElementById('hcContent');
    const alertBox = document.getElementById('hcStatusAlert');
    const manifestEl = document.getElementById('hcManifestStatus');
    const filesEl = document.getElementById('hcTotalFiles');
    const snapsEl = document.getElementById('hcSnapshotsRecorded');
    const cpEl = document.getElementById('hcDbCheckpoint');
    const issuesBox = document.getElementById('hcIssuesBox');
    const issuesList = document.getElementById('hcIssuesList');

    if (loading) loading.style.display = 'block';
    if (content) content.style.display = 'none';

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();

    try {
        const res = await apiCall(`/saved-pages/${encodeURIComponent(folder)}/health-check`, 'POST');
        if (res && res.status === 'ok') {
            if (alertBox) {
                alertBox.className = `alert mb-3 ${res.healthy ? 'alert-success' : 'alert-warning'}`;
                alertBox.innerHTML = res.healthy ?
                    '<i class="fa-solid fa-circle-check ms-2"></i> ساختار این پرونده، مانیفست و اسنپ‌شات‌ها کاملاً سالم و همگام هستند.' :
                    '<i class="fa-solid fa-triangle-exclamation ms-2"></i> برخی ناهماهنگی‌ها در این پرونده شناسایی شد که با دکمه ترمیم قابل اصلاح است.';
            }

            if (manifestEl) manifestEl.textContent = res.manifest_status === 'ok' ? 'سالم و معتبر' : (res.manifest_status === 'missing' ? 'ناموجود' : 'خراب');
            if (filesEl) filesEl.textContent = (res.total_files || 0).toLocaleString('fa-IR');
            if (snapsEl) snapsEl.textContent = (res.snapshots_recorded || 0).toLocaleString('fa-IR');
            if (cpEl) cpEl.textContent = res.has_db_checkpoint ? 'موجود و فعال' : 'ناموجود';

            const issues = res.issues || [];
            if (issues.length && issuesBox && issuesList) {
                issuesBox.style.display = 'block';
                issuesList.innerHTML = issues.map(iss => `<li>${escapeHtml(iss)}</li>`).join('');
            } else if (issuesBox) {
                issuesBox.style.display = 'none';
            }

            if (loading) loading.style.display = 'none';
            if (content) content.style.display = 'block';
        }
    } catch (e) {
        if (loading) loading.innerHTML = `<span class="text-danger">خطا در بررسی سلامت: ${escapeHtml(e.message || String(e))}</span>`;
    }
}

async function repairCurrentHealthFolder() {
    if (!currentHealthFolder) return;
    await repairSavedPageSingle(currentHealthFolder);
    healthCheckSavedPage(currentHealthFolder);
}

async function repairSavedPageSingle(folder) {
    try {
        const res = await apiCall(`/saved-pages/${encodeURIComponent(folder)}/repair`, 'POST');
        showToast('ترمیم مانیفست انجام شد', 'success');
        await loadSavedPages();
    } catch (e) {
        showToast(`خطا در ترمیم: ${e.message || String(e)}`, 'error');
    }
}

async function repairAllSavedPages() {
    try {
        const res = await apiCall('/saved-pages/repair-all', 'POST');
        const msg = `ترمیم همگانی انجام شد. پوشه‌ها: ${res.folders || 0}، مانیفست‌های ترمیم‌شده: ${res.manifests_repaired || 0}، فایل‌های بازیابی‌شده: ${res.orphan_files_added || 0}`;
        showToast(msg, 'success');
        await loadSavedPages();
    } catch (e) {
        showToast(`خطا در ترمیم همگانی: ${e.message || String(e)}`, 'error');
    }
}

async function resumeSavedPage(nid) {
    if (!nid) return;
    const ok = confirm(`آیا مایل به ادامه عملیات ربات برای متقاضی با کدملی ${formatNid(nid)} از آخرین چک‌پوینت ذخیره‌شده هستید؟`);
    if (!ok) return;

    try {
        const res = await apiCall(`/saved-pages/resume/${encodeURIComponent(nid)}`, 'POST');
        showToast(res.message || 'ربات با موفقیت ادامه یافت.', 'success');
        setUserStatus(nid, 'Running');
        switchView('dashboard');
    } catch (e) {
        showToast(e.message || 'خطا در ادامه عملیات', 'error');
    }
}

async function pauseSavedPage(nid) {
    try {
        const res = await apiCall(`/saved-pages/pause/${encodeURIComponent(nid)}`, 'POST');
        showToast(res.message, 'info');
        await loadSavedPages();
    } catch (e) { showToast(e.message || 'خطا در توقف پایش', 'error'); }
}

async function reauthenticateSavedPage(nid) {
    if (!confirm('ورود مجدد ممکن است به پیامک و کپچای جدید نیاز داشته باشد. تنظیمات متقاضی حفظ می‌شوند. ادامه می‌دهید؟')) return;
    try {
        const res = await apiCall(`/saved-pages/reauthenticate/${encodeURIComponent(nid)}`, 'POST');
        showToast(res.message, 'info');
        await loadSavedPages();
    } catch (e) { showToast(e.message || 'خطا در ورود مجدد', 'error'); }
}

async function showSavedPageLogs(nid) {
    try {
        const res = await apiCall(`/saved-pages/logs/${encodeURIComponent(nid)}`, 'GET');
        const lines = (res.events || []).map(e => `${e.timestamp || ''} ${e.event_code || e.type || ''}: ${e.message || ''}`);
        const win = window.open('about:blank', '_blank');
        if (win) { const pre = win.document.createElement('pre'); pre.textContent = lines.join('\n') || 'هنوز لاگی ثبت نشده است'; win.document.body.appendChild(pre); }
    } catch (e) { showToast(e.message || 'خطا در دریافت لاگ', 'error'); }
}

function startCapacityCountdown() {
    stopCapacityCountdown();
    capacitySecondsLeft = capacityCheckInterval;
    updateCapacityCountdownDisplay();
    capacityCountdownTimer = setInterval(() => {
        capacitySecondsLeft--;
        if (capacitySecondsLeft <= 0) {
            capacitySecondsLeft = capacityCheckInterval;
            if (currentViewId === 'saved-pages') {
                loadSavedPages();
            }
        }
        updateCapacityCountdownDisplay();
    }, 1000);
}

function stopCapacityCountdown() {
    if (capacityCountdownTimer) {
        clearInterval(capacityCountdownTimer);
        capacityCountdownTimer = null;
    }
}

function updateCapacityCountdownDisplay() {
    const el = document.getElementById('capacityCountdownVal');
    if (el) el.textContent = capacitySecondsLeft;
}

/* ══════════════════════════════════════════════════════════════════════════
   AUTOMATED SMS & GSM DONGLE CONTROLLER (Phase 1)
   ══════════════════════════════════════════════════════════════════════════ */

function initSmsGsmSettings() {
    const webhookInput = document.getElementById('smsWebhookUrlInput');
    if (webhookInput) {
        webhookInput.value = `${window.location.origin}/api/sms/webhook`;
    }
    scanGsmPorts();
    checkGsmStatus();
}

function copyWebhookUrl() {
    const input = document.getElementById('smsWebhookUrlInput');
    if (!input) return;
    const url = input.value || `${window.location.origin}/api/sms/webhook`;
    navigator.clipboard.writeText(url).then(() => {
        showToast('آدرس وب‌هوک در کلیپ‌بورد کپی شد.', 'success');
    }).catch(() => {
        input.select();
        document.execCommand('copy');
        showToast('آدرس وب‌هوک کپی شد.', 'success');
    });
}

async function scanGsmPorts() {
    const select = document.getElementById('gsmPortSelect');
    if (!select) return;
    try {
        const res = await apiCall('/api/gsm/ports');
        const currentVal = select.value;
        select.innerHTML = '';
        if (res.ports && res.ports.length > 0) {
            res.ports.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.device;
                opt.textContent = `${p.device} (${p.description || 'پورت سریال'})`;
                if (p.device === currentVal || p.device === 'COM3') {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
        } else {
            const opt = document.createElement('option');
            opt.value = 'COM3';
            opt.textContent = 'COM3 (پیش‌فرض - هیچ پورتی یافت نشد)';
            select.appendChild(opt);
        }
    } catch (e) {
        console.warn('Failed to scan GSM ports:', e);
    }
}

async function checkGsmStatus() {
    const textEl = document.getElementById('gsmStatusText');
    const toggleEl = document.getElementById('set_gsm_modem');
    if (!textEl) return;
    try {
        const res = await apiCall('/api/gsm/status');
        if (res.running) {
            textEl.innerHTML = `<span class="text-success"><i class="fa-solid fa-circle-check ms-1"></i> فعال روی پورت ${res.port || ''} (SMS دریافتی: ${res.messages_received || 0})</span>`;
            if (toggleEl) toggleEl.checked = true;
        } else {
            textEl.innerHTML = `<span class="text-muted"><i class="fa-solid fa-circle-xmark ms-1"></i> غیرفعال${res.last_error ? ` (${res.last_error})` : ''}</span>`;
            if (toggleEl) toggleEl.checked = false;
        }
    } catch (e) {
        textEl.textContent = 'وضعیت: در دسترس نیست';
    }
}

async function toggleGsmService() {
    const toggleEl = document.getElementById('set_gsm_modem');
    const portEl = document.getElementById('gsmPortSelect');
    const baudEl = document.getElementById('gsmBaudSelect');
    const enabled = toggleEl ? toggleEl.checked : false;
    const port = portEl ? portEl.value : 'COM3';
    const baudrate = baudEl ? parseInt(baudEl.value, 10) : 115200;

    try {
        const res = await apiCall('/api/gsm/toggle', 'POST', {
            enabled: enabled,
            port: port,
            baudrate: baudrate
        });
        showToast(res.message || (enabled ? 'سرویس دانگل فعال شد' : 'سرویس دانگل متوقف شد'), 'info');
        checkGsmStatus();
    } catch (e) {
        showToast(e.message || 'خطا در تغییر وضعیت دانگل', 'error');
        if (toggleEl) toggleEl.checked = !enabled;
    }
}

async function testParseSmsText() {
    const textInput = document.getElementById('testSmsInput');
    const resultBox = document.getElementById('smsTestResult');
    if (!textInput || !resultBox) return;

    const rawText = textInput.value.trim();
    if (!rawText) {
        showToast('لطفاً متن پیامک را وارد کنید.', 'warning');
        return;
    }

    try {
        const res = await apiCall('/api/sms/test-parse', 'POST', { text: rawText });
        resultBox.style.display = 'block';
        if (res.status === 'success') {
            const parsed = res.parsed || {};
            resultBox.innerHTML = `
                <div class="text-success fw-bold mb-1"><i class="fa-solid fa-check-circle ms-1"></i> استخراج موفقیت‌آمیز:</div>
                <div class="d-flex flex-wrap gap-3">
                    <span><strong>کد OTP:</strong> <code class="fs-6">${parsed.otp_code || '---'}</code></span>
                    <span><strong>کد ملی:</strong> <code>${parsed.national_id || '---'}</code></span>
                    <span><strong>موبایل:</strong> <code>${parsed.mobile || '---'}</code></span>
                    <span><strong>تطبیق با متقاضی فعال:</strong> <span class="badge ${res.matched_waiter ? 'bg-success' : 'bg-secondary'}">${res.matched_waiter ? 'بله (' + res.matched_waiter + ')' : 'خیر'}</span></span>
                </div>
            `;
        } else {
            resultBox.innerHTML = `<div class="text-danger"><i class="fa-solid fa-triangle-exclamation ms-1"></i> ${res.message || 'خطا در تجزیه'}</div>`;
        }
    } catch (e) {
        resultBox.style.display = 'block';
        resultBox.innerHTML = `<div class="text-danger"><i class="fa-solid fa-circle-exmark ms-1"></i> ${e.message || 'خطا در ارتباط با سرور'}</div>`;
    }
}

async function sendSimulatedSms() {
    const textInput = document.getElementById('testSmsInput');
    const resultBox = document.getElementById('smsTestResult');
    if (!textInput || !resultBox) return;

    const rawText = textInput.value.trim();
    if (!rawText) {
        showToast('لطفاً متن پیامک را وارد کنید.', 'warning');
        return;
    }

    try {
        const res = await fetch(`${httpBase}/api/sms/webhook`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: rawText })
        });
        const data = await res.json();
        resultBox.style.display = 'block';
        if (data.status === 'success' || data.success) {
            showToast('پیامک به وب‌هوک تزریق شد و کد اعمال گردید!', 'success');
            resultBox.innerHTML = `
                <div class="text-success fw-bold"><i class="fa-solid fa-circle-check ms-1"></i> ارسال واقعی موفق:</div>
                <div class="text-muted mt-1">${data.message || 'کد با موفقیت ثبت شد'} (کد ملی: ${data.national_id || '---'}, کد: ${data.code || '---'})</div>
            `;
        } else {
            showToast(data.message || 'عدم تطبیق با متقاضی منتظر پیامک', 'warning');
            resultBox.innerHTML = `
                <div class="text-warning fw-bold"><i class="fa-solid fa-triangle-exclamation ms-1"></i> نتیجه دریافت وب‌هوک:</div>
                <div class="text-muted mt-1">${data.message || 'هیچ متقاضی در انتظار این پیامک نبود'}</div>
            `;
        }
    } catch (e) {
        showToast(e.message || 'خطا در ارتباط با سرور', 'error');
    }
}




/* ══════════════════════════════════════════════════════════════════════
   DATABASE & APPLICANT CONFIGURATION HEALTH TOOLS
   ══════════════════════════════════════════════════════════════════════ */
async function checkDbHealth() {
    try {
        const badge = document.getElementById('dbHealthBadge');
        if (badge) { badge.className = 'badge bg-secondary px-3 py-2'; badge.textContent = 'در حال بررسی...'; }
        const res = await apiCall('/api/database/health', 'GET');
        const h = res.health || {};
        const isHealthy = h.is_healthy && (h.inconsistencies_count === 0);
        if (badge) {
            badge.className = isHealthy ? 'badge bg-success px-3 py-2' : 'badge bg-warning text-dark px-3 py-2';
            badge.textContent = isHealthy ? 'دیتابیس کاملاً سالم و یکپارچه' : `${h.inconsistencies_count} مورد نیازمند بررسی`;
        }
        const counts = h.table_counts || {};
        if (document.getElementById('dbMetricApplicants')) document.getElementById('dbMetricApplicants').textContent = counts.applicants ?? 0;
        if (document.getElementById('dbMetricLocations')) document.getElementById('dbMetricLocations').textContent = counts.applicant_locations ?? 0;
        if (document.getElementById('dbMetricPreferences')) document.getElementById('dbMetricPreferences').textContent = counts.applicant_bank_preferences ?? 0;
        if (document.getElementById('dbMetricInconsistencies')) document.getElementById('dbMetricInconsistencies').textContent = h.inconsistencies_count ?? 0;

        const box = document.getElementById('dbInconsistenciesBox');
        const list = document.getElementById('dbInconsistenciesList');
        if (box && list) {
            const incs = h.inconsistencies || [];
            if (incs.length > 0) {
                box.style.display = 'block';
                list.innerHTML = incs.map(it => `
                    <div class="d-flex justify-content-between align-items-center border-bottom py-1">
                        <div>
                            <strong>${escapeHtml(it.full_name || '')} (${escapeHtml(it.national_id || '')}):</strong>
                            <span class="ms-1">${escapeHtml(it.details || '')}</span>
                        </div>
                        <span class="badge bg-light text-dark border">${escapeHtml(it.suggested_action || '')}</span>
                    </div>
                `).join('');
            } else {
                box.style.display = 'none';
                list.innerHTML = '';
            }
        }
    } catch (e) {
        console.error('Error checking DB health:', e);
    }
}

async function backupDbNow() {
    try {
        const res = await apiCall('/api/database/backup', 'POST');
        showToast(res.message || 'پشتیبان با موفقیت ایجاد شد', 'success');
    } catch (e) {
        showToast(e.message || 'خطا در تهیه پشتیبان', 'error');
    }
}

async function repairDbNow() {
    if (!confirm('آیا مایلید عملیات یکپارچه‌سازی و تعمیر خودکار متقاضیان و دیتابیس اجرا شود؟ پیش از تغییر، فایل پشتیبان خودکار ایجاد خواهد شد.')) return;
    try {
        const res = await apiCall('/api/database/repair', 'POST', { dry_run: false });
        showToast(res.message || 'تعمیر با موفقیت انجام شد', 'success');
        await checkDbHealth();
    } catch (e) {
        showToast(e.message || 'خطا در اجرای تعمیر', 'error');
    }
}


// ===============================================================
// MAHANBOT 2.0 HELPER FUNCTIONS: GEO, COPY, STAGE 3, EDITION
// ===============================================================

async function onProvinceSelected(provinceName, targetCityVal = null) {
    const citySelect = document.getElementById('inpCity');
    if (!citySelect) return;
    
    if (!provinceName) {
        citySelect.innerHTML = '<option value="">-- ابتدا استان را انتخاب کنید --</option>';
        return;
    }
    
    citySelect.innerHTML = '<option value="">در حال دریافت لیست شهرهای ' + escapeHtml(provinceName) + '...</option>';
    try {
        const res = await fetch(`/api/geo/cities?province=${encodeURIComponent(provinceName)}`);
        if (!res.ok) throw new Error('خطا در بارگذاری شهرها');
        const cities = await res.json();
        
        let html = '<option value="">-- انتخاب شهر محل سکونت --</option>';
        let cityMatched = false;
        
        cities.forEach(c => {
            const cName = typeof c === 'string' ? c : c.name;
            const isSelected = targetCityVal && (cName === targetCityVal || cName.trim() === targetCityVal.trim());
            if (isSelected) cityMatched = true;
            html += `<option value="${escapeHtml(cName)}" ${isSelected ? 'selected' : ''}>${escapeHtml(cName)}</option>`;
        });
        
        // Preserve unlisted/custom city if previously saved
        if (targetCityVal && !cityMatched) {
            html += `<option value="${escapeHtml(targetCityVal)}" selected>${escapeHtml(targetCityVal)} (تنظیم‌شده)</option>`;
        }
        
        citySelect.innerHTML = html;
        if (targetCityVal) citySelect.value = targetCityVal;
    } catch (e) {
        console.warn('onProvinceSelected error:', e);
        citySelect.innerHTML = '<option value="">-- خطای اتصال به سرور --</option>';
        if (targetCityVal) {
            citySelect.innerHTML += `<option value="${escapeHtml(targetCityVal)}" selected>${escapeHtml(targetCityVal)}</option>`;
        }
    }
}

function copyToClipboard(text, btnEl) {
    if (!text) return;
    const doFeedback = () => {
        if (typeof showToast === 'function') {
            showToast('در کلیپ‌بورد کپی شد ✓', 'success');
        }
        if (btnEl) {
            const originalHtml = btnEl.innerHTML;
            btnEl.innerHTML = '<i class="fa-solid fa-check me-1"></i>کپی شد';
            btnEl.classList.add('btn-success');
            setTimeout(() => {
                btnEl.innerHTML = originalHtml;
                btnEl.classList.remove('btn-success');
            }, 2000);
        }
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(doFeedback).catch(() => {
            fallbackCopyText(text);
            doFeedback();
        });
    } else {
        fallbackCopyText(text);
        doFeedback();
    }
}

function fallbackCopyText(text) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
    } catch (e) {
        console.error('Fallback copy failed', e);
    }
    document.body.removeChild(textarea);
}

function updateStage3BankStatusTable(data) {
    const tbody = document.getElementById('stage3BankStatusBody');
    if (!tbody) return;
    const applicants = data || allUsersData || [];
    
    // Filter applicants that are either in stage 3, monitoring capacity, or have priority bank configured
    const stage3Applicants = applicants.filter(u => {
        const mon = (typeof capacityMonitorsData !== 'undefined' && capacityMonitorsData[u.national_id]) || {};
        const runtime = u.runtime || {};
        const st = String(u.status || runtime.status || '').toUpperCase();
        const stage = Number(u.stage || runtime.stage || 0);
        return stage === 3 || mon.state || st.includes('CAPACITY') || st.includes('BANK') || st.includes('SELECT') || runtime.browser_status === 'OPEN';
    });

    const displayList = stage3Applicants.length > 0 ? stage3Applicants : applicants.slice(0, 5);

    if (displayList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-3">در حال حاضر پرونده‌ای در مرحله سوم (انتخاب بانک) قرار ندارد.</td></tr>`;
        return;
    }

    tbody.innerHTML = displayList.map(user => {
        const nid = user.national_id;
        const runtime = user.runtime || {};
        const udata = (typeof parseUserData === 'function') ? parseUserData(user.data) : (user.data || {});
        const mon = (typeof capacityMonitorsData !== 'undefined' && capacityMonitorsData[nid]) || {};

        const city = mon.city || udata.city || user.city || '-';
        const province = mon.state_province || udata.state || user.state || '-';
        const priorityBank = (udata.priority_banks && udata.priority_banks[0]) || udata.primary_bank || user.primary_bank || '-';
        const selectedBank = mon.selected_bank || runtime.target_bank || udata.selected_bank || '-';
        const mode = mon.selected_mode || runtime.selected_mode || udata.bank_mode || 'online';
        const modeText = mode === 'online' 
            ? '<span class="badge badge-status-running"><i class="fa-solid fa-laptop me-1"></i>غیرحضوری</span>' 
            : '<span class="badge badge-status-neutral"><i class="fa-solid fa-building me-1"></i>حضوری</span>';
        const branch = udata.branch_code || (udata.priority_branches && udata.priority_branches[0]) || '-';

        const capState = mon.state || runtime.capacity_monitor_state || (runtime.status === 'BANK_CAPACITY_FULL' ? 'WAITING_CAPACITY' : (runtime.status || user.status || 'READY'));
        const badgeCls = (typeof getCapacityStateBadge === 'function') ? getCapacityStateBadge(capState) : 'badge bg-secondary';
        const stateText = (typeof translateCapacityState === 'function') ? translateCapacityState(capState) : capState;
        const lastChecked = mon.last_checked_at || runtime.last_checked_at || 'چند لحظه پیش';

        return `
            <tr>
                <td>
                    <strong>${escapeHtml(user.full_name || '')}</strong>
                    <div class="font-monospace small text-muted">${(typeof formatNid === 'function') ? formatNid(nid) : nid}</div>
                </td>
                <td>
                    <div>${escapeHtml(province)}</div>
                    <small class="text-muted">${escapeHtml(city)}</small>
                </td>
                <td><span class="badge badge-status-info">${escapeHtml(priorityBank)}</span></td>
                <td><strong>${escapeHtml(selectedBank)}</strong></td>
                <td>${modeText}</td>
                <td><small class="text-muted">${escapeHtml(branch)}</small></td>
                <td><span class="${badgeCls}">${stateText}</span></td>
                <td class="small text-muted">${escapeHtml(lastChecked)}</td>
            </tr>
        `;
    }).join('');
}

// Hook updateStage3BankStatusTable into regular render cycles
const _origRenderBankSelectList = typeof renderBankSelectList === 'function' ? renderBankSelectList : null;
if (_origRenderBankSelectList) {
    renderBankSelectList = function(data) {
        _origRenderBankSelectList(data);
        try { updateStage3BankStatusTable(data); } catch(e) {}
    };
}

const _origRenderStatusList = typeof renderStatusList === 'function' ? renderStatusList : null;
if (_origRenderStatusList) {
    renderStatusList = function(data) {
        _origRenderStatusList(data);
        try { updateStage3BankStatusTable(data); } catch(e) {}
    };
}

// Edition management (ADMIN vs CLIENT)
function updateAppEditionUI(user) {
    const badge = document.getElementById('userEditionBadge');
    const title = document.getElementById('editionTitle');
    const isAdmin = Boolean(user && user.is_admin);
    
    if (badge && title) {
        if (isAdmin) {
            title.textContent = 'MAHANBOT ADMIN';
            badge.style.background = 'linear-gradient(135deg, #00BFB3, #009F98)';
            badge.title = 'نسخه مدیریت کامل سامانه';
        } else {
            title.textContent = 'MAHANBOT CLIENT';
            badge.style.background = 'linear-gradient(135deg, #E5A900, #F4BD32)';
            badge.style.color = '#263746';
            badge.title = 'نسخه کاربری و ثبت‌نام متقاضی';
        }
    }
    
    document.body.classList.toggle('client-edition', !isAdmin);
    document.body.classList.toggle('admin-edition', isAdmin);
    
    // Hide or disable admin-only elements in client mode
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = isAdmin ? '' : 'none';
    });
}

// Enhance loadSubscriptionManager with edition check
const _origLoadSubscriptionManager = typeof loadSubscriptionManager === 'function' ? loadSubscriptionManager : null;
if (_origLoadSubscriptionManager) {
    loadSubscriptionManager = async function() {
        try {
            const meResponse = await fetch('/auth/me');
            if (meResponse.status === 401) { location.replace('/login'); return; }
            const me = await meResponse.json();
            updateAppEditionUI(me);
            if (!me.is_admin) return;
            return await _origLoadSubscriptionManager();
        } catch(e) {
            console.warn('loadSubscriptionManager wrapped error:', e);
        }
    };
}

function renderRecoverList(data) {
    const tbody = document.getElementById('recoverListBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    (data || []).forEach((user, idx) => {
        const d = parseUserData(user.data);
        const code = d.tracking_code || '-';
        const hasCode = code && code !== '-';
        const codeBadge = hasCode
            ? `<span class="badge bg-success-subtle text-success fw-bold px-2 py-1 font-monospace">${escapeHtml(code)}</span>`
            : `<span class="text-secondary fw-semibold">فاقد کد رهگیری</span>`;

        tbody.innerHTML += `
            <tr>
                <td class="text-center fw-bold" style="color: #64748B; font-size: 12px;">${idx + 1}</td>
                <td>
                    <span class="applicant-name-link" onclick="editUser(${idx})" title="مشاهده و ویرایش">${escapeHtml(user.full_name)}</span>
                </td>
                <td class="font-monospace text-center fw-semibold" style="color: #E2E8F0; font-size: 13px; letter-spacing: 0.5px;">${formatNid(user.national_id)}</td>
                <td class="font-monospace text-center">${codeBadge}</td>
                <td class="text-center"><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>
                <td class="text-center">
                    <button class="btn btn-sm btn-outline-primary rounded-pill px-3" onclick="actionRecover('${user.national_id}')">
                        <i class="fa-solid fa-file-invoice me-1"></i>بازیابی کد رهگیری
                    </button>
                </td>
            </tr>
        `;
    });
}

function renderDeleteReqList(data) {
    const tbody = document.getElementById('deleteReqListBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    (data || []).forEach((user, idx) => {
        const d = parseUserData(user.data);
        const code = d.tracking_code || '-';
        const hasCode = code && code !== '-';
        const codeDisplay = hasCode
            ? `<span class="badge bg-success-subtle text-success fw-bold px-2 py-1 font-monospace">${escapeHtml(code)}</span>`
            : `<span class="badge bg-danger-subtle text-danger fw-semibold">فاقد کد (الزامی)</span>`;

        const deleteButton = hasCode
            ? `<button class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="actionDeleteReq('${user.national_id}')">
                   <i class="fa-solid fa-trash-can me-1"></i>حذف از سامانه مرکزی
               </button>`
            : `<button class="btn btn-sm btn-outline-secondary rounded-pill px-3" disabled title="برای حذف در سامانه ve.cbi.ir، کد رهگیری الزامی است. ابتدا بازیابی کنید.">
                   <i class="fa-solid fa-ban me-1"></i>نیازمند کد رهگیری
               </button>`;

        tbody.innerHTML += `
            <tr>
                <td class="text-center fw-bold" style="color: #64748B; font-size: 12px;">${idx + 1}</td>
                <td>
                    <span class="applicant-name-link" onclick="editUser(${idx})" title="مشاهده و ویرایش">${escapeHtml(user.full_name)}</span>
                </td>
                <td class="font-monospace text-center fw-semibold" style="color: #E2E8F0; font-size: 13px; letter-spacing: 0.5px;">${formatNid(user.national_id)}</td>
                <td class="font-monospace text-center">${codeDisplay}</td>
                <td class="text-center"><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>
                <td class="text-center">${deleteButton}</td>
            </tr>
        `;
    });
}

async function actionDeleteReq(nid) {
    const user = (allUsersData || []).find(u => String(u.national_id) === String(nid));
    const name = user?.full_name || nid;
    if (!confirm(`آیا از ارسال درخواست حذف ثبت‌نام متقاضی «${name}» به سامانه مرکزی ve.cbi.ir اطمینان دارید؟\n\n⚠️ این عملیات، ثبت‌نام قبلی را در سامانه بانک مرکزی (TasReqDelete.aspx) لغو می‌کند تا امکان ثبت‌نام مجدد فعال شود.`)) {
        return;
    }
    try {
        const response = await apiCall(`/bot/action/delete-request/${nid}`, 'POST', {confirmed:true});
        if (response?.status === 'queued' || response?.status === 'already_running') {
            setUserStatus(nid, 'Running');
            switchView('dashboard');
        } else {
            alert('درخواست حذف ثبت‌نام در صف قرار گرفت.');
        }
    } catch (e) {
        alert(e.message || 'خطا در شروع بات حذف ثبت‌نام');
    }
}


async function clearApplicantTracking(nid) {
    if (!confirm('آیا از پاکسازی کد رهگیری و بازنشانی پرونده این متقاضی برای ثبت‌نام مجدد اطمینان دارید؟')) return;
    try {
        const res = await fetch(`/applicants/${nid}/clear-tracking`, {
            method: 'POST',
            headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' }
        });
        if (res.ok) {
            showToast('کد رهگیری پاکسازی شد و متقاضی برای ثبت مجدد آماده گردید.', 'success');
            if (typeof loadApplicants === 'function') loadApplicants();
        } else {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'خطا در پاکسازی کد رهگیری', 'error');
        }
    } catch (e) {
        showToast('خطا در ارتباط با سرور: ' + e, 'error');
    }
}


/* ══════════════════════════════════════════════════════════════════
   MAHANBOT 2.0 — QUEUE & STATE MACHINE DASHBOARD MANAGEMENT (PHASE 7)
   ══════════════════════════════════════════════════════════════════ */

window.cachedQueueItems = [];
window.cachedOtpQueueItems = [];

const FSM_STATE_LABELS = {
    'QUEUED': { text: 'در صف انتظار', cls: 'badge bg-secondary' },
    'PREPARING': { text: 'آماده‌سازی', cls: 'badge bg-info text-dark' },
    'STARTING': { text: 'آغاز فرایند', cls: 'badge bg-primary' },
    'REQUESTING_OTP': { text: 'ارسال درخواست OTP', cls: 'badge bg-warning text-dark' },
    'WAITING_OTP': { text: 'در انتظار پیامک', cls: 'badge bg-warning text-dark' },
    'OTP_RECEIVED': { text: 'دریافت کد پیامک', cls: 'badge bg-info text-dark' },
    'VERIFYING_OTP': { text: 'تایید کد پیامک', cls: 'badge bg-info text-dark' },
    'AUTHENTICATED': { text: 'ورود موفق', cls: 'badge bg-success' },
    'STEP_3_READY': { text: 'مرحله ۳ آماده', cls: 'badge bg-primary' },
    'SELECTING_BANK': { text: 'بررسی بانک‌ها', cls: 'badge bg-primary' },
    'WAITING_BRANCH_LIST': { text: 'دریافت فهرست شعب', cls: 'badge bg-info text-dark' },
    'SELECTING_BRANCH': { text: 'انتخاب شعبه', cls: 'badge bg-primary' },
    'BRANCH_SELECTED': { text: 'شعبه انتخاب شد', cls: 'badge bg-success' },
    'BRANCH_NOT_FOUND': { text: 'شعبه یافت نشد', cls: 'badge bg-warning text-dark' },
    'BRANCH_SELECTION_FAILED': { text: 'خطا در ثبت شعبه', cls: 'badge bg-danger' },
    'CAPACITY_FULL': { text: 'ظرفیت تکمیل', cls: 'badge bg-warning text-dark' },
    'WAITING_AVAILABILITY': { text: 'انتظار سهمیه', cls: 'badge bg-warning text-dark' },
    'READY_TO_SUBMIT': { text: 'آماده ثبت نهایی', cls: 'badge bg-success' },
    'SUBMITTING': { text: 'ارسال نهایی', cls: 'badge bg-primary' },
    'CONFIRMED': { text: 'ثبت قطعی موفق', cls: 'badge bg-success' },
    'SESSION_EXPIRED': { text: 'انقضای نشست', cls: 'badge bg-danger' },
    'PAUSED': { text: 'متوقف شده', cls: 'badge bg-secondary' },
    'BLOCKED': { text: 'نیازمند اقدام', cls: 'badge bg-danger' },
    'FAILED': { text: 'خطا', cls: 'badge bg-danger' }
};

async function loadQueueData() {
    try {
        const res = await apiCall('/api/queue', 'GET');
        if (res && res.status === 'success') {
            window.cachedQueueItems = res.data || [];
            filterQueueTable();
        }
        const statsRes = await apiCall('/api/queue/stats', 'GET');
        if (statsRes && statsRes.stats) {
            renderQueueStats(statsRes.stats);
        }
    } catch (e) {
        console.warn('loadQueueData error:', e);
    }
}

async function loadOtpQueueData() {
    try {
        const res = await apiCall('/api/otp-queue', 'GET');
        if (res && res.status === 'success') {
            window.cachedOtpQueueItems = res.data || [];
            renderOtpQueueTable(window.cachedOtpQueueItems);
        }
    } catch (e) {
        console.warn('loadOtpQueueData error:', e);
    }
}

function renderQueueStats(stats) {
    if (!stats) return;
    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.innerText = toPersianNum(val ?? 0);
    };
    setVal('queue-stat-total', stats.total);
    setVal('queue-stat-waiting', stats.waiting);
    setVal('queue-stat-running', stats.running);
    setVal('queue-stat-waiting-sms', stats.waiting_sms);
    setVal('queue-stat-step-3', stats.step_3);
    setVal('queue-stat-waiting-bank', stats.waiting_bank);
    setVal('queue-stat-completed', stats.completed);
    setVal('queue-stat-failed', (stats.failed || 0) + (stats.paused || 0));
}

function filterQueueTable() {
    const search = (document.getElementById('queueSearchInput')?.value || '').trim().toLowerCase();
    const status = (document.getElementById('queueStatusFilter')?.value || '').trim();
    const priority = (document.getElementById('queuePriorityFilter')?.value || '').trim();

    let items = window.cachedQueueItems || [];

    if (search) {
        items = items.filter(it =>
            (it.applicant_name || '').toLowerCase().includes(search) ||
            (it.applicant_id || '').includes(search)
        );
    }
    if (status) {
        items = items.filter(it => (it.queue_status || '') === status);
    }
    if (priority) {
        items = items.filter(it => (it.priority || '') === priority);
    }

    renderQueueTable(items);
}

function renderQueueTable(items) {
    const tbody = document.getElementById('queueTableBody');
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" class="text-center text-muted py-4">هیچ متقاضی در این دسته یافت نشد.</td></tr>';
        return;
    }

    let html = '';
    items.forEach((it, idx) => {
        const rank = it.rank || (idx + 1);
        const p = (it.priority || 'NORMAL').toUpperCase();
        const pBadgeCls = p === 'HIGH' ? 'bg-danger' : (p === 'LOW' ? 'bg-secondary' : 'bg-info text-dark');
        const pText = p === 'HIGH' ? 'بالا (HIGH)' : (p === 'LOW' ? 'پایین (LOW)' : 'عادی');

        const nidDisplay = (typeof sensitiveMasked !== 'undefined' && sensitiveMasked)
            ? (it.applicant_id ? it.applicant_id.substring(0, 3) + '****' + it.applicant_id.slice(-3) : '---')
            : (it.applicant_id || '---');

        const fsmInfo = FSM_STATE_LABELS[it.bot_state] || { text: it.bot_state || 'نامشخص', cls: 'badge bg-secondary' };

        const sessionDot = it.session_active
            ? '<span class="badge bg-success" title="مرورگر فعال و متصل"><i class="fa-solid fa-circle-dot me-1"></i> متصل</span>'
            : '<span class="badge bg-secondary" title="مرورگر غیرفعال"><i class="fa-regular fa-circle me-1"></i> غیرفعال</span>';

        let lastAct = '---';
        if (it.last_activity_at) {
            const secAgo = Math.max(0, Math.round(Date.now() / 1000 - it.last_activity_at));
            lastAct = secAgo < 60 ? `${secAgo} ثانیه پیش` : `${Math.round(secAgo / 60)} دقیقه پیش`;
        }

        const isRunning = it.queue_status === 'RUNNING';
        const isPaused = it.queue_status === 'PAUSED';

        html += `
        <tr>
            <td class="fw-bold text-center">
                <div class="d-flex flex-column align-items-center gap-1">
                    <span>${toPersianNum(rank)}</span>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary py-0 px-1" title="افزایش اولویت در صف" onclick="reorderQueueItem('${it.queue_id}', ${rank - 1})" ${rank <= 1 ? 'disabled' : ''}>▲</button>
                        <button class="btn btn-outline-secondary py-0 px-1" title="کاهش اولویت در صف" onclick="reorderQueueItem('${it.queue_id}', ${rank + 1})">▼</button>
                    </div>
                </div>
            </td>
            <td>
                <select class="form-select form-select-sm fw-bold" style="font-size:0.8rem; width:95px;" onchange="setQueuePriority('${it.applicant_id}', this.value)">
                    <option value="HIGH" ${p === 'HIGH' ? 'selected' : ''}>بالا (HIGH)</option>
                    <option value="NORMAL" ${p === 'NORMAL' ? 'selected' : ''}>عادی</option>
                    <option value="LOW" ${p === 'LOW' ? 'selected' : ''}>پایین (LOW)</option>
                </select>
            </td>
            <td><strong>${it.applicant_name || 'بدون نام'}</strong></td>
            <td><code>${toPersianNum(nidDisplay)}</code></td>
            <td><small class="badge bg-light text-dark border">${it.loan_type === 'CHILD' ? 'فرزندآوری' : 'ازدواج'}</small></td>
            <td><small>${it.requested_bank || 'همه بانک‌ها'}</small></td>
            <td><span class="${fsmInfo.cls}">${fsmInfo.text}</span></td>
            <td><small>${it.otp_state || '-'}</small></td>
            <td>${sessionDot}</td>
            <td><small class="text-muted">${lastAct}</small></td>
            <td>
                <div class="d-flex gap-1">
                    ${!isRunning ? `
                    <button class="btn btn-sm btn-success py-0 px-2" title="شروع پردازش متقاضی" onclick="startQueuedUser('${it.applicant_id}')">
                        <i class="fa-solid fa-play"></i>
                    </button>` : `
                    <button class="btn btn-sm btn-warning py-0 px-2" title="توقف موقت" onclick="pauseQueuedUser('${it.applicant_id}')">
                        <i class="fa-solid fa-pause"></i>
                    </button>`}
                    ${isPaused ? `
                    <button class="btn btn-sm btn-info py-0 px-2" title="ادامه پردازش" onclick="resumeQueuedUser('${it.applicant_id}')">
                        <i class="fa-solid fa-rotate-right"></i>
                    </button>` : ''}
                    <button class="btn btn-sm ${it.bot_state && it.bot_state.includes('BRANCH') ? 'btn-info text-white' : 'btn-outline-secondary'} py-0 px-2" title="انتخاب دستی شعبه" onclick="openManualBranchModal('${it.applicant_id}', '${it.applicant_name || ''}')">
                        <i class="fa-solid fa-building-columns"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger py-0 px-2" title="خروج از صف فعال" onclick="removeQueueItem('${it.applicant_id}')">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>
            </td>
        </tr>`;
    });

    tbody.innerHTML = html;
}

function renderOtpQueueTable(items) {
    const tbody = document.getElementById('otpQueueBody');
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">درخواستی در انتظار پیامک نیست.</td></tr>';
        return;
    }

    let html = '';
    const now = Math.floor(Date.now() / 1000);

    items.forEach(it => {
        const rem = Math.max(0, (it.expires_at || 0) - now);
        const remText = rem > 0 ? `${rem} ثانیه` : '<span class="text-danger">منقضی شد</span>';
        const stBadge = it.status === 'PENDING' ? 'bg-warning text-dark' : (it.status === 'DELIVERED' ? 'bg-success' : 'bg-secondary');

        html += `
        <tr>
            <td><strong>${it.applicant_id}</strong></td>
            <td><code>${toPersianNum(it.phone_number || '-')}</code></td>
            <td><small class="badge bg-light text-dark">${it.step || 'STEP_1'}</small></td>
            <td>${remText}</td>
            <td><span class="badge ${stBadge}">${it.status}</span></td>
            <td>
                <div class="input-group input-group-sm" style="width:170px;">
                    <input id="otp_in_${it.request_id}" class="form-control form-control-sm" placeholder="کد ۶ رقمی" maxlength="8">
                    <button class="btn btn-primary btn-sm" onclick="deliverManualOtpQueue('${it.request_id}', '${it.applicant_id}')">ارسال</button>
                </div>
            </td>
        </tr>`;
    });

    tbody.innerHTML = html;
}

async function deliverManualOtpQueue(requestId, nid) {
    const input = document.getElementById(`otp_in_${requestId}`);
    const code = (input?.value || '').trim();
    if (!code) {
        showMahanToast('خطا', 'لطفاً کد پیامک را وارد کنید.', 'warning');
        return;
    }
    try {
        const res = await apiCall('/api/otp', 'POST', {
            request_id: requestId,
            applicant_id: nid,
            national_id: nid,
            code: code,
            otp: code,
            source: 'DASHBOARD_QUEUE'
        });
        showMahanToast('موفق', 'کد پیامک با موفقیت تحویل داده شد.', 'success');
        loadOtpQueueData();
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا در ارسال کد', e.message, 'error');
    }
}

async function startQueuedUser(nid) {
    try {
        await apiCall(`/api/queue/${nid}/start`, 'POST');
        showMahanToast('آغاز پردازش', `بات متقاضی ${nid} در اولویت اجرا قرار گرفت.`, 'info');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا در شروع بات', e.message, 'error');
    }
}

async function pauseQueuedUser(nid) {
    try {
        await apiCall(`/api/queue/${nid}/pause`, 'POST');
        showMahanToast('توقف متقاضی', `متقاضی ${nid} موقتاً متوقف شد.`, 'warning');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function resumeQueuedUser(nid) {
    try {
        await apiCall(`/api/queue/${nid}/resume`, 'POST');
        showMahanToast('ادامه پردازش', `متقاضی ${nid} به صف انتظار بازگشت.`, 'success');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function setQueuePriority(nid, priority) {
    try {
        await apiCall(`/api/queue/${nid}/priority`, 'POST', { priority: priority });
        showMahanToast('تغییر اولویت', `اولویت متقاضی به ${priority} تغییر کرد.`, 'success');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function reorderQueueItem(queueId, targetPos) {
    if (targetPos < 1) return;
    try {
        await apiCall(`/api/queue/${queueId}/reorder`, 'POST', { position: targetPos });
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function removeQueueItem(nid) {
    if (!confirm(`آیا از خروج متقاضی ${nid} از صف پردازش اطمینان دارید؟`)) return;
    try {
        await apiCall(`/api/queue/${nid}/remove`, 'POST');
        showMahanToast('لغو از صف', 'متقاضی از صف فعال حذف گردید.', 'info');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function startAllQueue() {
    try {
        const res = await apiCall('/api/queue/start-all', 'POST');
        showMahanToast('شروع همه', `${res.started || 0} متقاضی در صف آغاز به کار نمودند.`, 'success');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function pauseAllQueue() {
    try {
        const res = await apiCall('/api/queue/pause-all', 'POST');
        showMahanToast('توقف همه', `${res.paused || 0} متقاضی متوقف شدند.`, 'warning');
        loadQueueData();
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}


// ══════════════════════════════════════════════════════════════════════════
// MAHANBOT 2.0: SMART CAPACITY RADAR & TELEGRAM FRONTEND CONTROLLER
// ══════════════════════════════════════════════════════════════════════════
let _radarStatesCache = [];
let _radarProvincesLoaded = false;

async function loadRadarData() {
    try {
        const res = await apiCall('/api/radar/summary', 'GET');
        if (!res || res.status !== 'ok') return;

        const summary = res.summary || {};
        _radarStatesCache = res.states || [];

        // Update metrics
        const elAvail = document.getElementById('radarMetricAvailable');
        const elRes = document.getElementById('radarMetricReserve');
        const elFull = document.getElementById('radarMetricFull');
        const elChan = document.getElementById('radarMetricChannel');

        if (elAvail) elAvail.textContent = toFarsiDigits(summary.available_count || 0);
        if (elRes) elRes.textContent = toFarsiDigits(summary.reserve_count || 0);
        if (elFull) elFull.textContent = toFarsiDigits(summary.full_count || 0);

        const tg = res.telegram_status || {};
        if (elChan) {
            elChan.textContent = tg.admin_chat_id ? tg.admin_chat_id : '@Mahanvpn3';
        }

        // 18:00 Batch Indicator
        const b18 = document.getElementById('radar18Badge');
        const bStatus = document.getElementById('radarBatchStatusDisplay');
        if (res.near_18_batch) {
            if (b18) {
                b18.className = 'badge p-2 px-3 bg-danger text-white animate-pulse';
                b18.innerHTML = '<i class="fa-solid fa-bolt me-1"></i>پنجره طلایی ۱۸:۰۰ فعال است!';
            }
            if (bStatus) {
                bStatus.className = 'p-2 rounded font-monospace bg-danger text-white';
                bStatus.textContent = '⚡ هم‌اکنون در بازه تخصیص سهمیه‌های روزانه (ساعت ۱۸:۰۰)';
            }
        } else {
            if (b18) {
                b18.className = 'badge p-2 px-3';
                b18.style.background = '#fef3c7';
                b18.style.color = '#92400e';
                b18.style.border = '1px solid #fcd34d';
                b18.innerHTML = '<i class="fa-solid fa-clock me-1"></i>آماده‌باش برای ساعت ۱۸:۰۰';
            }
            if (bStatus) {
                bStatus.className = 'p-2 rounded font-monospace';
                bStatus.style.background = '#f1f5f9';
                bStatus.style.color = '#334155';
                bStatus.textContent = 'عادی (پایش پیوسته قبل از ساعت ۱۸:۰۰)';
            }
        }

        // Populate Province filter dropdown
        if (!_radarProvincesLoaded) {
            const provSelect = document.getElementById('radarFilterProvince');
            if (provSelect) {
                const provs = [...new Set(_radarStatesCache.map(s => s.province_name).filter(Boolean))].sort();
                provs.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p;
                    opt.textContent = p;
                    provSelect.appendChild(opt);
                });
                _radarProvincesLoaded = true;
            }
        }

        // Populate Telegram settings inputs if empty
        const tokenInput = document.getElementById('telegramBotTokenInput');
        const chatInput = document.getElementById('telegramChatIdInput');
        const toggle = document.getElementById('telegramNotificationsToggle');
        if (tokenInput && !tokenInput.value && tg.has_token) {
            tokenInput.value = '8988974593:AAHnlt1ZVf5WAMFm59fbLtenxppF9aHM_Fc';
        }
        if (chatInput && !chatInput.value) {
            chatInput.value = tg.admin_chat_id || '-1003720759140';
        }
        if (toggle && tg.notifications_enabled !== undefined) {
            toggle.checked = tg.notifications_enabled;
        }

        renderRadarTable(_radarStatesCache);
    } catch (e) {
        console.warn('Error loading radar data:', e);
    }
}

function renderRadarTable(states) {
    const tbody = document.getElementById('radarLiveTableBody');
    const badge = document.getElementById('radarTableCountBadge');
    if (!tbody) return;

    if (badge) badge.textContent = `${toFarsiDigits(states.length)} مورد`;

    if (!states || states.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="text-center p-4 text-muted">
                    <i class="fa-solid fa-circle-info me-2"></i>هنوز داده‌ای در رادار ثبت نشده است. هم‌زمان با استعلام و اجرای بات‌ها اطلاعات استان‌ها ثبت می‌شود.
                </td>
            </tr>
        `;
        return;
    }

    const statusBadgeMap = {
        'AVAILABLE_OBSERVED': '<span class="badge" style="background:#dcfce7; color:#15803d; border:1px solid #86efac; font-size:0.8rem;"><i class="fa-solid fa-circle-check me-1"></i>دارای ظرفیت (مشاهده‌شده)</span>',
        'AVAILABLE_CONFIRMED': '<span class="badge" style="background:#bbf7d0; color:#166534; border:1px solid #4ade80; font-size:0.8rem;"><i class="fa-solid fa-circle-check me-1"></i>ظرفیت قطعی</span>',
        'RESERVE': '<span class="badge" style="background:#fef3c7; color:#b45309; border:1px solid #fde68a; font-size:0.8rem;"><i class="fa-solid fa-hourglass-half me-1"></i>صف ذخیره (تخصیص جدید)</span>',
        'FULL': '<span class="badge" style="background:#ffe4e6; color:#be123c; border:1px solid #fecdd3; font-size:0.8rem;"><i class="fa-solid fa-circle-xmark me-1"></i>تکمیل ظرفیت</span>',
        'MAINTENANCE': '<span class="badge bg-secondary text-white font-size:0.8rem;"><i class="fa-solid fa-wrench me-1"></i>بروزرسانی</span>',
    };

    let html = '';
    states.forEach((item, idx) => {
        const badgeHtml = statusBadgeMap[item.status] || `<span class="badge bg-light text-dark border">${item.status}</span>`;
        const timeAgo = item.last_observed_at ? formatTimeAgo(item.last_observed_at) : 'نامشخص';
        const visitLabel = item.visit_method === 'online' ? '<span class="badge bg-info text-dark">غیرحضوری (شعبه مجازی)</span>' : '<span class="badge bg-light text-secondary border">حضوری</span>';
        const loanLabel = item.loan_type === 'child' ? 'فرزندآوری' : 'ازدواج';

        html += `
            <tr>
                <td class="text-center font-monospace small text-muted">${toFarsiDigits(idx + 1)}</td>
                <td class="fw-bold" style="color:#1e293b;">${item.province_name || 'سراسری'}</td>
                <td class="text-secondary small">${item.city_name || 'کل استان'}</td>
                <td class="fw-bold text-primary">${item.bank_name || 'بانک'}</td>
                <td class="small">${loanLabel}</td>
                <td>${visitLabel}</td>
                <td>${badgeHtml}</td>
                <td class="small text-muted font-monospace">${timeAgo}</td>
                <td class="text-center">
                    <button class="btn btn-sm btn-outline-primary p-1 px-2" style="font-size:0.75rem;" onclick="sendTelegramAlertForBank('${escapeJs(item.province_name)}', '${escapeJs(item.bank_name)}', '${escapeJs(item.status)}')">
                        <i class="fa-brands fa-telegram me-1"></i>اعلان به کانال
                    </button>
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

function filterRadarTable() {
    const provFilter = (document.getElementById('radarFilterProvince')?.value || '').trim();
    const stFilter = (document.getElementById('radarFilterStatus')?.value || '').trim();
    const searchFilter = (document.getElementById('radarSearchInput')?.value || '').trim().toLowerCase();

    let filtered = _radarStatesCache.filter(item => {
        if (provFilter && item.province_name !== provFilter) return false;
        if (stFilter && item.status !== stFilter) return false;
        if (searchFilter) {
            const hay = `${item.province_name} ${item.city_name} ${item.bank_name}`.toLowerCase();
            if (!hay.includes(searchFilter)) return false;
        }
        return true;
    });

    renderRadarTable(filtered);
}

async function saveTelegramSettings() {
    const token = document.getElementById('telegramBotTokenInput')?.value?.trim();
    const chatId = document.getElementById('telegramChatIdInput')?.value?.trim();
    const notifyEnabled = document.getElementById('telegramNotificationsToggle')?.checked;

    try {
        const payload = {
            bot_token: token || undefined,
            admin_chat_id: chatId || undefined,
            channel_id: chatId || undefined,
            notifications_enabled: notifyEnabled,
        };
        const res = await apiCall('/api/radar/telegram/config', 'POST', payload);
        if (res && res.status === 'ok') {
            showMahanToast('موفق', 'تنظیمات تلگرام و کانال @Mahanvpn3 با موفقیت ذخیره شد.', 'success');
        } else {
            showMahanToast('خطا', 'عدم امکان ذخیره تنظیمات تلگرام', 'error');
        }
    } catch (e) {
        showMahanToast('خطا', e.message, 'error');
    }
}

async function sendTelegramTestFromUI() {
    const chatId = document.getElementById('telegramChatIdInput')?.value?.trim() || '-1003720759140';
    try {
        showMahanToast('ارسال تست', 'در حال ارسال پیام تست به کانال @Mahanvpn3...', 'info');
        const res = await apiCall('/api/radar/telegram/test', 'POST', { chat_id: chatId });
        if (res && res.status === 'ok') {
            showMahanToast('ارسال موفق', res.message || 'پیام تست با موفقیت به کانال ارسال شد.', 'success');
        } else {
            showMahanToast('نتیجه تست', res.detail || 'پیام در صف ارسال قرار گرفت.', 'warning');
        }
    } catch (e) {
        showMahanToast('نتیجه تست', e.message || 'پیام در صف ارسال خودکار ثبت شد.', 'warning');
    }
}

async function publishChannelPostFromUI() {
    const title = document.getElementById('telegramPostTitle')?.value?.trim() || '🚀 ظرفیت جدید سرویس‌های MahanBot و تسهیلات بانکی';
    const content = document.getElementById('telegramPostContent')?.value?.trim();

    if (!content) {
        showMahanToast('توجه', 'لطفاً متن پیام یا تعرفه‌ها را وارد کنید.', 'warning');
        return;
    }

    try {
        const chatId = document.getElementById('telegramChatIdInput')?.value?.trim() || '-1003720759140';
        const testPayload = {
            chat_id: chatId,
            message: `<b>${title}</b>\n\n${content}\n\n📢 @Mahanvpn3 | 🤖 @MahanVPN_Shop_Bot`
        };
        showMahanToast('در حال انتشار', 'در حال ارسال اطلاعیه به کانال @Mahanvpn3...', 'info');
        const res = await apiCall('/api/radar/telegram/test', 'POST', testPayload);
        showMahanToast('انتشار پیام', res.message || 'اطلاعیه با موفقیت به کانال ارسال شد.', 'success');
        document.getElementById('telegramPostContent').value = '';
    } catch (e) {
        showMahanToast('نتیجه انتشار', e.message || 'اطلاعیه در صف ارسال قرار گرفت.', 'warning');
    }
}

function sendTelegramAlertForBank(province, bank, status) {
    const chatId = document.getElementById('telegramChatIdInput')?.value?.trim() || '-1003720759140';
    const stLabel = status === 'RESERVE' ? 'در انتظار ظرفیت جدید (صف ذخیره)' : 'دارای ظرفیت ثبت‌نام';
    const msg = `🔔 <b>اعلان فوری ظرفیت بانک | رادار MAHANBOT</b>\n\n📍 استان: <b>${province}</b>\n🏦 بانک: <b>${bank}</b>\n📊 وضعیت: <b>${stLabel}</b>\n🕒 زمان: ${new Date().toLocaleTimeString('fa-IR')}\n\n📢 کانال رسمی: @Mahanvpn3\n🤖 ربات فروش: @MahanVPN_Shop_Bot`;
    
    apiCall('/api/radar/telegram/test', 'POST', { chat_id: chatId, message: msg })
        .then(() => showMahanToast('اعلان تلگرام', `اعلان ${bank} به کانال ارسال شد.`, 'success'))
        .catch(err => showMahanToast('اعلان تلگرام', err.message || 'پیام در صف قرار گرفت.', 'warning'));
}

function showTelegramQuickPost() {
    switchView('radar');
    const el = document.getElementById('telegramPostContent');
    if (el) el.focus();
}

function togglePasswordVisibility(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.type = el.type === 'password' ? 'text' : 'password';
}

function escapeJs(str) {
    if (!str) return '';
    return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}
