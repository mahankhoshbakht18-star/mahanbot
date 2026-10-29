'use strict';
const byId = id => document.getElementById(id);
async function api(path, body, raw = false) {
  const headers = {'X-MAHAN-ADMIN':'1'};
  if (body !== undefined) headers['Content-Type'] = raw ? 'application/vnd.android.package-archive' : 'application/json';
  const response = await fetch('/api/app-admin/' + path, {method:body === undefined?'GET':'POST',headers,
    body:body === undefined?undefined:(raw?body:JSON.stringify(body)),credentials:'same-origin'});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string'?data.detail:'درخواست پذیرفته نشد؛ اطلاعات و دسترسی را بررسی کنید.');
  return data;
}
function cell(row, value) {const td=document.createElement('td');td.textContent=String(value??'—');row.append(td);return td;}
function action(parent, title, callback, danger=false) {const b=document.createElement('button');b.textContent=title;if(danger)b.className='danger';b.onclick=async()=>{try{b.disabled=true;await callback();await refresh();}catch(e){byId('status').textContent=e.message;}finally{b.disabled=false;}};parent.append(b);}
const date = value => value ? new Date(typeof value==='number'?value*1000:value).toLocaleString('fa-IR'):'—';
async function refresh(){
 const data=await api('overview'); byId('latest').textContent=data.latest_version_code||'هنوز منتشر نشده';byId('devices').textContent=data.registered_devices;byId('outdated').textContent=data.outdated_devices;
 byId('releases').replaceChildren();for(const r of data.releases){const m=JSON.parse(r.metadata),row=document.createElement('tr');cell(row,m.version_name+' / '+r.version_code);cell(row,r.channel+(r.enabled?' · فعال':' · متوقف/پیش‌نویس'));cell(row,r.sha256);cell(row,date(r.published_at));const buttons=cell(row,'');
 action(buttons,r.channel==='draft'?'انتشار آزمایشی':'بررسی انتشار عمومی',()=>approve(r));
 if(r.enabled){action(buttons,'اطلاع‌رسانی مجاز',async()=>{if(confirm('اعلان برای نصب‌های ثبت‌شده و موافق در صف بررسی دوره‌ای قرار بگیرد؟')){const result=await api('releases/'+r.version_code+'/notify',{});byId('status').textContent='در انتظار بررسی دوره‌ای: '+result.queued_for_poll+'؛ Push ارسال نشده است.';}});action(buttons,'توقف توزیع',async()=>{if(confirm('دانلود و توزیع این نسخه متوقف شود؟'))await api('releases/'+r.version_code+'/stop',{});},true);}byId('releases').append(row);}
 byId('deviceRows').replaceChildren();for(const d of data.devices){const row=document.createElement('tr');cell(row,d.device_id);cell(row,d.version_name+' / '+d.version_code);cell(row,date(d.last_seen));cell(row,d.update_state);cell(row,d.notifications?'موافق':'خاموش');byId('deviceRows').append(row);}
 byId('notices').replaceChildren();for(const n of data.notices){const p=document.createElement('p');p.textContent='Build '+n.version_code+' · '+n.state+' · '+n.count;byId('notices').append(p);}
 byId('audit').replaceChildren();for(const a of data.history){const p=document.createElement('p');p.textContent=date(a.at)+' · '+a.action+' · Build '+a.version_code;byId('audit').append(p);}
}
byId('releaseForm').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{
 byId('status').textContent='در حال بارگذاری و بررسی فایل…';const verified=await api('upload',byId('apk').files[0],true);
 await api('releases',{sha256:verified.sha256,version_code:verified.version_code,version_name:verified.version_name,min_supported_version_code:Number(byId('minimum').value),release_notes:byId('notes').value,update_priority:byId('priority').value});
 byId('status').textContent='پیش‌نویس ثبت شد؛ هنوز عمومی نیست. SHA-256: '+verified.sha256;await refresh();
}catch(e){byId('status').textContent=e.message;}finally{button.disabled=false;}};
async function approve(release){const channel=release.channel==='draft'?'pilot':'production';byId('approvalText').textContent=channel==='pilot'?'فقط نصب‌های آزمایشی فهرست‌شده مجاز به دریافت می‌شوند.':'این اقدام فایل را عمومی می‌کند. پس از بررسی نتیجه آزمایش و تأیید مالک انجام دهید.';byId('pilotIds').disabled=channel==='production';byId('testReport').value='';byId('approval').showModal();byId('approveButton').onclick=async()=>{try{
 if(byId('testReport').value.trim().length<10)throw new Error('گزارش واقعی آزمون را وارد کنید.');
 if(!confirm(channel==='production'?'انتشار عمومی این نسخه را صریحاً تأیید می‌کنید؟':'انتشار برای همین گروه آزمایشی تأیید می‌شود؟'))return;
 await api('releases/'+release.version_code+'/approve',{channel,approval:channel==='pilot'?'APPROVE_PILOT':'APPROVE_PRODUCTION',test_report:byId('testReport').value,pilot_device_ids:byId('pilotIds').value.split(/\s+/).filter(Boolean)});byId('approval').close();await refresh();
 }catch(e){byId('status').textContent=e.message;}};}
refresh().catch(e=>{byId('status').textContent=e.message;});
