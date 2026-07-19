(() => {
  "use strict";

  const state = {
    currentNid: "",
    currentName: "",
    finalSubmit: false,
    socket: null,
    reconnectTimer: null,
    audioContext: null,
    latestSmsAt: null,
  };

  const byId = (id) => document.getElementById(id);

  function apiHeaders() {
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    const key =
      sessionStorage.getItem("mahanbot_api_key") ||
      sessionStorage.getItem("mahanbot.apiKey") ||
      sessionStorage.getItem("api_key") ||
      "";
    if (key) headers["X-API-KEY"] = key;
    return headers;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      headers: { ...apiHeaders(), ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }

  function hideLegacySmsUi() {
    const manual = byId("manualOtpInput");
    const manualRow = manual?.closest(".d-flex.align-items-center.gap-2");
    if (manualRow) manualRow.hidden = true;

    const otp = byId("otpInput");
    const otpCard = otp?.closest(".card");
    if (otpCard) otpCard.hidden = true;
  }

  function buildPanel() {
    if (byId("mahanOperationCenter")) return;
    const main = document.querySelector("main.main-content");
    if (!main) return;

    const panel = document.createElement("section");
    panel.id = "mahanOperationCenter";
    panel.setAttribute("aria-label", "مرکز یکپارچه پیامک و عملیات بانکی");
    panel.innerHTML = `
      <div class="moc-head">
        <div>
          <h2><i class="fas fa-layer-group"></i> مرکز پیامک و عملیات بانکی</h2>
          <p>یک محل برای پیامک، بانک منتخب، ثبت نهایی و آرشیو صفحات</p>
        </div>
        <div id="mocActiveApplicant" aria-live="polite">متقاضی فعال: در حال بررسی…</div>
      </div>
      <div class="moc-grid">
        <section class="moc-card" aria-labelledby="mocSmsTitle">
          <h3 id="mocSmsTitle"><i class="fas fa-sms"></i> پیامک</h3>
          <div class="moc-status"><span id="mocSmsDot" class="moc-dot"></span><span id="mocSmsStatus">در حال بررسی اتصال گوشی…</span></div>
          <div class="moc-row">
            <input id="mocOtpInput" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="8" aria-label="کد پیامک" placeholder="کد پیامک">
            <button id="mocOtpSend" class="moc-button primary" type="button">ارسال کد به بات</button>
          </div>
          <div class="moc-row" style="margin-top:9px">
            <button id="mocPhoneSetup" class="moc-button secondary" type="button">تنظیم اتصال گوشی</button>
            <button id="mocRefreshApplicant" class="moc-button secondary" type="button">به‌روزرسانی متقاضی</button>
          </div>
        </section>

        <section class="moc-card" aria-labelledby="mocBankTitle">
          <h3 id="mocBankTitle"><i class="fas fa-university"></i> بانک منتخب و ثبت نهایی</h3>
          <div class="moc-final">
            <div><strong id="mocFinalLabel">ثبت نهایی خاموش است</strong><small>خاموش: فقط اعلان؛ روشن: دکمه ذخیره زده می‌شود</small></div>
            <input id="mocFinalToggle" class="moc-switch" type="checkbox" role="switch" aria-label="فعال‌سازی ثبت نهایی">
          </div>
          <div id="mocBankAlert" class="moc-alert-box" aria-live="assertive">هنوز بانک منتخب مشاهده نشده است.</div>
        </section>

        <section class="moc-card" aria-labelledby="mocArchiveTitle">
          <h3 id="mocArchiveTitle"><i class="fas fa-folder-open"></i> آرشیو صفحات بانک</h3>
          <div class="moc-row">
            <button id="mocArchiveRefresh" class="moc-button success" type="button">نمایش آرشیو متقاضی</button>
            <span id="mocArchiveCount" class="text-muted small">۰ فایل</span>
          </div>
          <div id="mocArchiveList" class="moc-archive-list"></div>
        </section>
      </div>
      <div id="mocPhoneSetupBox" class="moc-setup"></div>
    `;

    const intro = main.querySelector(".mahan-page-intro");
    if (intro?.nextSibling) intro.parentNode.insertBefore(panel, intro.nextSibling);
    else main.prepend(panel);
    document.body.classList.add("mahan-operation-center-ready");
  }

  function setSmsStatus(text, mode = "") {
    const status = byId("mocSmsStatus");
    const dot = byId("mocSmsDot");
    if (status) status.textContent = text;
    if (dot) dot.className = `moc-dot ${mode}`.trim();
  }

  function setBankAlert(text, hot = false) {
    const box = byId("mocBankAlert");
    if (!box) return;
    box.textContent = text;
    box.className = `moc-alert-box${hot ? " hot" : ""}`;
  }

  function speak(text) {
    try {
      if (!("speechSynthesis" in window)) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(String(text || ""));
      utterance.lang = "fa-IR";
      utterance.rate = 0.95;
      window.speechSynthesis.speak(utterance);
    } catch (_) {}
  }

  function beep() {
    try {
      if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const ctx = state.audioContext;
      if (ctx.state === "suspended") ctx.resume();
      const now = ctx.currentTime;
      [0, 0.16, 0.32].forEach((offset, index) => {
        const oscillator = ctx.createOscillator();
        const gain = ctx.createGain();
        oscillator.frequency.value = [660, 880, 1040][index];
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.13, now + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.13);
        oscillator.connect(gain).connect(ctx.destination);
        oscillator.start(now + offset);
        oscillator.stop(now + offset + 0.15);
      });
    } catch (_) {}
  }

  function activeApplicantFromRows(rows) {
    const activeTokens = ["running", "register", "select", "waiting", "awaiting", "submitted"];
    return rows.find((item) => {
      const status = String(item.status || "").toLowerCase();
      return activeTokens.some((token) => status.includes(token));
    }) || rows[0] || null;
  }

  async function refreshApplicant() {
    try {
      const rows = await api("/applicants", { method: "GET", headers: { "Content-Type": undefined } });
      const list = Array.isArray(rows) ? rows : [];
      const user = activeApplicantFromRows(list);
      state.currentNid = String(user?.national_id || "");
      state.currentName = String(user?.full_name || "");
      const label = byId("mocActiveApplicant");
      if (label) {
        label.textContent = user
          ? `متقاضی فعال: ${state.currentName || "بدون نام"} — ${state.currentNid}`
          : "متقاضی فعالی وجود ندارد";
      }
      if (!state.currentNid) setSmsStatus("ابتدا یک متقاضی را اجرا کنید", "wait");
      return user;
    } catch (error) {
      setSmsStatus(`خطا در دریافت متقاضی: ${error}`, "alert");
      return null;
    }
  }

  async function refreshOperationStatus() {
    try {
      const payload = await api("/api/v1/operation-center/status", { method: "GET", headers: { "Content-Type": undefined } });
      state.finalSubmit = Boolean(payload.final_submit);
      const toggle = byId("mocFinalToggle");
      if (toggle) toggle.checked = state.finalSubmit;
      updateFinalLabel();
    } catch (_) {}
  }

  function updateFinalLabel() {
    const label = byId("mocFinalLabel");
    if (!label) return;
    label.textContent = state.finalSubmit ? "ثبت نهایی روشن است" : "ثبت نهایی خاموش است";
    label.style.color = state.finalSubmit ? "#047857" : "#b45309";
  }

  async function setFinalSubmit(enabled) {
    const toggle = byId("mocFinalToggle");
    if (toggle) toggle.disabled = true;
    try {
      const payload = await api("/api/v1/operation-center/final-submit", {
        method: "POST",
        body: JSON.stringify({ enabled: Boolean(enabled) }),
      });
      state.finalSubmit = Boolean(payload.final_submit);
      if (toggle) toggle.checked = state.finalSubmit;
      updateFinalLabel();
      const text = state.finalSubmit
        ? "ثبت نهایی فعال شد؛ Job منتظر اجازه می‌تواند دکمه ذخیره را بزند."
        : "ثبت نهایی غیرفعال شد؛ فقط اعلان بانک و شعبه نمایش داده می‌شود.";
      setBankAlert(text, state.finalSubmit);
      speak(text);
    } catch (error) {
      if (toggle) toggle.checked = state.finalSubmit;
      setBankAlert(`تغییر تنظیم ثبت نهایی ناموفق بود: ${error}`, true);
    } finally {
      if (toggle) toggle.disabled = false;
    }
  }

  async function sendOtp() {
    const input = byId("mocOtpInput");
    const button = byId("mocOtpSend");
    const code = String(input?.value || "").replace(/\D/g, "");
    if (!state.currentNid) {
      setSmsStatus("متقاضی فعال پیدا نشد", "alert");
      return;
    }
    if (!/^\d{4,8}$/.test(code)) {
      setSmsStatus("کد پیامک را با ۴ تا ۸ رقم وارد کنید", "alert");
      input?.focus();
      return;
    }
    if (button) button.disabled = true;
    setSmsStatus("در حال ارسال کد به Job فعال…", "wait");
    try {
      await api("/manual_otp", {
        method: "POST",
        body: JSON.stringify({ nid: state.currentNid, code }),
      });
      if (input) input.value = "";
      setSmsStatus("کد با موفقیت به Job فعال ارسال شد", "ok");
      speak("کد پیامک ارسال شد");
    } catch (error) {
      setSmsStatus(`ارسال کد ناموفق بود: ${error}`, "alert");
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function showPhoneSetup() {
    const box = byId("mocPhoneSetupBox");
    if (!box) return;
    box.classList.add("show");
    box.textContent = "در حال دریافت تنظیمات اتصال گوشی…";
    try {
      const payload = await api("/api/v1/sms/setup", { method: "GET", headers: { "Content-Type": undefined } });
      box.textContent = `Server URL: ${payload.notify_url || "-"}\nHeartbeat URL: ${payload.heartbeat_url || "-"}\nDevice key: ${payload.device_key || "-"}`;
    } catch (error) {
      box.textContent = `تنظیمات اتصال در دسترس نیست: ${error}`;
    }
  }

  function handleSmsArrival(event) {
    state.latestSmsAt = Number(event?.received_at || event?.created_at || Date.now() / 1000);
    const time = new Date(state.latestSmsAt * 1000).toLocaleTimeString("fa-IR");
    setSmsStatus(`پیامک جدید در ساعت ${time} رسید؛ کد را وارد و ارسال کنید`, "ok");
    const input = byId("mocOtpInput");
    input?.focus();
    beep();
    speak("پیامک جدید دریافت شد. کد را وارد کنید.");
  }

  async function refreshArchives() {
    const list = byId("mocArchiveList");
    const count = byId("mocArchiveCount");
    if (!list || !count) return;
    if (!state.currentNid) {
      list.textContent = "متقاضی فعال انتخاب نشده است.";
      count.textContent = "۰ فایل";
      return;
    }
    list.textContent = "در حال دریافت آرشیو…";
    try {
      const payload = await api(`/api/v1/archives/${encodeURIComponent(state.currentNid)}`, {
        method: "GET",
        headers: { "Content-Type": undefined },
      });
      const records = Array.isArray(payload.archives) ? payload.archives : [];
      count.textContent = `${records.length.toLocaleString("fa-IR")} رکورد`;
      list.innerHTML = "";
      if (!records.length) {
        list.textContent = "هنوز صفحه بانکی برای این متقاضی ذخیره نشده است.";
        return;
      }
      records.slice(0, 80).forEach((record) => {
        const item = document.createElement("article");
        item.className = "moc-archive-item";
        const title = document.createElement("strong");
        title.textContent = `${record.bank || "بانک"} — ${record.stage || "صفحه"}`;
        const meta = document.createElement("div");
        meta.className = "text-muted";
        meta.textContent = record.created_at_iso || "";
        const links = document.createElement("div");
        links.className = "moc-archive-links";
        if (record.screenshot_url) {
          const link = document.createElement("a");
          link.href = record.screenshot_url;
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = "تصویر";
          links.appendChild(link);
        }
        if (record.html_url) {
          const link = document.createElement("a");
          link.href = record.html_url;
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = "صفحه HTML";
          links.appendChild(link);
        }
        item.append(title, meta, links);
        list.appendChild(item);
      });
    } catch (error) {
      list.textContent = `دریافت آرشیو ناموفق بود: ${error}`;
    }
  }

  function handleServerEvent(payload) {
    if (!payload?.type) return;
    if (payload.type === "bank_match_found") {
      const text = `بانک منتخب ${payload.bank || ""} در فهرست موجود دیده شد.`;
      setBankAlert(text, true);
      beep();
      speak(text);
      refreshArchives();
    } else if (payload.type === "branch_ready") {
      const text = `بانک ${payload.bank || ""} و شعبه ${payload.branch || ""} آماده است.`;
      setBankAlert(text, true);
      beep();
      speak(text);
      refreshArchives();
    } else if (payload.type === "final_submit_required") {
      const text = `ثبت نهایی برای بانک ${payload.bank || ""} غیرفعال است؛ فقط اعلان انجام شد.`;
      setBankAlert(text, true);
      beep();
      speak(text);
    } else if (payload.type === "final_submit_clicked") {
      const text = `ثبت نهایی بانک ${payload.bank || ""} انجام شد.`;
      setBankAlert(text, true);
      beep();
      speak(text);
      refreshArchives();
    }
  }

  function connectEventSocket() {
    if (state.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.socket.readyState)) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws`);
    state.socket = socket;
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.type === "ping") {
          socket.send(JSON.stringify({ type: "pong", ts: Date.now() }));
          return;
        }
        handleServerEvent(payload);
      } catch (_) {}
    };
    socket.onclose = () => {
      if (state.socket === socket) state.socket = null;
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = setTimeout(connectEventSocket, 1800);
    };
    socket.onerror = () => {
      try { socket.close(); } catch (_) {}
    };
  }

  function bind() {
    byId("mocOtpSend")?.addEventListener("click", sendOtp);
    byId("mocOtpInput")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") sendOtp();
    });
    byId("mocOtpInput")?.addEventListener("input", (event) => {
      event.target.value = String(event.target.value || "").replace(/\D/g, "");
    });
    byId("mocFinalToggle")?.addEventListener("change", (event) => setFinalSubmit(event.target.checked));
    byId("mocPhoneSetup")?.addEventListener("click", showPhoneSetup);
    byId("mocRefreshApplicant")?.addEventListener("click", refreshApplicant);
    byId("mocArchiveRefresh")?.addEventListener("click", refreshArchives);
    window.addEventListener("mahanbot:sms-arrived", (event) => handleSmsArrival(event.detail || {}));
    document.addEventListener("click", () => {
      try {
        if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (state.audioContext.state === "suspended") state.audioContext.resume();
      } catch (_) {}
    }, { once: true });
  }

  async function init() {
    buildPanel();
    hideLegacySmsUi();
    bind();
    await Promise.all([refreshApplicant(), refreshOperationStatus()]);
    connectEventSocket();
    window.setInterval(refreshApplicant, 2500);
    window.setInterval(refreshOperationStatus, 5000);
  }

  window.MahanBotOperationCenter = {
    onSmsArrival: handleSmsArrival,
    refreshArchives,
    refreshApplicant,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
