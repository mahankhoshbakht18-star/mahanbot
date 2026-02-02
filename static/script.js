const API_URL = "http://127.0.0.1:8000";
const MESSAGES = window.MESSAGES_FA || {};
const warnedMessageKeys = new Set();
let activeBankList = [];
let selectedNidForBank = null;
let dashboardInterval = null;
let editingUserId = null;
let allUsersData = [];
let wsClient = null;
let logEvents = [];
let browserProfile = null;
let allowedDomainsState = { env: [], db: [], effective: [] };
let currentViewId = 'dashboard';
const jobIdByNid = new Map();
const LOG_BUFFER_LIMIT = 500;
const logFilters = {
    text: '',
    nid: '',
    jobId: '',
    level: '',
    type: ''
};

document.addEventListener("DOMContentLoaded", () => {
    switchView('dashboard');
    startClock();
    setupLogFilters();
    connectWebSocket();
    loadBrowserProfile();
    if(document.getElementById('otpInput')){
        document.getElementById('otpInput').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') sendOtp(this.value);
        });
    }
});

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
        fetchDashboardData();
        if(!dashboardInterval) dashboardInterval = setInterval(fetchDashboardData, 2000);
    } else {
        if(dashboardInterval) { clearInterval(dashboardInterval); dashboardInterval = null; }
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

// --- توابع کمکی ---
function parseUserData(userData) {
    if (!userData) return {};
    if (typeof userData === 'object') return userData;
    try { return JSON.parse(userData); } 
    catch (e) { return {}; }
}

async function fetchDashboardData() {
    try {
        const res = await fetch(`${API_URL}/applicants`);
        const data = await res.json();
        renderDashboard(data);
        if(document.getElementById('connectionStatus')){
            document.getElementById('connectionStatus').className = "badge bg-success";
            document.getElementById('connectionStatus').innerText = getMessage('connection.connected', 'سرور متصل');
        }
    } catch (err) {
        if(document.getElementById('connectionStatus')){
            document.getElementById('connectionStatus').className = "badge bg-danger";
            document.getElementById('connectionStatus').innerText = getMessage('connection.disconnected', 'قطع ارتباط');
        }
    }
}

function renderDashboard(users) {
    const tbody = document.getElementById('activeBotsBody');
    if(!tbody) return;
    tbody.innerHTML = '';
    let runningCount = 0, successCount = 0, codeCount = 0, runningNid = null;

    users.forEach(user => {
        let d = parseUserData(user.data);
        if(d.tracking_code) codeCount++;
        if(user.status.toLowerCase().includes('success')) successCount++;

        const isActive = ['Running', 'Registering', 'Selecting', 'Waiting SMS'].some(s => user.status.includes(s));
        if (isActive) {
            runningCount++;
            if(user.status.includes('Wait')) runningNid = user.national_id;
            else if (!runningNid) runningNid = user.national_id;

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

    if(runningCount===0) tbody.innerHTML = `<tr><td colspan="3" class="text-muted small py-3">${getMessage('labels.inactive', 'غیرفعال')}</td></tr>`;
    window.currentActiveBotNid = runningNid;

    if(document.getElementById('stat-total')) document.getElementById('stat-total').innerText = users.length;
    if(document.getElementById('stat-active')) document.getElementById('stat-active').innerText = runningCount;
    if(document.getElementById('stat-success')) document.getElementById('stat-success').innerText = successCount;
    if(document.getElementById('stat-codes')) document.getElementById('stat-codes').innerText = codeCount;
}

async function sendOtp(code) {
    const nid = window.currentActiveBotNid;
    if (!nid) return;
    document.getElementById('otpStatus').innerHTML = '...';
    await apiCall(`/receive_sms`, 'POST', {nid: nid, code: code});
    document.getElementById('otpStatus').innerHTML = `<span class="text-success">${getMessage('alerts.otp_sent', 'ارسال شد')}</span>`;
    document.getElementById('otpInput').value = "";
}

async function fetchApplicants(mode) {
    try {
        const res = await fetch(`${API_URL}/applicants`);
        const data = await res.json();
        allUsersData = data;

        if(mode === 'list') renderMainList(data);
        else if(mode === 'bank-select') renderBankSelectList(data);
        else if(mode === 'status') renderStatusList(data);
        else if(mode === 'recover') renderSimpleList(data, 'recoverListBody', 'actionRecover', 'بازیابی کد', 'btn-warning text-dark');
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
                        <i class="fas fa-play me-1"></i> Start Status
                    </button>
                </td>
                <td>
                    <button class="btn btn-sm btn-danger shadow-sm" onclick="stopBot('${user.national_id}')">
                        <i class="fas fa-stop me-1"></i> STOP Status
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
                <td><button class="btn btn-sm btn-outline-success rounded-pill px-3" onclick="startRegister('${user.national_id}')"><i class="fas fa-play me-1"></i>Start Register</button></td>
                <td><button class="btn btn-sm btn-outline-danger rounded-pill px-3" onclick="stopBot('${user.national_id}')"><i class="fas fa-stop me-1"></i>STOP Register</button></td>
            </tr>
        `;
    });
}

function renderBankSelectList(data) {
    const tbody = document.getElementById('bankSelectListBody');
    if(!tbody) return;
    tbody.innerHTML = '';
    data.forEach(user => {
        tbody.innerHTML += `
            <tr>
                <td>${user.full_name}</td>
                <td class="font-monospace">${user.national_id}</td>
                <td><span class="badge ${getStatusBadge(user.status)}">${translateStatus(user.status)}</span></td>
                <td><button class="btn btn-sm btn-primary" onclick="openBankModal('${user.national_id}')">Start Select</button></td>
                <td><button class="btn btn-sm btn-danger" onclick="stopBot('${user.national_id}')">STOP Select</button></td>
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

function actionRecover(nid) { alert(getMessage('alerts.recover_unavailable', 'بخش بازیابی هنوز فعال نیست')); }
function actionDeleteReq(nid) { if(confirm(getMessage('alerts.delete_confirm', 'آیا مطمئن هستید؟'))) apiCall(`/bot/action/delete-request/${nid}`, 'POST'); }

function editUser(idx) {
    const user = allUsersData[idx];
    if(!user) return;
    editingUserId = user.id;
    document.getElementById('formTitle').innerText = `ویرایش: ${user.full_name}`;
    document.getElementById('btnResetForm').style.display = 'block';
    
    let d = parseUserData(user.data); 
    
    document.getElementById('inpName').value = user.full_name || '';
    document.getElementById('inpNid').value = user.national_id || '';
    document.getElementById('inpMobile').value = d.mobile || '';
    document.getElementById('inpSpouse').value = d.spouse || '';
    document.getElementById('inpPhone').value = d.phone || '';
    document.getElementById('inpZip').value = d.zip_code || '';
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
    
    activeBankList = d.banks || [];
    renderPriorityList();
    
    switchView('add');
    document.getElementById('view-add').scrollIntoView({ behavior: 'smooth' });
}

function resetAddForm() {
    editingUserId = null;
    document.getElementById('addForm').reset();
    document.getElementById('formTitle').innerText = "ثبت متقاضی جدید";
    document.getElementById('btnResetForm').style.display = 'none';
    activeBankList = [];
    renderPriorityList();
}

async function submitNewApplicant() {
    const nid = document.getElementById('inpNid').value;
    const name = document.getElementById('inpName').value;

    if(!nid || !name) return alert(getMessage('alerts.required_name_nid', 'نام و کد ملی الزامی است'));

    const formData = {
        mobile: document.getElementById('inpMobile').value,
        spouse: document.getElementById('inpSpouse').value,
        phone: document.getElementById('inpPhone').value,
        zip_code: document.getElementById('inpZip').value,
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
        banks: activeBankList
    };

    const payload = {
        id: editingUserId,
        full_name: name,
        national_id: nid,
        data: formData
    };

    await apiCall('/applicants', 'POST', payload);
    alert(getMessage('alerts.save_success', 'اطلاعات با موفقیت ذخیره شد.'));
    if(editingUserId) resetAddForm();
    switchView('list');
}

function addBankPriority() { 
    const n = document.getElementById('inpBankName').value;
    const c = document.getElementById('inpBranch').value; 
    if(n){
        activeBankList.push({name:n, branch:c}); 
        document.getElementById('inpBankName').value=''; 
        document.getElementById('inpBranch').value=''; 
        renderPriorityList();
    } else { alert(getMessage('alerts.select_bank_required', 'لطفا نام بانک را انتخاب کنید')); }
}

function renderPriorityList() { 
    document.getElementById('priorityList').innerHTML = activeBankList.map((b,i)=>`
        <span class="badge bg-white text-dark border p-2 me-1">
            ${i+1}. ${typeof b==='string'?b:b.name} 
            ${(typeof b==='object' && b.branch) ? `<span class="text-muted small">(${b.branch})</span>` : ''}
            <i onclick="removeBank(${i})" class="fas fa-times text-danger ms-1" style="cursor:pointer"></i>
        </span>
    `).join(''); 
}

function removeBank(i) { activeBankList.splice(i,1); renderPriorityList(); }

// ==========================================
// *** بخش تنظیمات اصلاح شده ***
// ==========================================

async function loadSettings() {
    try {
        const s = await (await fetch(`${API_URL}/settings`)).json();
        
        // تنظیمات عمومی
        document.getElementById('set_captcha').value = s.captcha_delay || 0.1;
        document.getElementById('set_retry').value = s.retry_count || 1000;
        document.getElementById('set_headless').checked = s.headless || false;
        
        // تنظیمات جدید
        document.getElementById('set_captcha_mode').value = s.captcha_mode || 'human'; 
        document.getElementById('set_final_submit').checked = s.final_submit || false;
        
        document.getElementById('set_useproxy').checked = s.use_proxy || false;
        document.getElementById('set_proxylist').value = s.proxy_list || '';

    } catch(e) {}
}

async function loadBrowserProfile() {
    try {
        const profile = await (await fetch(`${API_URL}/settings/browser-profile`)).json();
        browserProfile = profile;
        if(document.getElementById('browserSelect')) {
            document.getElementById('browserSelect').value = profile.browser || 'chromium';
            document.getElementById('browserHeadless').checked = !!profile.headless;
            document.getElementById('browserSlowMo').value = profile.slow_mo_ms ?? 0;
            document.getElementById('browserTimeout').value = profile.timeout_ms ?? 30000;
            document.getElementById('browserViewportWidth').value = profile.viewport?.width ?? 1280;
            document.getElementById('browserViewportHeight').value = profile.viewport?.height ?? 720;
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
        viewport: {
            width: parseInt(document.getElementById('browserViewportWidth').value || '1280', 10),
            height: parseInt(document.getElementById('browserViewportHeight').value || '720', 10)
        },
        user_data_dir: document.getElementById('browserUserDataDir').value || null,
        proxy: document.getElementById('browserProxy').value || null
    };
    const res = await fetch(`${API_URL}/settings/browser-profile`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    if(res.ok) {
        const saved = await res.json();
        browserProfile = saved;
        updateBrowserProfileSummary(saved);
        alert(getMessage('alerts.browser_settings_saved', 'تنظیمات مرورگر ذخیره شد.'));
    } else {
        alert(getMessage('alerts.browser_settings_failed', 'خطا در ذخیره تنظیمات مرورگر.'));
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
    const res = await fetch(`${API_URL}/browser/test-launch`, {method: 'POST'});
    if(res.ok) {
        alert(getMessage('alerts.test_launch_ok', 'Test launch انجام شد.'));
    } else {
        alert(getMessage('alerts.test_launch_failed', 'Test launch با خطا مواجه شد.'));
    }
}

async function saveSettings() {
    // جمع‌آوری تمام فیلدها (قبلاً ناقص بود)
    const payload = {
        captcha_delay: document.getElementById('set_captcha').value,
        retry_count: document.getElementById('set_retry').value,
        headless: document.getElementById('set_headless').checked,
        
        captcha_mode: document.getElementById('set_captcha_mode').value,
        final_submit: document.getElementById('set_final_submit').checked,
        
        use_proxy: document.getElementById('set_useproxy').checked,
        proxy_list: document.getElementById('set_proxylist').value
    };
    
    await apiCall('/settings', 'POST', payload);
    alert(getMessage('alerts.settings_saved', 'تنظیمات با موفقیت ذخیره شد.'));
}

async function loadAllowedDomains() {
    const statusEl = document.getElementById('allowedDomainsStatus');
    if (statusEl) statusEl.textContent = '';
    try {
        const res = await fetch(`${API_URL}/settings/allowed-domains`);
        if (!res.ok) throw new Error('Failed');
        allowedDomainsState = await res.json();
        renderAllowedDomains();
    } catch (e) {
        if (statusEl) {
            statusEl.textContent = getMessage('alerts.allowed_domains_failed', 'خطا در بارگذاری دامنه‌های مجاز.');
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

    renderList(envBox, allowedDomainsState.env || [], 'دامنه‌ای ثبت نشده است.');
    renderList(effectiveBox, allowedDomainsState.effective || [], 'دامنه‌ای ثبت نشده است.');

    if (dbBox) {
        if (!allowedDomainsState.db || !allowedDomainsState.db.length) {
            dbBox.innerHTML = '<span class="text-muted">دامنه‌ای ثبت نشده است.</span>';
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
        statusEl.textContent = getMessage('alerts.allowed_domains_saving', 'در حال ذخیره دامنه‌ها...');
        statusEl.className = 'small text-muted';
    }
    try {
        const res = await fetch(`${API_URL}/settings/allowed-domains`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({domains: allowedDomainsState.db})
        });
        const data = await res.json();
        if (!res.ok) {
            const message = data?.detail?.details?.invalid_domains
                ? `${getMessage('alerts.allowed_domains_invalid', 'دامنه‌های نامعتبر')}: ${data.detail.details.invalid_domains.join(', ')}`
                : getMessage('alerts.allowed_domains_failed', 'خطا در ذخیره دامنه‌های مجاز.');
            if (statusEl) {
                statusEl.textContent = message;
                statusEl.className = 'small text-danger';
            }
            return;
        }
        allowedDomainsState = data;
        renderAllowedDomains();
        if (statusEl) {
            statusEl.textContent = getMessage('alerts.allowed_domains_saved', 'دامنه‌های مجاز ذخیره شد.');
            statusEl.className = 'small text-success';
        }
    } catch (e) {
        if (statusEl) {
            statusEl.textContent = getMessage('alerts.allowed_domains_failed', 'خطا در ذخیره دامنه‌های مجاز.');
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
    resultEl.textContent = getMessage('alerts.allowlist_checking', 'در حال بررسی...');
    resultEl.className = 'small text-muted';
    try {
        const res = await fetch(`${API_URL}/settings/allowed-domains/check`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url})
        });
        const data = await res.json();
        if (!res.ok) {
            resultEl.textContent = getMessage('alerts.allowlist_check_failed', 'خطا در بررسی آدرس.');
            resultEl.className = 'small text-danger';
            return;
        }
        if (data.allowed) {
            resultEl.textContent = getMessage('alerts.allowlist_allowed', 'این آدرس مجاز است.');
            resultEl.className = 'small text-success';
        } else {
            resultEl.textContent = getMessage('alerts.allowlist_blocked', 'این آدرس در فهرست مجاز نیست.');
            resultEl.className = 'small text-warning';
        }
    } catch (e) {
        resultEl.textContent = getMessage('alerts.allowlist_check_failed', 'خطا در بررسی آدرس.');
        resultEl.className = 'small text-danger';
    }
}

// توابع کمکی دیگر
function translateStatus(s) {
    const fallback = getMessage('status.ready', 'آماده');
    if(!s) return fallback;
    s = s.toLowerCase();
    if(s.includes('run')) return getMessage('status.running', 'اجرا');
    if(s.includes('wait')) return getMessage('status.waiting_sms', 'منتظر پیامک');
    if(s.includes('succ')) return getMessage('status.success', 'موفق');
    if(s.includes('stop')) return getMessage('status.stopped', 'متوقف');
    return s;
}
function getStatusBadge(s) { if(!s) return 'bg-light text-muted'; s=s.toLowerCase(); if(s.includes('succ')) return 'bg-success'; if(s.includes('stop')) return 'bg-danger'; if(s.includes('wait')) return 'bg-warning text-dark'; if(s.includes('run')) return 'bg-primary'; return 'bg-secondary'; }
function openBankModal(nid) {
    selectedNidForBank = nid;
    document.getElementById('modalNidDisplay').innerText = nid;
    const overrideSelect = document.getElementById('runBrowserOverride');
    if (overrideSelect) overrideSelect.value = '';
    new bootstrap.Modal(document.getElementById('bankActionModal')).show();
}
async function confirmBankStart() {
    const t = document.querySelector('input[name="loanType"]:checked').value;
    const override = document.getElementById('runBrowserOverride')?.value;
    const payload = {bot_name: 'select', nid: selectedNidForBank, loan_type: t};
    if (override) {
        payload.browser_profile_override = {browser: override};
    }
    await apiCall('/jobs/start', 'POST', payload);
    setUserStatus(selectedNidForBank, 'Running');
    bootstrap.Modal.getInstance(document.getElementById('bankActionModal')).hide();
    switchView('dashboard');
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
async function apiCall(u, m, b) { return (await fetch(API_URL+u, {method:m, headers:{'Content-Type':'application/json'}, body:JSON.stringify(b)})).json(); }

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
    const wsUrl = API_URL.replace('http', 'ws') + '/ws';
    wsClient = new WebSocket(wsUrl);

    wsClient.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            handleSocketEvent(payload);
        } catch (e) {
            console.warn('Invalid WS payload', e);
        }
    };

    wsClient.onclose = () => {
        setTimeout(connectWebSocket, 2000);
    };
}

function handleSocketEvent(payload) {
    if (!payload || !payload.type) return;
    trackJobForNid(payload);
    logEvents.push(payload);
    if (logEvents.length > LOG_BUFFER_LIMIT) {
        logEvents.shift();
    }
    renderLogs();
}

function trackJobForNid(payload) {
    const nid = payload.nid;
    const jobId = payload.job_id;
    if (!nid || !jobId) return;
    jobIdByNid.set(nid, jobId);
    if (payload.type === 'job_status') {
        const status = (payload.status || '').toLowerCase();
        if (['stopped', 'cancelled', 'failed'].includes(status)) {
            jobIdByNid.delete(nid);
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
        term.innerHTML = '<div class="text-muted">رویدادی یافت نشد.</div>';
        return;
    }

    filtered.forEach((event) => {
        const div = document.createElement('div');
        const timeText = event.ts ? new Date(event.ts * 1000).toLocaleTimeString('fa-IR') : '';
        const nidLabel = event.nid ? `<span style="color:#00e5ff">${event.nid}</span>` : '<span style="color:#00e5ff">system</span>';
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
