/* Licensing UI: decisions come from the backend, never localStorage. */
(() => {
    let state = {}, refreshing = false;
    const el = id => document.getElementById(id);
    const labels = {administrator:'نسخه مدیر',active:'فعال',expired:'منقضی‌شده',revoked:'غیرفعال توسط مدیر',
        not_activated:'نیاز به فعال‌سازی',invalid:'کلید نامعتبر',device_limit:'ظرفیت دستگاه تکمیل',
        device_mismatch:'دستگاه تأیید نشد',network_error:'خطای اتصال به سرور',invalid_response:'پاسخ نامعتبر سرور',
        rate_limited:'محدودیت درخواست',configuration_error:'تنظیم اتصال لازم است',server_error:'خطای سرور'};
    const date = value => value ? new Date(value * 1000).toLocaleString('fa-IR') : '—';
    function message(text='',failed=false) { el('licenseMessage').textContent=text; el('licenseMessage').classList.toggle('text-danger',failed); }
    async function api(path,method='GET',body) {
        const r = await fetch('/api/license'+path,{method,headers:{'Content-Type':'application/json'},
            ...(body===undefined ? {} : {body:JSON.stringify(body)})});
        const data=await r.json();
        if(!r.ok) throw new Error(typeof data.detail==='string' ? data.detail : data.detail?.message || 'درخواست مدیریت لایسنس موفق نبود.');
        return data;
    }
    async function refresh(force=false) {
        if(refreshing) return; refreshing=true;
        try {
            state=await api('/status'+(force?'?force=true':''));
            el('licenseState').textContent=labels[state.state] || 'در حال بررسی';
            el('licenseState').className='badge '+(state.valid?'bg-success':'bg-warning text-dark');
            el('licenseQuickStatus').textContent='لایسنس: '+(labels[state.state] || 'بررسی');
            el('licenseExpiry').textContent=date(state.expires_at);
            el('licenseCustomer').textContent=state.customer || '—';
            el('licenseInstallId').textContent=state.installation_id || 'نسخه مدیر';
            el('licenseActivationForm').hidden=!state.required;
            el('licenseRuntimeNotice').hidden=!state.required || state.valid;
            if(state.required && !state.valid) message(state.message || 'برای ادامه اجرای بات، اعتبار لایسنس باید تأیید شود.',true);
        } catch(e) { message(e.message,true); } finally { refreshing=false; }
    }
    async function activate(event) {
        event.preventDefault(); const btn=el('activateLicenseButton'); btn.disabled=true;
        try {
            await api('/activate','POST',{license_key:el('licenseActivationKey').value.trim()});
            el('licenseActivationKey').value='';
            message('فعال شد؛ برای ادامه متقاضی متوقف‌شده، دکمه ادامه همان متقاضی را بزنید.');
            await refresh(true);
        } catch(e) { message(e.message,true); } finally { btn.disabled=false; }
    }
    function cell(row,text) { const td=document.createElement('td');td.textContent=text;row.appendChild(td);return td; }
    function button(parent,title,callback) {
        const btn=document.createElement('button');btn.type='button';btn.className='btn btn-sm btn-outline-primary';
        btn.textContent=title;btn.addEventListener('click',()=>callback(btn));parent.appendChild(btn);return btn;
    }
    async function action(btn,path,method,payload) {
        btn.disabled=true;
        try { await api(path,method,payload);message('تغییر روی سرور مرکزی ذخیره شد.');await loadManager(); }
        catch(e) { message(e.message,true); } finally { btn.disabled=false; }
    }
    async function showDevices(license) {
        try {
            const devices=await api(`/admin/licenses/${encodeURIComponent(license.id)}/devices`);
            const box=el('licenseDevices');box.replaceChildren();box.hidden=false;
            const title=document.createElement('h4');title.textContent='دستگاه‌های '+license.customer;box.appendChild(title);
            for(const device of devices) {
                const row=document.createElement('div');row.className='d-flex gap-2 flex-wrap align-items-center my-2';
                const text=document.createElement('span');
                text.textContent=`${device.installation_id.slice(0,12)}… · آخرین بررسی: ${date(device.last_seen)} · ${device.active?'فعال':'آزادشده'}`;
                row.appendChild(text);
                if(device.active) button(row,'آزاد کردن دستگاه',async btn=>{
                    if(!confirm('دسترسی این دستگاه قطع و ظرفیت آن آزاد شود؟')) return;
                    await action(btn,`/admin/licenses/${encodeURIComponent(license.id)}/devices/${encodeURIComponent(device.id)}`,'DELETE');
                    await showDevices(license);
                });
                box.appendChild(row);
            }
            if(!devices.length) box.appendChild(document.createTextNode('هنوز دستگاهی فعال نشده است.'));
        } catch(e) { message(e.message,true); }
    }
    async function loadManager() {
        try {
            const r=await fetch('/auth/me');if(!r.ok)return;
            const me=await r.json();const panel=el('centralLicenseManager');
            panel.hidden=!me.is_admin || me.edition==='CLIENT';if(panel.hidden)return;
            const licenses=await api('/admin/licenses');const body=el('licenseRows');body.replaceChildren();
            for(const item of licenses) {
                const row=document.createElement('tr');
                cell(row,item.customer);cell(row,item.key_hint);cell(row,date(item.expires_at));
                cell(row,!item.active?'غیرفعال':item.expires_at*1000<=Date.now()?'منقضی':'فعال');
                cell(row,`${item.devices_used} از ${item.max_devices}`);
                const actions=cell(row,'');actions.className='license-actions';
                const days=document.createElement('input');days.type='number';days.min='1';days.max='3650';days.value='30';
                days.className='form-control';days.setAttribute('aria-label','روز تمدید '+item.customer);actions.appendChild(days);
                button(actions,'تمدید',btn=>{
                    if(!days.checkValidity())return days.reportValidity();
                    return action(btn,`/admin/licenses/${encodeURIComponent(item.id)}/renew`,'POST',{days:Number(days.value)});
                });
                button(actions,item.active?'غیرفعال‌سازی':'فعال‌سازی',btn=>{
                    if(item.active && !confirm('لایسنس این مشتری غیرفعال شود؟'))return;
                    return action(btn,`/admin/licenses/${encodeURIComponent(item.id)}/state`,'PUT',{active:!item.active});
                });
                button(actions,'دستگاه‌ها',()=>showDevices(item));body.appendChild(row);
            }
            el('licenseManagerStatus').textContent=licenses.length?`${licenses.length.toLocaleString('fa-IR')} لایسنس روی سرور مرکزی`:'هنوز لایسنسی صادر نشده است.';
        } catch(e) { el('licenseManagerStatus').textContent=e.message; }
    }
    async function issue(event) {
        event.preventDefault();const btn=el('issueLicenseButton');btn.disabled=true;
        try {
            const result=await api('/admin/licenses','POST',{customer:el('licenseIssueCustomer').value.trim(),
                days:Number(el('licenseIssueDays').value),max_devices:Number(el('licenseIssueDevices').value)});
            el('licenseIssuedKey').value=result.license_key;el('licenseIssuedBox').hidden=false;
            message('کلید صادر شد؛ آن را به مشتری تحویل دهید. کلید کامل در فهرست سرور نگهداری نمی‌شود.');await loadManager();
        } catch(e) { message(e.message,true); } finally { btn.disabled=false; }
    }
    document.addEventListener('DOMContentLoaded',()=>{
        el('licenseActivationForm').addEventListener('submit',activate);
        el('licenseIssueForm').addEventListener('submit',issue);
        el('licenseCheckButton').addEventListener('click',()=>refresh(true));
        el('licenseManagerRefresh').addEventListener('click',loadManager);
        el('licenseCopyKey').addEventListener('click',async()=>{
            try { await navigator.clipboard.writeText(el('licenseIssuedKey').value);message('کلید کپی شد.'); }
            catch(_) { el('licenseIssuedKey').select();message('کلید انتخاب شد؛ با Ctrl+C کپی کنید.'); }
        });
        refresh();loadManager();setInterval(()=>refresh(),10000);
    });
    window.MahanLicense={getState:()=>({...state}),refresh,loadManager};
})();
