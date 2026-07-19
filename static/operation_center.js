(() => {
  "use strict";

  const state = {
    currentNid: "",
    currentName: "",
    currentStatus: "",
    finalSubmit: false,
    socket: null,
    reconnectTimer: null,
    audioContext: null,
    latestSmsAt: null,
  };

  const byId = (id) => document.getElementById(id);

  function requestHeaders(hasBody = false) {
    const headers = { Accept: "application/json" };
    if (hasBody) headers["Content-Type"] = "application/json";
    const key =
      sessionStorage.getItem("mahanbot_api_key") ||
      sessionStorage.getItem("mahanbot.apiKey") ||
      sessionStorage.getItem("api_key") ||
      "";
    if (key) headers["X-API-KEY"] = key;
    return headers;
  }

  async function api(path, options = {}) {
    const hasBody = options.body !== undefined && options.body !== null;
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      headers: { ...requestHeaders(hasBody), ...(options.headers || {}) },
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
            <div><strong id="mocFinalLabel">ثبت نهایی مجوز ندارد</strong><small>مجوز فقط برای متقاضی فعال و فقط یک بار مصرف می‌شود</small></div>
            <button id="mocFinalButton" class="moc-button danger" type="button">ثبت نهایی این متقاضی</button>
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
    const activeTokens = ["running", "registering", "selecting", "waiting sms", "awaiting final submit"];
    return rows.find((item) => {
      const status = String(item.status || "").trim().toLowerCase();
      return activeTokens.some((token) => status.includes(token));
    }) || null;
  }

  function updateFinalUi() {
    const label = byId("mocFinalLabel");
    const button = byId("mocFinalButton");
    if (label) {
      label.textContent = state.finalSubmit
        ? "مجوز یک‌بارمصرف ثبت نهایی صادر شده است"
        : "ثبت نهایی مجوز ندارد";
      label.style.color = state.finalSubmit ? "#047857" : "#b45309";
    }
    if (button) {
      button.disabled = !state.currentNid || state.finalSubmit;
      button.textContent = state.finalSubmit ? "منتظر ثبت نهایی…" : "ثبت نهایی این متقاضی";
    }
  }

  async function refreshApplicant() {
    try {
      const rows = await api("/applicants", { method: "GET" });
      const list = Array.isArray(rows) ? rows : [];
      const previousNid = state.currentNid;
      const user = activeApplicantFromRows(list);
      state.currentNid = String(user?.national_id || "");
      state.currentName = String(user?.full_name || "");
      state.currentStatus = String(user?.status || "");
      if (previousNid !== state.currentNid) state.finalSubmit = false;
      const label = byId("mocActiveApplicant");
      if (label) {
        label.textContent = user
          ? `متقاضی فعال: ${state.currentName || "بدون نام"} — ${state.currentNid} — ${state.currentStatus}`
          : "متقاضی فعالی وجود ندارد";
      }
      const sendButton = byId("mocOtpSend");
      const input = byId("mocOtpInput");
      if (sendButton) sendButton.disabled = !state.currentNid;
      if (input) input.disabled = !state.currentNid;
      updateFinalUi();
      if (!state.currentNid) setSmsStatus("ابتدا یک Job ثبت یا انتخاب بانک را اجرا کنید", "wait");
      return user;
    } catch (error) {
      state.currentNid = "";
      state.currentName = "";
      state.currentStatus = "";
      state.finalSubmit = false;
      updateFinalUi();
      setSmsStatus(`خطا در دریافت متقاضی: ${error}`, "alert");
      return null;
    }
  }

  async function refreshFinalPermission() {
    if (!state.currentNid) {
      state.finalSubmit = false;
      updateFinalUi();
      return;
    }
    try {
      const payload = await api(`/api/v1/operation-center/final-submit/${encodeURIComponent(state.currentNid)}`, { method: "GET" });
      state.finalSubmit = Boolean(payload.enabled);
      updateFinalUi();
    } catch (_) {
      state.finalSubmit = false;
      updateFinalUi();
    }
  }

  async function refreshSmsDeviceStatus() {
    if (state.latestSmsAt && Date.now() / 1000 - state.latestSmsAt < 45) return;
    try {
      const payload = await api("/api/v1/sms/notify/devices", { method: "GET" });
      const devices = Array.isArray(payload.devices) ? payload.devices : [];
      const online = devices.filter((item) => Boolean(item.online));
      if (online.length) {
        setSmsStatus(`${online.length.toLocaleString("fa-IR")} دستگاه پیامک آنلاین است`, "ok");
      } else {
        setSmsStatus("دستگاه پیامک آفلاین است؛ تنظیم اتصال گوشی را بررسی کنید", "wait");
      }
    } catch (_) {
      setSmsStatus("وضعیت اتصال گوشی در دسترس نیست", "alert");
    }
  }

  async function grantFinalSubmit() {
    await refreshApplicant();
    if (!state.currentNid) {
      setBankAlert("Job فعالی برای صدور مجوز ثبت نهایی وجود ندارد.", true);
      return;
    }
    const button = byId("mocFinalButton");
    if (button) button.disabled = true;
    try {
      const payload = await api("/api/v1/operation-center/final-submit", {
        method: "POST",
        body: JSON.stringify({ nid: state.currentNid, enabled: true }),
      });
      state.finalSubmit = Boolean(payload.enabled);
      updateFinalUi();
      const text = `مجوز یک‌بارمصرف ثبت نهایی برای ${state.currentName || state.currentNid} صادر شد.`;
      setBankAlert(text, true);
      speak(text);
    } catch (error) {
      state.finalSubmit = false;
      updateFinalUi();
      setBankAlert(`صدور مجوز ثبت نهایی ناموفق بود: ${error}`, true);
    }
  }

  async function sendOtp() {
    await refreshApplicant();
    const input = byId("mocOtpInput");
    const button = byId("mocOtpSend");
    const code = String(input?.value || "").replace(/\D/g, "");
    if (!state.currentNid) {
      setSmsStatus("Job فعالی برای دریافت کد وجود ندارد", "alert");
      return;
    }
    if (!/^\d{4,8}$/.test(code)) {
      setSmsStatus("کد پیامک را با ۴ تا ۸ رقم وارد کنید", "alert");
      input?.focus();
      return;
    }
    if (button) button.disabled = true;
    setSmsStatus(`در حال ارسال کد برای ${state.currentName || state.currentNid}…`, "wait");
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
      if (button) button.disabled = !state.currentNid;
    }
  }

  async function showPhoneSetup() {
    const box = byId("mocPhoneSetupBox");
    if (!box) return;
    box.classList.add("show");
    box.textContent = "در حال دریافت تنظیمات اتصال گوشی…";
    try {
      const payload = await api("/api/v1/sms/setup", { method: "GET" });
      box.textContent = `Server URL: ${payload.notify_url || "-"}\nHeartbeat URL: ${payload.heartbeat_url || "-"}\nDevice key: ${payload.device_key || "-"}`;
    } catch (error) {
      box.textContent = `تنظیمات اتصال در دسترس نیست: ${error}`;
    }
  }

  function handleSmsArrival(event) {
    state.latestSmsAt = Number(event?.received_at || event?.created_at || Date.now() / 1000);
    const time = new Date(state.latestSmsAt * 1000).toLocaleTimeString("fa-IR");
    setSmsStatus(`پیامک جدید در ساعت ${time} رسید؛ کد را وارد و ارسال کنید`, "ok");
    byId("mocOtpInput")?.focus();
    beep();
    speak("پیامک جدید دریافت شد. کد را وارد کنید.");
  }

  async function refreshArchives() {
    const list = byId("mocArchiveList");
    const count = byId("mocArchiveCount");
    if (!list || !count) return;
    if (!state.currentNid) {
      list.textContent = "برای مشاهده آرشیو، یک Job مربوط به متقاضی را اجرا کنید.";
      count.textContent = "۰ فایل";
      return;
    }
    list.textContent = "در حال دریافت آرشیو…";
    try {
      const payload = await api(`/api/v1/archives/${encodeURIComponent(state.currentNid)}`, { method: "GET" });
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
      const text = `ثبت نهایی برای بانک ${payload.bank || ""} مجوز ندارد؛ دکمه ثبت نهایی همین متقاضی را بزنید.`;
      setBankAlert(text, true);
      byId("mocFinalButton")?.focus();
      beep();
      speak(text);
    } else if (payload.type === "final_submit_clicked") {
      state.finalSubmit = false;
      updateFinalUi();
      const text = `ثبت نهایی بانک ${payload.bank || ""} انجام شد و مجوز مصرف شد.`;
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
    byId("mocFinalButton")?.addEventListener("click", grantFinalSubmit);
    byId("mocPhoneSetup")?.addEventListener("click", showPhoneSetup);
    byId("mocRefreshApplicant")?.addEventListener("click", async () => {
      await refreshApplicant();
      await refreshFinalPermission();
    });
    byId("mocArchiveRefresh")?.addEventListener("click", refreshArchives);
    window.addEventListener("mahanbot:sms-arrived", (event) => handleSmsArrival(event.detail || {}));
    document.addEventListener("click", () => {
      try {
        if (!state.audioContext) state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (state.audioContext.state === "suspended") state.audioContext.resume();
      } catch (_) {}
    }, { once: true });
  }

  async function refreshAll() {
    await refreshApplicant();
    await Promise.all([refreshFinalPermission(), refreshSmsDeviceStatus()]);
  }

  async function init() {
    buildPanel();
    hideLegacySmsUi();
    bind();
    await refreshAll();
    connectEventSocket();
    window.setInterval(refreshAll, 3000);
  }

  window.MahanBotOperationCenter = {
    onSmsArrival: handleSmsArrival,
    refreshArchives,
    refreshApplicant,
    refreshSmsDeviceStatus,
    refreshFinalPermission,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
