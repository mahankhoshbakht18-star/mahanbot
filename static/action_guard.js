(() => {
  'use strict';

  function messageFrom(error) {
    return String(error?.message || error || 'خطای ناشناخته');
  }

  function normalizeDigits(value) {
    return String(value ?? '')
      .replace(/[۰-۹]/g, (char) => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(char)))
      .replace(/[٠-٩]/g, (char) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(char)));
  }

  function validateApplicantPayload(body) {
    if (!body || typeof body !== 'object') throw new Error('اطلاعات متقاضی نامعتبر است.');
    const fullName = String(body.full_name || '').trim();
    const nationalId = normalizeDigits(body.national_id).replace(/\D/g, '');
    if (fullName.length < 2) throw new Error('نام و نام خانوادگی را کامل وارد کنید.');
    if (!/^\d{10}$/.test(nationalId)) throw new Error('کد ملی باید دقیقاً ۱۰ رقم باشد.');
    body.full_name = fullName;
    body.national_id = nationalId;
  }

  function installApiResultGuard() {
    if (typeof apiCall !== 'function' || apiCall.__mahanActionGuard) return;
    const baseApiCall = apiCall;
    const guarded = async function guardedApiCall(path, method, body) {
      if (path === '/applicants' && String(method || '').toUpperCase() === 'POST') {
        validateApplicantPayload(body);
      }
      const payload = await baseApiCall(path, method, body);
      if (payload && String(payload.status || '').toLowerCase() === 'error') {
        throw new Error(payload.msg || payload.message || 'عملیات در سرور انجام نشد.');
      }
      return payload;
    };
    guarded.__mahanActionGuard = true;
    apiCall = guarded;
  }

  function installApplicantSubmitGuard() {
    if (typeof submitNewApplicant !== 'function' || submitNewApplicant.__mahanActionGuard) return;
    const originalSubmit = submitNewApplicant;
    const guarded = async function guardedSubmitNewApplicant() {
      try {
        return await originalSubmit();
      } catch (error) {
        console.error('Applicant save failed:', error);
        alert(`ذخیره متقاضی انجام نشد: ${messageFrom(error)}`);
        return null;
      }
    };
    guarded.__mahanActionGuard = true;
    submitNewApplicant = guarded;
  }

  function installStopGuard() {
    if (typeof stopBot !== 'function' || stopBot.__mahanActionGuard) return;
    const guarded = async function guardedStopBot(nid) {
      const normalizedNid = String(nid || '').trim();
      if (!normalizedNid) return;
      let previousStatus = null;
      try {
        if (typeof allUsersData !== 'undefined' && Array.isArray(allUsersData)) {
          previousStatus = allUsersData.find((item) => item.national_id === normalizedNid)?.status ?? null;
        }
        const jobId = typeof jobIdByNid !== 'undefined' ? jobIdByNid.get(normalizedNid) : null;
        if (jobId) {
          await apiCall(`/jobs/cancel/${encodeURIComponent(jobId)}`, 'POST');
          jobIdByNid.delete(normalizedNid);
        } else {
          await apiCall(`/bot/stop/${encodeURIComponent(normalizedNid)}`, 'POST');
        }
        if (typeof setUserStatus === 'function') setUserStatus(normalizedNid, 'Stopped');
        if (typeof refreshCurrentView === 'function') refreshCurrentView();
        if (typeof markDashboardStopped === 'function') markDashboardStopped(normalizedNid);
        window.setTimeout(() => {
          if (typeof fetchDashboardData === 'function') fetchDashboardData();
        }, 500);
      } catch (error) {
        if (previousStatus && typeof setUserStatus === 'function') setUserStatus(normalizedNid, previousStatus);
        if (typeof refreshCurrentView === 'function') refreshCurrentView();
        console.error('Stop request failed:', error);
        alert(`توقف عملیات تأیید نشد: ${messageFrom(error)}`);
      }
    };
    guarded.__mahanActionGuard = true;
    stopBot = guarded;
  }

  function init() {
    installApiResultGuard();
    installApplicantSubmitGuard();
    installStopGuard();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
