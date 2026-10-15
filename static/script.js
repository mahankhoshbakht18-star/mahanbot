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
    'ملت',
    'صادرات',
    'تجارت',
    'سپه',
    'پارسیان',
    'پاسارگاد',
    'سامان',
    'دی',
    'اقتصاد نوین',

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
        document.getElementById('subscriptionPanel').hidden = false;
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

async function loadBrowserDisplayMode() {
    try {
        const settings = await (await fetch(`${httpBase}/settings`)).json();
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


    if(viewId === 'list') fetchApplicants('list');

    if(viewId === 'bank-select') fetchApplicants('bank-select');

    if(viewId === 'status') fetchApplicants('status');

    if(viewId === 'recover') fetchApplicants('recover');

    if(viewId === 'delete-req') fetchApplicants('delete-req');

    if(viewId === 'settings') {

        loadSettings();

        loadBrowserProfile();

        loadAllowedDomains();

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

    const optionsEl = document.getElementById('bankOptions');

    const priorityEl = document.getElementById('priorityBanksList');

    const favoriteEl = document.getElementById('favoriteBanksList');

    if (!optionsEl || !priorityEl || !favoriteEl) return;

    renderBankOptions();

    renderPriorityBanks();

    renderFavoriteBanks();

    setupDragAndDrop(priorityEl);

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

    let activeUser = null;



    users.forEach(user => {

        let d = parseUserData(user.data);

        if(d.tracking_code) codeCount++;

        if(user.status.toLowerCase().includes('success')) successCount++;



        const normalizedStatus = String(user.status || '').toLowerCase();
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

                <td><strong>${user.full_name}</strong><br><small>${user.national_id}</small></td>

                <td><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>

                <td><button class="btn btn-sm btn-danger rounded-circle" onclick="stopBot('${user.national_id}')"><i class="fas fa-power-off"></i></button></td>

            `;

            tbody.appendChild(tr);

        }

    });



    if(runningCount===0) tbody.innerHTML = `<tr><td colspan="3" class="text-muted small py-3">${getMessage('labels.inactive', '')}</td></tr>`;

    window.currentActiveBotNid = runningNid || window.currentActiveBotNid || null;

    renderActiveApplicantCard(activeUser);



    if(document.getElementById('stat-total')) document.getElementById('stat-total').innerText = users.length;

    if(document.getElementById('stat-active')) document.getElementById('stat-active').innerText = runningCount;

    if(document.getElementById('stat-success')) document.getElementById('stat-success').innerText = successCount;

    if(document.getElementById('stat-codes')) document.getElementById('stat-codes').innerText = codeCount;

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

    metaEl.textContent = `کد ملی: ${user.national_id || '-'} | موبایل: ${mobile}`;

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



async function sendOtp(code) {

    const nid = window.currentActiveBotNid;

    if (!nid) {
        document.getElementById('otpStatus').textContent = 'ابتدا یک بات را اجرا کنید.';
        return;
    }

    document.getElementById('otpStatus').innerHTML = '...';

    await apiCall(`/receive_sms`, 'POST', {nid: nid, code: code});

    document.getElementById('otpStatus').innerHTML = `<span class="text-success">${getMessage('alerts.otp_sent', ' ')}</span>`;

    document.getElementById('otpInput').value = "";

}




async function sendOtpManualFallback(code) {

    const nid = window.currentActiveBotNid;

    if (!nid) return;

    const cleanCode = (code || '').trim();

    if (!cleanCode) return;

    document.getElementById('otpStatus').innerHTML = '...';

    await apiCall(`/manual_otp`, 'POST', {nid: nid, code: cleanCode});

    document.getElementById('otpStatus').innerHTML = `<span class="text-warning">کد دستی ارسال شد.</span>`;

    document.getElementById('otpInput').value = '';

}


async function sendManualOtp() {

    const nid = window.currentActiveBotNid;

    const input = document.getElementById('manualOtpInput');

    const statusEl = document.getElementById('manualOtpStatus');

    if (!input) return;
    if (!nid) {
        if (statusEl) statusEl.textContent = 'کاربر فعال پیدا نشد؛ ابتدا بات را اجرا کنید.';
        return;
    }

    const code = input.value.trim();

    if (!code) return;

    if (statusEl) statusEl.textContent = 'در حال ارسال...';

    await apiCall(`/manual_otp`, 'POST', {nid: nid, code: code});

    if (statusEl) statusEl.textContent = 'ارسال دستی انجام شد.';

    input.value = '';

}



async function fetchApplicants(mode) {

    try {

        const res = await fetch(`${httpBase}/applicants`);

        const data = await res.json();

        allUsersData = data;



        if(mode === 'list') renderMainList(data);

        else if(mode === 'bank-select') renderBankSelectList(data);

        else if(mode === 'status') renderStatusList(data);

        else if(mode === 'recover') renderSimpleList(data, 'recoverListBody', 'actionRecover', 'شروع بازیابی', 'btn-warning text-dark');

        else if(mode === 'delete-req') renderSimpleList(data, 'deleteReqListBody', 'actionDeleteReq', 'حذف درخواست', 'btn-danger');

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

    if(!tbody) return;

    tbody.innerHTML = '';

    data.forEach((user, idx) => {

        let d = parseUserData(user.data);

        let code = d.tracking_code || '-';

        

        tbody.innerHTML += `

            <tr>

                <td>${idx+1}</td>

                <td onclick="editUser(${idx})" style="cursor:pointer; color:var(--primary-color); font-weight:bold;">${user.full_name} <i class="fas fa-pen small ms-1 text-muted"></i></td>

                <td class="font-monospace">${user.national_id}</td>

                <td class="font-monospace text-success">${code}</td>

                <td><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>

                <td><button class="btn btn-sm btn-outline-success rounded-pill px-3" onclick="startRegister('${user.national_id}')"><i class="fas fa-play me-1"></i>شروع ثبت‌نام</button></td>

                <td><button class="btn btn-sm btn-outline-warning rounded-pill px-3" onclick="stopBot('${user.national_id}')"><i class="fas fa-stop me-1"></i>توقف</button></td>

                <td><button class="btn btn-sm btn-outline-danger rounded-pill" onclick="deleteApplicant('${user.national_id}')"><i class="fas fa-trash"></i></button></td>

            </tr>

        `;

    });

}



function renderBankSelectList(data) {
    const tbody = document.getElementById('bankSelectListBody');
    if(!tbody) return;
    tbody.innerHTML = '';
    data.forEach(user => {
        const runtime = user.runtime || {};
        const banks = Array.isArray(runtime.banks_available) ? runtime.banks_available : [];
        const nextSeconds = runtime.next_refresh ? Math.max(0, Math.ceil(runtime.next_refresh - Date.now() / 1000)) : null;
        const lastNext = `${runtimeTime(runtime.last_refresh)} / ${nextSeconds === null ? '-' : nextSeconds + 's'}`;
        const errorText = runtime.last_error ? escapeHtml(runtime.last_error) : '-';
        tbody.innerHTML += `
            <tr>
                <td>${escapeHtml(user.full_name)}</td>
                <td><span class="badge ${getStatusBadge(runtime.status || user.status)}">${escapeHtml(runtime.status || translateStatus(user.status))}</span></td>
                <td>${escapeHtml(runtime.current_step || '-')}</td>
                <td>${escapeHtml(runtime.browser_status || '-')}</td>
                <td>${escapeHtml(runtime.target_bank || '-')}</td>
                <td title="${escapeHtml(banks.join('، '))}">${banks.length}${runtime.new_banks?.length ? ` <span class="badge bg-success">+${runtime.new_banks.length}</span>` : ''}</td>
                <td>${lastNext}</td>
                <td>${runtimeTime(runtime.last_activity_at)}</td>
                <td>${Number(runtime.retry_count || 0)} / <span title="${errorText}">${errorText}</span></td>
                <td class="d-flex gap-1"><button class="btn btn-sm btn-primary" onclick="startBankSelect('${user.national_id}')">شروع</button><button class="btn btn-sm btn-warning" onclick="restartBankSelect('${user.national_id}')">راه‌اندازی مجدد</button><button class="btn btn-sm btn-danger" onclick="stopBot('${user.national_id}')">توقف</button></td>
            </tr>
        `;
    });
}



function renderSimpleList(data, elId, actionFn, txt, cls) {

    const tbody = document.getElementById(elId);

    if(!tbody) return;

    tbody.innerHTML = '';

    data.forEach(user => {

        tbody.innerHTML += `<tr><td>${user.full_name}</td><td class="font-monospace">${user.national_id}</td><td><button class="btn btn-sm ${cls}" onclick="${actionFn}('${user.national_id}')">${txt}</button></td></tr>`;

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

function actionDeleteReq(nid) { if(confirm(getMessage('alerts.delete_confirm', '  '))) apiCall(`/bot/action/delete-request/${nid}`, 'POST'); }

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

    document.getElementById('inpState').value = d.state || '';

    document.getElementById('inpCity').value = d.city || '';

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
        bank_mode: document.getElementById('inpBankMode') ? document.getElementById('inpBankMode').value : '',
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
        if (document.getElementById('set_allow_reserve_bank')) document.getElementById('set_allow_reserve_bank').checked = s.allow_reserve_bank === true;
        if (document.getElementById('set_diagnostic_logging')) document.getElementById('set_diagnostic_logging').checked = s.diagnostic_logging_enabled !== false;
        document.getElementById('set_bank_monitor').checked = s.bank_monitor_enabled !== false;
        document.getElementById('set_save_page').checked = s.save_page_enabled !== false;
        document.getElementById('set_save_html').checked = s.save_html !== false;
        document.getElementById('set_save_screenshot').checked = s.save_screenshot !== false;
        document.getElementById('set_save_change_only').checked = s.save_only_on_bank_change !== false;
        document.getElementById('set_auto_repair').checked = s.auto_repair_saved_pages === true;

        document.getElementById('set_headless').checked = s.headless || false;
        document.getElementById('set_browser_display_mode').value = s.browser_display_mode || 'external';
        applyBrowserDisplayMode(s.browser_display_mode || 'external');

        

        //  

        document.getElementById('set_captcha_mode').value = s.captcha_mode || 'model'; 

        document.getElementById('set_final_submit').checked = Boolean(s.auto_final_submit ?? s.final_submit ?? false);

        

        document.getElementById('set_useproxy').checked = s.use_proxy || false;

        document.getElementById('set_proxylist').value = s.proxy_list || '';



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

        proxy_list: document.getElementById('set_proxylist').value

    };

    

    await apiCall('/settings', 'POST', payload);

    applyBrowserDisplayMode(payload.browser_display_mode);

    alert(getMessage('alerts.settings_saved', '    .'));

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
    if (mode === 'online') {
        branchInput.value = '';
        branchInput.disabled = true;
        branchInput.placeholder = 'در روش غیرحضوری انتخاب شعبه غیرفعال است';
    } else {
        branchInput.disabled = false;
        branchInput.placeholder = 'مثال: کد شعبه یا نام شعبه ۱، شعبه ۲';
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

function getStatusBadge(s) { if(!s) return 'bg-light text-muted'; s=s.toLowerCase(); if(s.includes('succ')) return 'bg-success'; if(s.includes('stop')) return 'bg-danger'; if(s.includes('wait')) return 'bg-warning text-dark'; if(s.includes('run')) return 'bg-primary'; return 'bg-secondary'; }

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

    logEvents.push(payload);
    if (logEvents.length > LOG_BUFFER_LIMIT) {
        logEvents.shift();
    }
    scheduleRenderLogs();
    if (payload.type === 'favorite_found') {

        showFavoriteToast(payload);

        playAlertSound();

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

    if (currentViewId === 'bank-select') renderBankSelectList(allUsersData);

    if (currentViewId === 'status') renderStatusList(allUsersData);

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

        const nidLabel = event.nid ? `<span style="color:#00e5ff">${maskSensitive(event.nid)}</span>` : '<span style="color:#00e5ff">system</span>';

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



function showFavoriteToast(payload) {

    const container = document.getElementById('toastContainer');

    if (!container) return;

    const bankName = payload.bank || payload.detail || '  ';

    const toastEl = document.createElement('div');

    toastEl.className = 'toast align-items-center text-bg-warning border-0 mb-2';

    toastEl.setAttribute('role', 'alert');

    toastEl.setAttribute('aria-live', 'assertive');

    toastEl.setAttribute('aria-atomic', 'true');

    toastEl.innerHTML = `

        <div class="d-flex">

            <div class="toast-body">

                     : ${bankName}

            </div>

            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>

        </div>

    `;

    container.appendChild(toastEl);

    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });

    toast.show();

    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());

}



function playAlertSound() {

    try {

        const ctx = new (window.AudioContext || window.webkitAudioContext)();

        const oscillator = ctx.createOscillator();

        const gain = ctx.createGain();

        oscillator.type = 'sine';

        oscillator.frequency.value = 880;

        gain.gain.value = 0.2;

        oscillator.connect(gain);

        gain.connect(ctx.destination);

        oscillator.start();

        setTimeout(() => {

            oscillator.stop();

            ctx.close();

        }, 300);

    } catch (e) {

        console.warn('Audio alert blocked', e);

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

        success: '#4caf50',

        warning: '#ffb300',

        error: '#ef5350',

        info: '#64b5f6',

        stopped: '#f44336',

        stopping: '#ffa726',

        registering: '#42a5f5',

        selecting: '#7e57c2',

        'waiting sms': '#ff9800'

    };

    return palette[level.toLowerCase()] || '#cfd8dc';

}

