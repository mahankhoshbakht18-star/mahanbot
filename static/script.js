const API_URL = "http://127.0.0.1:8000";
const WS_URL = `${API_URL.replace("http", "ws")}/ws`;
const MESSAGES = {
    saveSuccess: "اطلاعات با موفقیت ذخیره شد.",
    saveError: "ذخیره اطلاعات با خطا مواجه شد.",
    otpSent: "پیامک با موفقیت ثبت شد.",
    otpFailed: "ثبت پیامک ناموفق بود.",
    startRegister: "ربات ثبت نام در صف اجرا قرار گرفت.",
    startSelect: "ربات انتخاب بانک در صف اجرا قرار گرفت.",
    startStatus: "استعلام وضعیت در صف اجرا قرار گرفت.",
    stopRequested: "درخواست توقف ثبت شد.",
    missingTracking: "کد رهگیری برای این کاربر ثبت نشده است.",
    connectionError: "خطا در ارتباط با سرور.",
    defaultError: "عملیات ناموفق بود.",
    recoverUnavailable: "بخش بازیابی هنوز فعال نیست.",
    requiredFields: "نام و کد ملی الزامی است.",
    selectBank: "لطفا نام بانک را انتخاب کنید.",
    settingsSaved: "تنظیمات با موفقیت ذخیره شد.",
};
let activeBankList = [];
let selectedNidForBank = null;
let dashboardInterval = null;
let editingUserId = null;
let allUsersData = [];
let eventsBuffer = [];
let jobsCache = [];
let wsConnection = null;

document.addEventListener("DOMContentLoaded", () => {
    switchView('dashboard');
    startClock();
    initLogFilters();
    startWebSocket();
    if(document.getElementById('otpInput')){
        document.getElementById('otpInput').addEventListener('keypress', function (e) {
            if (e.key === 'Enter') sendOtp(this.value);
        });
    }
});

function startClock() {
    setInterval(() => {
        const el = document.getElementById('clock');
        if(el) el.innerText = new Date().toLocaleTimeString('fa-IR');
    }, 1000);
}

function switchView(viewId, navEl) {
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
    if(viewId === 'settings') loadSettings(); // لود تنظیمات
    
    if(viewId === 'add' && !editingUserId) resetAddForm();
}

// --- توابع کمکی ---
function parseUserData(userData) {
    if (!userData) return {};
    if (typeof userData === 'object') return userData;
    try { return JSON.parse(userData); } 
    catch (e) { return {}; }
}

function startWebSocket() {
    if (wsConnection) wsConnection.close();
    wsConnection = new WebSocket(WS_URL);
    wsConnection.onmessage = (event) => {
        try {
            const payload = JSON.parse(event.data);
            handleEvent(payload);
        } catch (e) {
            console.warn('Invalid event payload', e);
        }
    };
    wsConnection.onclose = () => {
        setTimeout(startWebSocket, 2000);
    };
}

function handleEvent(event) {
    eventsBuffer.push(event);
    if (eventsBuffer.length > 300) eventsBuffer.shift();
    if (event.type === 'job_status') {
        const updated = jobsCache.find(job => job.job_id === event.job_id);
        if (updated) {
            updated.state = event.meta?.state || updated.state;
        } else if (event.job_id) {
            jobsCache.push({
                job_id: event.job_id,
                bot_name: event.bot || '-',
                nid: event.nid || '-',
                applicant_name: event.meta?.applicant_name || '-',
                state: event.meta?.state || 'QUEUED',
                created_at: event.ts,
                started_at: event.meta?.started_at || null,
                finished_at: event.meta?.finished_at || null,
                last_error: null,
            });
        }
        renderJobCards(jobsCache);
    }
    if (event.type === 'log') {
        appendLogIfMatch(event);
    }
    refreshFilterOptions();
}

function initLogFilters() {
    ['logLevelFilter', 'logBotFilter', 'logNidFilter', 'logJobFilter'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', renderFilteredLogs);
    });
}

function clearLogs() {
    eventsBuffer = [];
    const term = document.getElementById('terminalBox');
    if (term) term.innerHTML = '<div class="text-muted">> لاگ‌ها پاک شد.</div>';
}

async function fetchDashboardData() {
    try {
        const [statsRes, jobsRes] = await Promise.all([
            fetch(`${API_URL}/stats`),
            fetch(`${API_URL}/jobs`),
        ]);
        const stats = await statsRes.json();
        const jobs = await jobsRes.json();
        jobsCache = jobs;
        renderDashboard(stats, jobs);
        updateJobFilters(jobs);
        if(document.getElementById('connectionStatus')){
            document.getElementById('connectionStatus').className = "badge bg-success";
            document.getElementById('connectionStatus').innerText = "سرور متصل";
        }
    } catch (err) {
        if(document.getElementById('connectionStatus')){
            document.getElementById('connectionStatus').className = "badge bg-danger";
            document.getElementById('connectionStatus').innerText = "قطع ارتباط";
        }
    }
}

function renderDashboard(stats, jobs) {
    const tbody = document.getElementById('activeBotsBody');
    if(!tbody) return;
    tbody.innerHTML = '';
    let runningNid = null;
    const activeJobs = jobs.filter(job => ['QUEUED', 'RUNNING'].includes(job.state));

    activeJobs.forEach(job => {
        if (!runningNid && job.nid) runningNid = job.nid;
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${job.applicant_name || '---'}</strong><br><small>${job.nid}</small></td>
            <td><span class="badge ${getJobBadge(job.state)}">${translateJobState(job.state)}</span></td>
            <td><button class="btn btn-sm btn-danger rounded-circle" onclick="stopBot('${job.nid}')"><i class="fas fa-power-off"></i></button></td>
        `;
        tbody.appendChild(tr);
    });

    if(activeJobs.length===0) tbody.innerHTML = `<tr><td colspan="3" class="text-muted small py-3">غیرفعال</td></tr>`;
    window.currentActiveBotNid = runningNid;

    if(document.getElementById('stat-total')) document.getElementById('stat-total').innerText = stats.applicants || 0;
    if(document.getElementById('stat-active')) document.getElementById('stat-active').innerText = stats.active_jobs || 0;
    if(document.getElementById('stat-success')) document.getElementById('stat-success').innerText = stats.success_count || 0;
    if(document.getElementById('stat-codes')) document.getElementById('stat-codes').innerText = stats.tracking_codes || 0;

    renderJobCards(jobs);
}

function logToTerminal(event) {
    if (!event || !event.message) return;
    const term = document.getElementById('terminalBox');
    if(!term) return;
    const div = document.createElement('div');
    const level = (event.level || 'INFO').toLowerCase();
    const levelClass = `log-level-${level}`;
    const ts = event.ts ? new Date(event.ts).toLocaleTimeString('fa-IR') : new Date().toLocaleTimeString('fa-IR');
    div.className = levelClass;
    div.innerHTML = `<span style="color:#666">[${ts}]</span> <span style="color:#00e5ff">${event.nid || '-'}</span> <span>${event.message}</span>`;
    term.appendChild(div);
    term.scrollTop = term.scrollHeight;
}

function appendLogIfMatch(event) {
    if (!matchesFilters(event)) return;
    logToTerminal(event);
}

function renderFilteredLogs() {
    const term = document.getElementById('terminalBox');
    if(!term) return;
    term.innerHTML = '';
    eventsBuffer.filter(event => event.type === 'log').forEach(event => {
        if (matchesFilters(event)) logToTerminal(event);
    });
}

function matchesFilters(event) {
    const level = document.getElementById('logLevelFilter')?.value || '';
    const bot = document.getElementById('logBotFilter')?.value || '';
    const nid = document.getElementById('logNidFilter')?.value || '';
    const job = document.getElementById('logJobFilter')?.value || '';

    if (level && (event.level || '').toUpperCase() !== level) return false;
    if (bot && event.bot !== bot) return false;
    if (nid && event.nid !== nid) return false;
    if (job && event.job_id !== job) return false;
    return true;
}

function refreshFilterOptions() {
    const bots = new Set();
    const nids = new Set();
    const jobIds = new Set();
    eventsBuffer.forEach(event => {
        if (event.bot) bots.add(event.bot);
        if (event.nid) nids.add(event.nid);
        if (event.job_id) jobIds.add(event.job_id);
    });
    updateSelectOptions('logBotFilter', bots, 'همه ربات‌ها');
    updateSelectOptions('logNidFilter', nids, 'همه کدهای ملی');
    updateSelectOptions('logJobFilter', jobIds, 'همه Jobها');
}

function updateSelectOptions(selectId, values, placeholder) {
    const select = document.getElementById(selectId);
    if (!select) return;
    const current = select.value;
    select.innerHTML = `<option value="">${placeholder}</option>`;
    Array.from(values).sort().forEach(value => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = value;
        select.appendChild(opt);
    });
    if (current && values.has(current)) select.value = current;
}

function updateJobFilters(jobs) {
    const bots = new Set();
    const nids = new Set();
    const jobIds = new Set();
    jobs.forEach(job => {
        bots.add(job.bot_name);
        nids.add(job.nid);
        jobIds.add(job.job_id);
    });
    updateSelectOptions('logBotFilter', bots, 'همه ربات‌ها');
    updateSelectOptions('logNidFilter', nids, 'همه کدهای ملی');
    updateSelectOptions('logJobFilter', jobIds, 'همه Jobها');
}

function renderJobCards(jobs) {
    const container = document.getElementById('jobCards');
    const summary = document.getElementById('jobSummaryBadge');
    if (!container) return;
    container.innerHTML = '';
    const sorted = [...jobs].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    const activeCount = jobs.filter(job => ['QUEUED', 'RUNNING'].includes(job.state)).length;
    if (summary) summary.textContent = activeCount ? `${activeCount} وظیفه فعال` : 'بدون وظیفه فعال';
    sorted.slice(0, 8).forEach(job => {
        const card = document.createElement('div');
        const progress = getJobProgress(job.state);
        card.className = 'job-card';
        card.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
                <h6>${job.applicant_name || 'متقاضی ناشناس'}</h6>
                <span class="job-status-badge job-status-${job.state}">${translateJobState(job.state)}</span>
            </div>
            <div class="job-meta">${job.bot_name} • ${job.nid}</div>
            <div class="job-meta">${job.started_at ? `شروع: ${formatTime(job.started_at)}` : 'در انتظار اجرا'}</div>
            ${job.last_error ? `<div class="text-danger small mt-2">${job.last_error}</div>` : ''}
            <div class="job-progress"><span style="width:${progress}%; background:${getProgressColor(job.state)};"></span></div>
        `;
        container.appendChild(card);
    });
    if (!sorted.length) {
        container.innerHTML = '<div class="text-muted">هیچ وظیفه‌ای ثبت نشده است.</div>';
    }
}

function formatTime(value) {
    try { return new Date(value).toLocaleTimeString('fa-IR'); } catch { return value; }
}

function getJobProgress(state) {
    if (state === 'QUEUED') return 20;
    if (state === 'RUNNING') return 60;
    return 100;
}

function getProgressColor(state) {
    if (state === 'FAILED') return '#ef4444';
    if (state === 'CANCELED') return '#94a3b8';
    if (state === 'SUCCEEDED') return '#22c55e';
    if (state === 'RUNNING') return '#6366f1';
    return '#0ea5e9';
}

async function sendOtp(code) {
    const nid = window.currentActiveBotNid;
    if (!nid) return;
    document.getElementById('otpStatus').innerHTML = '...';
    try {
        await apiCall(`/receive_sms`, 'POST', {nid: nid, code: code});
        document.getElementById('otpStatus').innerHTML = '<span class="text-success">ارسال شد</span>';
        document.getElementById('otpInput').value = "";
        alert(MESSAGES.otpSent);
    } catch (err) {
        document.getElementById('otpStatus').innerHTML = '<span class="text-danger">ناموفق</span>';
        alert(err?.message || MESSAGES.otpFailed);
    }
}

async function fetchApplicants(mode) {
    try {
        const res = await fetch(`${API_URL}/applicants`);
        const data = await res.json();
        allUsersData = data;

        if(mode === 'list') renderMainList(data);
        else if(mode === 'bank-select') renderSimpleList(data, 'bankSelectListBody', 'openBankModal', 'انتخاب بانک', 'btn-primary');
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
                <td>
                    <button class="btn btn-sm btn-info text-white shadow-sm" onclick="actionViewStatus('${user.national_id}')">
                        <i class="fas fa-search me-1"></i> استعلام
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
                <td><button class="btn btn-sm btn-outline-success rounded-pill px-3" onclick="startRegister('${user.national_id}')"><i class="fas fa-play me-1"></i>شروع</button></td>
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

async function actionViewStatus(nid) {
    const user = allUsersData.find(u => u.national_id === nid);
    if(user) {
        let d = parseUserData(user.data);
        if(!d.tracking_code) {
            alert(MESSAGES.missingTracking);
            return;
        }
    }
    try {
        const res = await apiCall(`/bot/action/view-status/${nid}`, 'POST');
        if(res.status === 'started') {
            alert(MESSAGES.startStatus);
            switchView('dashboard');
        } else {
            alert(res.message || MESSAGES.defaultError);
        }
    } catch(e) { alert(e?.message || MESSAGES.connectionError); }
}

function actionRecover(nid) { alert(MESSAGES.recoverUnavailable); }
function actionDeleteReq(nid) { if(confirm("آیا مطمئن هستید؟")) apiCall(`/bot/action/delete-request/${nid}`, 'POST'); }

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

    if(!nid || !name) return alert(MESSAGES.requiredFields);

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

    try {
        await apiCall('/applicants', 'POST', payload);
        alert(MESSAGES.saveSuccess);
        if(editingUserId) resetAddForm();
        switchView('list');
    } catch (err) {
        alert(err?.message || MESSAGES.saveError);
    }
}

function addBankPriority() { 
    const n = document.getElementById('inpBankName').value;
    const c = document.getElementById('inpBranch').value; 
    if(n){
        activeBankList.push({name:n, branch:c}); 
        document.getElementById('inpBankName').value=''; 
        document.getElementById('inpBranch').value=''; 
        renderPriorityList();
    } else { alert(MESSAGES.selectBank); }
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
    
    try {
        await apiCall('/settings', 'POST', payload);
        alert(MESSAGES.settingsSaved);
    } catch (err) {
        alert(err?.message || MESSAGES.defaultError);
    }
}

// توابع کمکی دیگر
function translateStatus(s) { if(!s) return 'آماده'; s=s.toLowerCase(); if(s.includes('run')) return 'اجرا'; if(s.includes('wait')) return 'منتظر پیامک'; if(s.includes('succ')) return 'موفق'; if(s.includes('stop')) return 'متوقف'; return s; }
function getStatusBadge(s) { if(!s) return 'bg-light text-muted'; s=s.toLowerCase(); if(s.includes('succ')) return 'bg-success'; if(s.includes('stop')) return 'bg-danger'; if(s.includes('wait')) return 'bg-warning text-dark'; if(s.includes('run')) return 'bg-primary'; return 'bg-secondary'; }
function translateJobState(state) {
    const map = {
        QUEUED: 'در صف',
        RUNNING: 'در حال اجرا',
        SUCCEEDED: 'موفق',
        FAILED: 'ناموفق',
        CANCELED: 'لغو شده'
    };
    return map[state] || state;
}
function getJobBadge(state) {
    const map = {
        QUEUED: 'bg-info text-dark',
        RUNNING: 'bg-primary',
        SUCCEEDED: 'bg-success',
        FAILED: 'bg-danger',
        CANCELED: 'bg-secondary'
    };
    return map[state] || 'bg-secondary';
}
function openBankModal(nid) { selectedNidForBank = nid; document.getElementById('modalNidDisplay').innerText = nid; new bootstrap.Modal(document.getElementById('bankActionModal')).show(); }
async function confirmBankStart() {
    const t = document.querySelector('input[name="loanType"]:checked').value;
    try {
        await apiCall('/bot/start-select', 'POST', {nid: selectedNidForBank, loan_type: t});
        alert(MESSAGES.startSelect);
        bootstrap.Modal.getInstance(document.getElementById('bankActionModal')).hide();
        switchView('dashboard');
    } catch (err) {
        alert(err?.message || MESSAGES.defaultError);
    }
}
async function startRegister(nid) {
    try {
        await apiCall(`/bot/start-register/${nid}`, 'POST');
        alert(MESSAGES.startRegister);
        switchView('dashboard');
    } catch (err) {
        alert(err?.message || MESSAGES.defaultError);
    }
}
async function stopBot(nid) {
    try {
        await apiCall(`/bot/stop/${nid}`, 'POST');
        alert(MESSAGES.stopRequested);
        setTimeout(fetchDashboardData, 1000);
    } catch (err) {
        alert(err?.message || MESSAGES.defaultError);
    }
}
async function apiCall(u, m, b) {
    const res = await fetch(API_URL+u, {
        method: m,
        headers: {'Content-Type':'application/json'},
        body: b ? JSON.stringify(b) : undefined
    });
    let payload = {};
    try { payload = await res.json(); } catch (e) {}
    if (!res.ok) {
        throw payload;
    }
    return payload;
}
