(() => {
  "use strict";

  const STORAGE_CURSOR = "mahanbot.smsNotify.cursor";
  const STORAGE_SOUND = "mahanbot.smsNotify.sound";
  const POLL_MS = 2500;
  let cursor = Number(sessionStorage.getItem(STORAGE_CURSOR) || 0);
  let audioContext = null;
  let pollTimer = null;
  let polling = false;

  function apiKey() {
    return (
      sessionStorage.getItem("mahanbot_api_key") ||
      sessionStorage.getItem("mahanbot.apiKey") ||
      sessionStorage.getItem("api_key") ||
      ""
    );
  }

  function requestHeaders() {
    const headers = { Accept: "application/json" };
    const key = apiKey();
    if (key) headers["X-API-KEY"] = key;
    return headers;
  }

  function ensureUi() {
    if (document.getElementById("mahanbot-sms-notify-root")) return;

    const style = document.createElement("style");
    style.textContent = `
      #mahanbot-sms-notify-root{position:fixed;inset-inline:16px;top:16px;z-index:2147483000;display:flex;flex-direction:column;gap:10px;pointer-events:none;font-family:inherit;direction:rtl}
      .mahanbot-sms-card{pointer-events:auto;max-width:620px;margin-inline:auto;width:min(100%,620px);background:linear-gradient(135deg,#0f766e,#1d4ed8);color:#fff;border:1px solid rgba(255,255,255,.24);border-radius:18px;padding:16px 18px;box-shadow:0 18px 50px rgba(15,23,42,.34);animation:mahanbotSmsIn .24s ease-out}
      .mahanbot-sms-card h3{margin:0 0 7px;font-size:18px;line-height:1.7}.mahanbot-sms-card p{margin:3px 0;line-height:1.8;font-size:14px}.mahanbot-sms-meta{opacity:.86;font-size:12px!important}
      .mahanbot-sms-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.mahanbot-sms-actions button{border:0;border-radius:999px;padding:8px 13px;cursor:pointer;font:inherit;font-size:13px}.mahanbot-sms-primary{background:#fff;color:#0f4c81}.mahanbot-sms-secondary{background:rgba(255,255,255,.16);color:#fff;border:1px solid rgba(255,255,255,.34)!important}
      #mahanbot-sms-device-chip{position:fixed;left:14px;bottom:14px;z-index:2147482999;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:999px;padding:7px 11px;font:12px/1.4 inherit;box-shadow:0 8px 24px rgba(15,23,42,.24)}
      @keyframes mahanbotSmsIn{from{opacity:0;transform:translateY(-12px) scale(.98)}to{opacity:1;transform:none}}
      @media(max-width:640px){#mahanbot-sms-notify-root{inset-inline:8px;top:8px}.mahanbot-sms-card{border-radius:14px;padding:13px}}
    `;
    document.head.appendChild(style);

    const root = document.createElement("div");
    root.id = "mahanbot-sms-notify-root";
    root.setAttribute("aria-live", "assertive");
    document.body.appendChild(root);

    const chip = document.createElement("div");
    chip.id = "mahanbot-sms-device-chip";
    chip.textContent = "📱 اتصال پیامک: در حال بررسی";
    document.body.appendChild(chip);

    document.addEventListener(
      "click",
      () => {
        try {
          if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
          if (audioContext.state === "suspended") audioContext.resume();
        } catch (_) {
          // Sound remains optional.
        }
      },
      { once: true }
    );
  }

  function soundEnabled() {
    return localStorage.getItem(STORAGE_SOUND) !== "off";
  }

  function playSignal() {
    if (!soundEnabled()) return;
    try {
      if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const now = audioContext.currentTime;
      [0, 0.18].forEach((offset, index) => {
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = index === 0 ? 740 : 920;
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.16, now + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.15);
        oscillator.connect(gain).connect(audioContext.destination);
        oscillator.start(now + offset);
        oscillator.stop(now + offset + 0.17);
      });
    } catch (_) {
      // Browsers can block audio until the first user interaction.
    }
  }

  function faTime(value) {
    try {
      return new Date(Number(value) * 1000).toLocaleTimeString("fa-IR", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch (_) {
      return "اکنون";
    }
  }

  function showArrival(event) {
    ensureUi();
    const root = document.getElementById("mahanbot-sms-notify-root");
    const card = document.createElement("section");
    card.className = "mahanbot-sms-card";
    card.dataset.eventId = event.event_id || "";

    const title = document.createElement("h3");
    title.textContent = "📩 پیامک جدید دریافت شد";
    card.appendChild(title);

    const instruction = document.createElement("p");
    instruction.textContent = "کد تأیید را مستقیماً در مرورگر بازشده وارد کنید؛ متن پیامک به MahanBot منتقل نشده است.";
    card.appendChild(instruction);

    const meta = document.createElement("p");
    meta.className = "mahanbot-sms-meta";
    const device = event.device_id ? `دستگاه: ${event.device_id}` : "دستگاه Android";
    meta.textContent = `${device} • زمان: ${faTime(event.received_at || event.created_at)}`;
    card.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "mahanbot-sms-actions";

    const done = document.createElement("button");
    done.type = "button";
    done.className = "mahanbot-sms-primary";
    done.textContent = "متوجه شدم؛ ورود کد در مرورگر";
    done.addEventListener("click", () => card.remove());
    actions.appendChild(done);

    const mute = document.createElement("button");
    mute.type = "button";
    mute.className = "mahanbot-sms-secondary";
    mute.textContent = soundEnabled() ? "بی‌صدا کردن هشدار" : "فعال‌کردن صدا";
    mute.addEventListener("click", () => {
      const enabled = soundEnabled();
      localStorage.setItem(STORAGE_SOUND, enabled ? "off" : "on");
      mute.textContent = enabled ? "فعال‌کردن صدا" : "بی‌صدا کردن هشدار";
    });
    actions.appendChild(mute);

    card.appendChild(actions);
    root.prepend(card);
    while (root.children.length > 3) root.lastElementChild.remove();

    playSignal();
    window.dispatchEvent(new CustomEvent("mahanbot:sms-arrived", { detail: event }));
  }

  async function updateDeviceChip() {
    const chip = document.getElementById("mahanbot-sms-device-chip");
    if (!chip) return;
    try {
      const response = await fetch("/api/v1/sms/notify/devices", {
        headers: requestHeaders(),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      const devices = Array.isArray(payload.devices) ? payload.devices : [];
      const online = devices.filter((item) => item.online).length;
      chip.textContent = online > 0 ? `📱 اتصال پیامک: ${online} دستگاه آنلاین` : "📱 اتصال پیامک: دستگاه آفلاین";
    } catch (_) {
      chip.textContent = "📱 اتصال پیامک: نیازمند تنظیم کلید/API";
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      ensureUi();
      const url = new URL("/api/v1/sms/notify/latest", window.location.origin);
      url.searchParams.set("since", String(cursor));
      url.searchParams.set("limit", "20");
      const response = await fetch(url.toString(), {
        headers: requestHeaders(),
        cache: "no-store",
      });
      if (!response.ok) return;
      const payload = await response.json();
      const events = Array.isArray(payload.events) ? payload.events : [];
      for (const event of events) {
        const eventTime = Number(event.created_at || event.received_at || 0);
        if (eventTime > cursor) cursor = eventTime;
        showArrival(event);
      }
      sessionStorage.setItem(STORAGE_CURSOR, String(cursor));
      await updateDeviceChip();
    } catch (_) {
      // Keep the main dashboard usable even when the bridge is unavailable.
    } finally {
      polling = false;
    }
  }

  function start() {
    ensureUi();
    if (!cursor) {
      cursor = Date.now() / 1000 - 3;
      sessionStorage.setItem(STORAGE_CURSOR, String(cursor));
    }
    poll();
    pollTimer = window.setInterval(poll, POLL_MS);
    window.addEventListener("beforeunload", () => window.clearInterval(pollTimer), { once: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
