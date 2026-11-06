/**
 * static/page3_management.js — Page 3 Official Console & Session Diagnostics
 * Provides UI actions for:
 * - Official controlled refresh & re-entry
 * - Checkpoint saving
 * - Deep read-only analysis of saved HTMLs
 * - Live vs historical bank comparisons (STALE_ONLY detection)
 * - Session diagnostics and failure recovery guides
 */

async function fetchPage3Diagnostics(nid) {
  if (!nid) return null;
  try {
    const res = await fetch(`/api/page3/diagnostics/${encodeURIComponent(nid)}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error("Failed to fetch Page 3 diagnostics:", err);
    return null;
  }
}

async function triggerOfficialPage3Refresh(nid) {
  if (!nid) {
    alert("لطفاً یک متقاضی را برای تازه‌سازی رسمی انتخاب کنید.");
    return;
  }
  const btn = document.getElementById("btnOfficialRefresh");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`/api/page3/refresh-official/${encodeURIComponent(nid)}`, {
      method: "POST"
    });
    const data = await res.json();
    if (data.success) {
      alert("✅ تازه‌سازی رسمی صفحه سوم با موفقیت انجام شد:\n" + (data.message || ""));
    } else {
      alert("⚠️ نتیجه تازه‌سازی رسمی:\n" + (data.message || "خطا در برقراری ارتباط"));
    }
    updatePage3ConsoleUI(nid);
  } catch (err) {
    alert("خطای ارتباط با سرور: " + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function inspectSavedPageModal(filePath) {
  if (!filePath) {
    filePath = prompt("مسیر فایل HTML ذخیره‌شده را وارد کنید:", "saved_pages/");
    if (!filePath) return;
  }
  try {
    const res = await fetch(`/api/page3/analyze-saved?file_path=${encodeURIComponent(filePath)}`);
    if (!res.ok) {
      alert("فایل مورد نظر یافت نشد یا غیرقابل دسترس است.");
      return;
    }
    const report = await res.json();
    let msg = `📋 گزارش تحلیل تخصصی فایل ذخیره‌شده:\n\n`;
    msg += `• نام فایل: ${report.source_path}\n`;
    msg += `• ماهیت منبع: ${report.source_mode}\n`;
    msg += `• صفحه سوم معتبر: ${report.is_page3_select_bnk ? 'بله' : 'خیر'}\n`;
    msg += `• متد فرم: ${report.form ? report.form.method : 'نامشخص'}\n`;
    msg += `• حضور ddlBankName: ${report.controls ? report.controls.ddlBankName_present : false}\n`;
    msg += `• حضور ddlCity: ${report.controls ? report.controls.ddlCity_present : false}\n`;
    msg += `• حضور btnSave: ${report.controls ? report.controls.btnSave_present : false}\n`;
    msg += `• تعداد گزینه‌های بانک: ${report.banks_snapshot ? report.banks_snapshot.total_options : 0}\n`;
    if (report.banks_snapshot && report.banks_snapshot.active_capacity_banks.length) {
      msg += `• بانک‌های دارای ظرفیت فعال: ${report.banks_snapshot.active_capacity_banks.join('، ')}\n`;
    }
    if (report.banks_snapshot && report.banks_snapshot.reserve_banks.length) {
      msg += `• بانک‌های رزرو: ${report.banks_snapshot.reserve_banks.join('، ')}\n`;
    }
    if (report.stale_errors && report.stale_errors.length) {
      msg += `• پیام‌های خطای ذخیره‌شده: ${report.stale_errors.join(' | ')}\n`;
    }
    alert(msg);
  } catch (err) {
    alert("خطا در تحلیل فایل ذخیره‌شده: " + err.message);
  }
}

async function compareBankListsModal(nid) {
  if (!nid) {
    alert("لطفاً کدملی متقاضی را وارد کنید.");
    return;
  }
  try {
    const res = await fetch(`/api/page3/compare-banks/${encodeURIComponent(nid)}`);
    if (!res.ok) {
      alert("اطلاعات متقاضی یافت نشد.");
      return;
    }
    const data = await res.json();
    let msg = `📊 مقایسه فهرست بانک‌های زنده با تاریخچه متقاضی (${nid}):\n\n`;
    msg += `• تعداد بانک‌های زنده استان: ${data.live_banks_count}\n`;
    msg += `• بانک اولویت‌دار متقاضی: ${data.target_bank || 'تنظیم نشده'}\n`;
    if (data.target_classification) {
      msg += `• وضعیت اولویت: ${data.target_classification.status}\n`;
      if (data.target_classification.warning) {
        msg += `⚠️ هشدار: ${data.target_classification.warning}\n`;
      }
    }
    if (data.stale_banks && data.stale_banks.length) {
      msg += `\n⚠️ بانک‌های موجود در سوابق اما غایب در فهرست زنده (STALE_ONLY):\n${data.stale_banks.join('، ')}\n`;
    } else {
      msg += `\n✅ هیچ مغایرت منسوخی (STALE_ONLY) یافت نشد.\n`;
    }
    alert(msg);
  } catch (err) {
    alert("خطا در دریافت مقایسه بانک‌ها: " + err.message);
  }
}

async function updatePage3ConsoleUI(nid) {
  if (!nid) return;
  const diag = await fetchPage3Diagnostics(nid);
  if (!diag) return;

  const badgeEl = document.getElementById("p3SessionBadge");
  const descEl = document.getElementById("p3SessionDesc");
  const alertBox = document.getElementById("p3FailureAlertBox");
  const alertText = document.getElementById("p3FailureReasonText");

  if (badgeEl) {
    badgeEl.textContent = diag.title_fa || diag.status;
    badgeEl.style.backgroundColor = diag.color || "#6B7280";
  }
  if (descEl) {
    descEl.textContent = diag.description || "";
  }
  if (alertBox && alertText) {
    if (diag.status === "SERVER_REJECTED" || diag.status === "OTP_EXPIRED" || diag.status === "MAINTENANCE_ACTIVE") {
      alertBox.style.display = "block";
      alertText.textContent = diag.reason || diag.description || "";
    } else {
      alertBox.style.display = "none";
    }
  }
}
