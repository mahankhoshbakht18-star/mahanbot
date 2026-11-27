window.Sadaf = (function () {
  const fa = (n) => String(n ?? 0).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
  const statusText = {
    success: "ثبت موفق", failed: "ناموفق", skipped: "ردشده", needs_review: "نیازمند بررسی",
    waiting: "در انتظار", running: "در حال اجرا", paused: "توقف موقت", stopped: "متوقف",
    done: "تمام شد", idle: "آماده", needs_operator: "منتظر شما", interrupted: "ناتمام",
  };
  const FALLBACK_CATALOG = {
    sectors: [],
    groups: [
      { title: "مسیر مصرف", hint: "بخش مصرف را از فهرست انتخاب کنید. اگر فهرست نیامد، همان متن گزینهٔ سایت را بنویسید.", fields: [
        { name: "بخش مصرف", kind: "text" }, { name: "گروه کاربرد", kind: "text" }, { name: "کاربرد", kind: "text" }, { name: "کاربرد مورد نظر", kind: "text" },
      ]},
      { title: "دوره کشت", hint: "", fields: [
        { name: "آمار تولید دوره کشت قبل", kind: "number" }, { name: "نوع کشت", kind: "text" }, { name: "سطح زیر کشت فعال", kind: "number" },
        { name: "شروع دوره کشت", kind: "text" }, { name: "مدت زمان کشت", kind: "text" }, { name: "نوع مصرف سوخت", kind: "text" },
        { name: "از تاریخ", kind: "text" }, { name: "تا تاریخ", kind: "text" },
      ]},
      { title: "فروشنده", hint: "بات دکمهٔ انتخاب فروشندگی را می‌زند و ردیف هم‌نام با این فیلد را برمی‌دارد.", fields: [
        { name: "نوع فروشندگی", kind: "text" }, { name: "نام فروشندگی", kind: "text" },
      ]},
      { title: "مقدار فرآورده", hint: "", fields: [
        { name: "نفتگاز|مقدار درخواستی در بخش یارانه", kind: "number" }, { name: "نفتگاز|مقدار درخواستی در بخش غیریارانه", kind: "number" },
        { name: "نفتگاز|مانده موجودی", kind: "number" }, { name: "بنزین|مقدار درخواستی در بخش یارانه", kind: "number" },
      ]},
      { title: "توضیح", hint: "", fields: [{ name: "خلاصه دلیل شما برای نیاز به نفتگاز", kind: "long" }] },
    ],
  };
  let root, timer, catalog, catalogPromise, buildPromise;
  let editingId = null, extras = [], editingExtra = -1;
  let defaults = {};

  function norm(value) {
    return String(value || "").replace(/[يى]/g, "ی").replace(/ك/g, "ک").replace(/\s/g, "");
  }
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (key === "class") el.className = value;
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
      else if (value != null) el.setAttribute(key, value);
    });
    (children || []).forEach((child) => el.append(child && child.nodeType ? child : document.createTextNode(child == null ? "" : String(child))));
    return el;
  }
  function button(text, fn, cls) {
    return h("button", { type: "button", class: cls || "sadaf-btn ghost", onclick: async (event) => {
      event.preventDefault();
      try { await fn(); }
      catch (error) { note(error.message || "عملیات انجام نشد.", "err"); }
    } }, [text]);
  }
  function note(text, tone) {
    const box = document.getElementById("sadafMessage");
    if (!box) return;
    box.textContent = text;
    box.classList.toggle("is-ok", tone === "ok");
    box.classList.toggle("is-err", tone === "err");
  }
  function explain(data, response) {
    const detail = data && data.detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const text = detail.map((item) => item && (item.msg || item.message)).filter(Boolean).join(" ");
      if (text) return text;
    }
    if (response.status === 401) return "ابتدا وارد MAHANBOT شوید.";
    if (response.status === 402) return "لایسنس معتبر نیست. اجرای ربات سدف تا فعال شدن لایسنس شروع نمی‌شود.";
    if (response.status === 404) return "مسیر سدف پیدا نشد. برنامه را یک‌بار ببندید و دوباره باز کنید.";
    return "درخواست سدف انجام نشد. (" + response.status + ")";
  }
  async function api(path, options) {
    const response = await fetch(path, Object.assign({ credentials: "same-origin", redirect: "manual" }, options || {}));
    if (response.status === 301 || response.status === 302 || response.status === 303 || response.type === "opaqueredirect") {
      throw new Error("نشست ورود تمام شده است. دوباره وارد MAHANBOT شوید.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(explain(data, response));
    return data;
  }
  function ensureCatalog() {
    if (catalog) return Promise.resolve(catalog);
    if (!catalogPromise) {
      catalogPromise = api("/api/sadaf/catalog").catch(() => null);
    }
    return catalogPromise.then((data) => dressCatalog(data));
  }
  function dressCatalog(data) {
    const base = data && data.groups ? data : FALLBACK_CATALOG;
    const names = new Set();
    (base.groups || []).forEach((group) => group.fields.forEach((field) => names.add(field.name)));
    if (names.has("نوع فروشندگی")) return base;
    const seller = {
      title: "فروشنده",
      hint: "بات دکمهٔ انتخاب فروشندگی را می‌زند و ردیف هم‌نام با این فیلد را برمی‌دارد.",
      fields: [
        { name: "نوع فروشندگی", kind: "text" },
        { name: "نام فروشندگی", kind: "text" },
      ],
    };
    const groups = (base.groups || []).slice();
    const at = groups.findIndex((group) => group.title === "مقدار فرآورده");
    if (at >= 0) groups.splice(at, 0, seller);
    else groups.push(seller);
    return Object.assign({}, base, { groups });
  }
  function builtinNames() {
    const names = [];
    (catalog.groups || []).forEach((group) => group.fields.forEach((field) => names.push(field.name)));
    return names;
  }
  function choicesFor(name) {
    if (name === "نام فروشندگی") return catalog.sellers || [];
    return catalog.sectors || [];
  }
  function fieldControl(field) {
    const wide = field.kind === "long" || field.kind === "select";
    let control;
    if (field.kind === "select") control = dropdown(field.name, choicesFor(field.name));
    else if (field.kind === "long") {
      control = h("textarea", { class: "sadaf-input", rows: "3", "data-field": field.name, "data-kind": "text" });
    } else {
      control = h("input", {
        class: "sadaf-input",
        "data-field": field.name,
        "data-kind": field.kind === "number" ? "number" : "text",
        inputmode: field.kind === "number" ? "decimal" : "text",
        placeholder: field.placeholder || (field.kind === "number" ? "عدد" : ""),
        autocomplete: "off",
      });
    }
    return h("label", { class: wide ? "sadaf-span-2" : "" }, [field.name.replace("|", " — "), control]);
  }
  function dropdown(name, options) {
    const gas = new Set(catalog.naftgaz_sellers || []);
    const select = h("select", { class: "sadaf-input", "data-field": name, "data-kind": "select" }, [
      h("option", { value: "" }, [name === "نام فروشندگی" ? "— تعاونی را انتخاب کنید —" : "— انتخاب کنید —"]),
    ]);
    options.forEach((item) => {
      select.append(h("option", { value: item }, [gas.has(item) ? item + " — نفتگاز دارد" : item]));
    });
    return select;
  }

  function shell() {
    const host = document.getElementById("view-sadaf");
    if (!host) return Promise.resolve();
    if (host.dataset.ready === "5") return Promise.resolve();
    if (buildPromise) return buildPromise;
    buildPromise = (async () => {
      try {
      catalog = await ensureCatalog();
      root = host;
      host.classList.add("view-section");
      const groupsFor = (id) => h("div", { id }, (catalog.groups || []).map((group) => h("section", {}, [
        h("h3", { class: "sadaf-sub" }, [group.title]),
        group.hint ? h("p", { class: "sadaf-hint" }, [group.hint]) : "",
        h("div", { class: "sadaf-grid" }, group.fields.map(fieldControl)),
      ])));
      host.replaceChildren(h("div", { class: "sadaf-wrap" }, [
        h("section", { class: "sadaf-hero" }, [
          h("div", {}, [
            h("div", { class: "sadaf-kicker" }, ["سوخت سدف"]),
            h("h2", {}, ["متقاضی‌ها و اجرای درخواست"]),
            h("p", { id: "sadafMessage", class: "sadaf-message" }, ["اطلاعات درخواست یک بار در «تنظیمات» ذخیره می‌شود. برای متقاضی جدید فقط نام کاربری و رمز لازم است. کپچا خودکار حل می‌شود؛ فقط اگر چند تلاش خودکار شکست بخورد آلارم می‌دهد تا کد را دستی بنویسید."]),
          ]),
          h("span", { id: "sadafSaveState", class: "sadaf-pill" }, ["متقاضی جدید"]),
        ]),
        h("div", { class: "sadaf-alarm", id: "sadafAlarm", role: "alert" }, [
          h("i", { class: "fa-solid fa-bell" }),
          h("span", { id: "sadafAlarmText", class: "sadaf-grow" }, [""]),
          button("نوشتن کد", () => { focusCaptcha(); }, "sadaf-btn primary"),
          button("بی‌صدا", () => { alarmMuted = true; }, "sadaf-btn ghost"),
        ]),
        h("div", { class: "sadaf-stats", id: "sadafStats" }),
        h("article", { class: "sadaf-card", id: "sadaf-form" }, [
          h("h3", { id: "sadafFormTitle" }, ["متقاضی جدید"]),
          h("form", { id: "sadafForm", onsubmit: saveForm }, [
            h("div", { class: "sadaf-grid" }, [
              h("label", {}, ["نام کاربری سامانه سدف ", h("span", { class: "sadaf-req" }, ["*"]), h("input", { id: "sadafUser", class: "sadaf-input sadaf-latin", autocomplete: "off", required: "required" })]),
              h("label", {}, ["رمز عبور سامانه ", h("span", { class: "sadaf-req", id: "sadafPassMark" }, ["*"]),
                h("input", { id: "sadafPass", class: "sadaf-input sadaf-latin", type: "password", autocomplete: "new-password" }),
              ]),
              h("label", {}, ["نام و نام خانوادگی (اختیاری)", h("input", { id: "sadafName", class: "sadaf-input", autocomplete: "off", placeholder: "خالی بماند، از پنل سدف خوانده می‌شود" })]),
              h("label", {}, ["شماره تلفن (اختیاری)", h("input", { id: "sadafPhone", class: "sadaf-input", inputmode: "tel", autocomplete: "off" })]),
              h("p", { class: "sadaf-hint sadaf-span-2" }, ["بقیهٔ اطلاعات درخواست از «تنظیمات» برداشته می‌شود. اگر نام را ننویسید، بات بعد از اولین ورود نام و نام خانوادگی را از پنل سدف می‌خواند و ذخیره می‌کند. رمز رمزنگاری‌شده ذخیره می‌شود و دوباره نشان داده نمی‌شود؛ هنگام ویرایش اگر خالی بماند همان رمز قبلی می‌ماند."]),
            ]),
            h("details", { id: "sadafOwn", class: "sadaf-own" }, [
              h("summary", {}, ["مقدار اختصاصی برای همین متقاضی (اختیاری)"]),
              h("p", { class: "sadaf-hint" }, ["فقط خانه‌ای را پر کنید که برای این متقاضی با «تنظیمات» فرق دارد؛ مثلاً سطح زیر کشت یا لیتر نفتگاز. خانهٔ خالی از تنظیمات پر می‌شود."]),
              groupsFor("sadafGroups"),
              h("h3", { class: "sadaf-sub" }, ["فیلد سفارشی"]),
              h("div", { id: "sadafExtras" }),
              h("div", { class: "sadaf-grid" }, [
                h("label", {}, ["نام فیلد در سایت", h("input", { id: "sadafExtraName", class: "sadaf-input" })]),
                h("label", {}, ["نوع فیلد", h("select", { id: "sadafExtraKind", class: "sadaf-input", onchange: toggleExtraOptions }, [
                  h("option", { value: "text" }, ["متنی"]),
                  h("option", { value: "number" }, ["عددی"]),
                  h("option", { value: "select" }, ["انتخابی"]),
                ])]),
                h("label", { class: "sadaf-span-2", id: "sadafExtraOptionsWrap" }, ["گزینه‌ها، هر کدام در یک خط", h("textarea", { id: "sadafExtraOptions", class: "sadaf-input", rows: "3" })]),
                h("label", { class: "sadaf-span-2" }, ["مقدار", h("input", { id: "sadafExtraValue", class: "sadaf-input" })]),
              ]),
              h("div", { class: "sadaf-actions" }, [button("افزودن فیلد", addExtra, "sadaf-btn ghost")]),
            ]),
            h("div", { class: "sadaf-actions" }, [
              h("button", { class: "sadaf-btn primary", type: "submit" }, ["ذخیره متقاضی"]),
              button("متقاضی جدید", clearForm, "sadaf-btn ghost"),
              button("اجرای همین متقاضی", runOne, "sadaf-btn teal"),
            ]),
          ]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-run" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-play" }), "اجرای عملیات"]),
          h("p", { id: "sadafRunLine" }, ["مرورگر سدف هنوز باز نشده است."]),
          h("div", { class: "sadaf-inline" }, [
            h("label", {}, ["شروع صف از ردیف", h("input", { id: "sadafStartRow", class: "sadaf-input", type: "number", min: "1", value: "1" })]),
          ]),
          h("div", { class: "sadaf-inline" }, [
            h("label", {}, ["کد امنیتی تصویر (بعد از آلارم)", h("input", { id: "sadafCaptcha", class: "sadaf-input sadaf-captcha", maxlength: "8", autocomplete: "off", spellcheck: "false", placeholder: "کد تصویر", onkeydown: async (event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              try { await submitCaptcha(); }
              catch (error) { note(error.message || "عملیات انجام نشد.", "err"); }
            } })]),
            button("ارسال کد تصویر", submitCaptcha, "sadaf-btn primary"),
          ]),
          h("p", { class: "sadaf-hint" }, ["ربات کپچای ورود و صفحهٔ آخر را چند بار خودکار حل می‌کند. اگر نشد، آلارم می‌دهد؛ آن‌وقت کد را از پنجرهٔ سدف بخوانید و اینجا بنویسید. کد ورود پذیرفته‌شده برای آموزش مدل سدف نگه داشته می‌شود."]),
          h("div", { class: "sadaf-inline" }, [
            h("label", { class: "sadaf-grow" }, ["کد تایید ۷ رقمی یا متن پیامک NIOPDC", h("textarea", { id: "sadafSms", class: "sadaf-input", rows: "2", autocomplete: "off", spellcheck: "false", placeholder: "اگر اپ پیامک وصل نیست، کد یا کل پیامک را اینجا بچسبانید" })]),
            button("ثبت پیامک", submitSms, "sadaf-btn primary"),
          ]),
          h("p", { class: "sadaf-hint" }, ["پیامک‌های NIOPDC از اپ پیامک ماهان خودکار می‌رسند. پیامک رمز عبور و کارت بانکی ذخیره نمی‌شود."]),
          h("div", { class: "sadaf-actions" }, [
            button("باز کردن سایت و ورود", openSite, "sadaf-btn teal"),
            button("شروع صف", () => runStart(false), "sadaf-btn primary"),
            button("اجرای مجدد ثبت‌شده‌ها", () => runStart(true), "sadaf-btn ghost"),
            button("توقف موقت", () => command("pause"), "sadaf-btn ghost"),
            button("ادامه صف", () => command("resume"), "sadaf-btn ghost"),
            button("رد کردن مشتری", () => command("skip"), "sadaf-btn warn"),
            button("اجرای مجدد فرم", () => command("refill"), "sadaf-btn ghost"),
            button("توقف کامل", () => command("stop"), "sadaf-btn danger"),
            button("ثبت را تأیید می‌کنم", () => command("next"), "sadaf-btn teal"),
            button("نیازمند بررسی و بعدی", () => command("accept_uncertain"), "sadaf-btn warn"),
          ]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-people" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-users" }), "فهرست متقاضی‌ها ", h("span", { id: "sadafPeopleCount", class: "sadaf-pill" }, ["۰"])]),
          h("div", { class: "sadaf-inline" }, [
            h("input", { id: "sadafSearch", class: "sadaf-input", placeholder: "جست‌وجوی نام، تلفن یا نام کاربری", oninput: queueSearch }),
            button("جست‌وجو", loadCustomers, "sadaf-btn ghost"),
            button("متقاضی جدید", newApplicant, "sadaf-btn primary"),
          ]),
          h("div", { class: "sadaf-table-wrap" }, [h("table", { class: "sadaf-table", id: "sadafTable" })]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-jahad" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-file-csv" }), "درخواست‌های ثبت‌شده برای جهاد ", h("span", { id: "sadafJahadCount", class: "sadaf-pill" }, ["۰"])]),
          h("div", { class: "sadaf-table-wrap" }, [h("table", { class: "sadaf-table", id: "sadafJahad" })]),
          h("div", { class: "sadaf-actions" }, [button("دریافت فایل جهاد", downloadJahad, "sadaf-btn ghost")]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-log" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-clipboard-list" }), "فعالیت و گزارش"]),
          h("div", { id: "sadafLog", class: "sadaf-log" }),
          h("div", { class: "sadaf-actions" }, [
            button("مشاهده گزارش", downloadReport, "sadaf-btn ghost"),
            button("نسخه پشتیبان", backup, "sadaf-btn ghost"),
          ]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-settings" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-gear" }), "تنظیمات"]),
          h("p", { class: "sadaf-hint" }, ["این اطلاعات برای همهٔ متقاضی‌ها یکی است و یک بار اینجا ذخیره می‌شود. اگر متقاضی‌ای مقدار اختصاصی داشته باشد، همان مقدار برای او جایگزین می‌شود."]),
          h("form", { id: "sadafSettingsForm", onsubmit: (event) => { event.preventDefault(); saveSettings().catch((error) => note(error.message || "تنظیمات ذخیره نشد.", "err")); } }, [
            h("h3", { class: "sadaf-sub" }, ["سامانه"]),
            h("div", { class: "sadaf-grid" }, [
              h("label", {}, ["مرورگر", h("select", { id: "sadafBrowser", class: "sadaf-input" }, [
                h("option", { value: "chrome" }, ["Chrome"]),
                h("option", { value: "edge" }, ["Edge"]),
              ])]),
              h("label", {}, ["آدرس سامانه", h("input", { id: "sadafBase", class: "sadaf-input sadaf-latin", autocomplete: "off" })]),
            ]),
            groupsFor("sadafSettingsGroups"),
            h("div", { class: "sadaf-actions" }, [h("button", { class: "sadaf-btn primary", type: "submit" }, ["ذخیره تنظیمات"])]),
          ]),
        ]),
      ]));
      toggleExtraOptions();
      fillForm(null);
      host.dataset.ready = "5";
      } catch (error) {
        buildPromise = null;
        host.dataset.ready = "";
        throw error;
      }
    })();
    return buildPromise;
  }

  function toggleExtraOptions() {
    const kind = document.getElementById("sadafExtraKind");
    const wrap = document.getElementById("sadafExtraOptionsWrap");
    if (kind && wrap) wrap.hidden = kind.value !== "select";
  }
  function scopeOf(id) { return document.getElementById(id) || root; }
  function valuesFromForm(scopeId) {
    const fields = [];
    scopeOf(scopeId).querySelectorAll("[data-field]").forEach((el) => {
      const kind = el.getAttribute("data-kind") || "text";
      let value = "";
      if (kind === "select") {
        const typed = (el.value || "").trim();
        value = choicesFor(el.getAttribute("data-field")).indexOf(typed) >= 0 ? typed : "";
      } else value = (el.value || "").trim();
      if (value) fields.push({ name: el.getAttribute("data-field"), value, kind });
    });
    return fields;
  }
  function ownFields() {
    // Only what differs from «تنظیمات» is kept on the applicant, so later setting changes reach everyone.
    const own = valuesFromForm("sadafGroups").filter((field) => norm(defaults[field.name] || "") !== norm(field.value));
    extras.forEach((item) => { if (item.name && item.value) own.push(item); });
    return own;
  }
  function setControl(scopeId, name, value) {
    const el = scopeOf(scopeId).querySelector('[data-field="' + CSS.escape(name) + '"]');
    if (!el) return;
    el.value = value;
  }
  function showDefaults() {
    scopeOf("sadafGroups").querySelectorAll("[data-field]").forEach((el) => {
      const value = defaults[el.getAttribute("data-field")] || "";
      if (el.tagName === "SELECT") {
        el.options[0].textContent = value ? "از تنظیمات: " + value : "— انتخاب کنید —";
      } else {
        el.placeholder = value ? "از تنظیمات: " + value : "";
      }
    });
  }
  function fillForm(item) {
    editingId = item ? item.id : null;
    document.getElementById("sadafFormTitle").textContent = item ? "ویرایش متقاضی" : "متقاضی جدید";
    document.getElementById("sadafPassMark").textContent = item ? "" : "*";
    document.getElementById("sadafName").value = item ? item.full_name || "" : "";
    document.getElementById("sadafPhone").value = item ? item.phone || "" : "";
    document.getElementById("sadafUser").value = item ? item.username || "" : "";
    document.getElementById("sadafPass").value = "";
    const known = new Set(builtinNames());
    extras = [];
    scopeOf("sadafGroups").querySelectorAll("[data-field]").forEach((el) => { el.value = ""; });
    let own = 0;
    (item ? item.fields || [] : []).forEach((field) => {
      if (known.has(field.name)) {
        if (norm(defaults[field.name] || "") === norm(field.value)) return;
        setControl("sadafGroups", field.name, field.value);
      } else extras.push({ name: field.name, kind: field.kind || "text", value: field.value, options: field.options || "" });
      own += 1;
    });
    drawExtras();
    showDefaults();
    document.getElementById("sadafOwn").open = own > 0;
    document.getElementById("sadafSaveState").textContent = item ? "در حال ویرایش" : "متقاضی جدید";
  }
  function drawExtras() {
    const box = document.getElementById("sadafExtras");
    const kindText = { text: "متنی", number: "عددی", select: "انتخابی" };
    box.replaceChildren(...extras.map((item, index) => h("div", { class: "sadaf-extra" }, [
      h("strong", {}, [item.name]),
      h("span", { class: "sadaf-pill" }, [kindText[item.kind] || "متنی"]),
      h("span", {}, [item.value]),
      h("span", { class: "sadaf-actions" }, [
        button("ویرایش", () => editExtra(index), "sadaf-btn ghost tiny"),
        button("حذف", () => removeExtra(index), "sadaf-btn danger tiny"),
      ]),
    ])));
  }
  function editExtra(index) {
    const item = extras[index];
    editingExtra = index;
    document.getElementById("sadafExtraName").value = item.name;
    document.getElementById("sadafExtraKind").value = item.kind || "text";
    document.getElementById("sadafExtraOptions").value = item.options || "";
    document.getElementById("sadafExtraValue").value = item.value;
    toggleExtraOptions();
  }
  function removeExtra(index) {
    const item = extras[index];
    if (!confirm("فیلد «" + item.name + "» حذف شود؟")) return;
    extras.splice(index, 1);
    if (editingExtra === index) editingExtra = -1;
    drawExtras();
  }
  function addExtra() {
    const name = document.getElementById("sadafExtraName").value.trim();
    const kind = document.getElementById("sadafExtraKind").value;
    const value = document.getElementById("sadafExtraValue").value.trim();
    const options = document.getElementById("sadafExtraOptions").value.trim();
    if (!name || !value) throw new Error("نام فیلد و مقدار را هر دو بنویسید.");
    if (builtinNames().includes(name)) throw new Error("این نام در فرم اصلی هست. همان خانه را پر کنید.");
    if (kind === "select" && !options) throw new Error("برای فیلد انتخابی گزینه‌ها را بنویسید.");
    const item = { name, kind, value, options: kind === "select" ? options : "" };
    if (editingExtra >= 0) extras[editingExtra] = item;
    else extras.push(item);
    editingExtra = -1;
    ["sadafExtraName", "sadafExtraOptions", "sadafExtraValue"].forEach((id) => { document.getElementById(id).value = ""; });
    document.getElementById("sadafExtraKind").value = "text";
    toggleExtraOptions();
    drawExtras();
  }
  async function newApplicant() {
    await focus("form");
    clearForm();
  }
  function clearForm() {
    fillForm(null);
    note("نام کاربری و رمز متقاضی جدید را بنویسید. بقیه از «تنظیمات» برداشته می‌شود.", "ok");
    document.getElementById("sadafUser").focus();
  }
  async function persist() {
    const payload = {
      full_name: document.getElementById("sadafName").value.trim(),
      username: document.getElementById("sadafUser").value.trim(),
      phone: document.getElementById("sadafPhone").value.trim(),
      password: document.getElementById("sadafPass").value,
      fields: ownFields(),
    };
    if (!payload.username) throw new Error("نام کاربری لازم است.");
    if (!editingId && !payload.password) throw new Error("برای مشتری جدید رمز عبور لازم است.");
    const path = editingId ? "/api/sadaf/customers/" + editingId : "/api/sadaf/customers";
    const saved = await api(path, {
      method: editingId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    editingId = saved.id;
    document.getElementById("sadafPass").value = "";
    document.getElementById("sadafPassMark").textContent = "";
    document.getElementById("sadafFormTitle").textContent = "ویرایش متقاضی";
    document.getElementById("sadafSaveState").textContent = "ذخیره شد";
    note(saved.full_name ? "متقاضی «" + saved.full_name + "» ذخیره شد." : "متقاضی ذخیره شد. نام بعد از اولین ورود از پنل سدف خوانده می‌شود.", "ok");
    await loadCustomers();
    await refresh();
    return saved;
  }
  async function saveForm(event) {
    event.preventDefault();
    try { await persist(); }
    catch (error) { note(error.message || "ذخیره انجام نشد.", "err"); }
  }
  async function editCustomer(id) {
    const item = await api("/api/sadaf/customers/" + id);
    fillForm(item);
    await focus("form");
    note("اطلاعات «" + item.username + "» در فرم قرار گرفت. رمز نشان داده نمی‌شود.", "ok");
  }
  async function removeCustomer(id) {
    if (!confirm("این مشتری از دیتابیس سدف حذف شود؟")) return;
    await api("/api/sadaf/customers/" + id, { method: "DELETE" });
    if (editingId === id) fillForm(null);
    note("مشتری حذف شد.", "ok");
    await loadCustomers();
    await refresh();
  }
  let searchTimer = 0;
  function queueSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadCustomers().catch((error) => note(error.message, "err")), 250);
  }
  async function loadCustomers() {
    const q = document.getElementById("sadafSearch") ? document.getElementById("sadafSearch").value || "" : "";
    const data = await api("/api/sadaf/customers?q=" + encodeURIComponent(q));
    const table = document.getElementById("sadafTable");
    const items = data.items || [];
    document.getElementById("sadafPeopleCount").textContent = fa(items.length);
    const head = h("thead", {}, [h("tr", {}, ["نام", "تلفن", "نام کاربری", "اطلاعات درخواست", "آخرین اجرا", "تاریخ", ""].map((title) => h("th", {}, [title])))]);
    const body = h("tbody", {}, items.map((item) => h("tr", {}, [
      h("td", {}, [item.full_name || "از پنل سدف خوانده می‌شود"]),
      h("td", { class: "sadaf-latin" }, [item.phone || "—"]),
      h("td", { class: "sadaf-latin" }, [item.username]),
      h("td", {}, [item.info_status || "—"]),
      h("td", {}, [statusText[item.last_status] || "در انتظار"]),
      h("td", { class: "sadaf-latin" }, [item.last_finished_at || "—"]),
      h("td", { class: "sadaf-actions" }, [
        button("ویرایش", () => editCustomer(item.id), "sadaf-btn ghost tiny"),
        button("اجرا", () => runCustomer(item.id, item.last_status === "success"), "sadaf-btn teal tiny"),
        button("حذف", () => removeCustomer(item.id), "sadaf-btn danger tiny"),
      ]),
    ])));
    table.replaceChildren(head, body);
  }
  async function refresh() {
    if (document.body.dataset.module !== "sadaf") return;
    const data = await api("/api/sadaf/summary");
    const counts = data.counts || {};
    document.getElementById("sadafStats").replaceChildren(
      stat("کل", counts.total), stat("ثبت موفق", counts.success), stat("در انتظار", counts.waiting),
      stat("ناموفق", counts.failed), stat("ردشده", counts.skipped), stat("باقی‌مانده", counts.remaining)
    );
    const run = data.run || {};
    const who = run.current ? "مشتری " + fa(run.current.index) + " از " + fa(run.current.total) + " — " + run.current.username : "";
    document.getElementById("sadafRunLine").textContent = [statusText[run.status] || "آماده", who, run.message || ""].filter(Boolean).join(" · ");
    showAlarm(run.alarm);
    const events = await api("/api/sadaf/events?limit=12");
    document.getElementById("sadafLog").replaceChildren(...(events.items || []).map((item) => h("div", {}, [item.at + " — " + item.message])));
  }
  let alarmAt = 0, alarmMuted = false, alarmTimer = 0, titleTimer = 0, audio = null, plainTitle = "";
  function tone() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.35].forEach((delay) => {
        const osc = audio.createOscillator(), gain = audio.createGain();
        osc.type = "square"; osc.frequency.value = 880; gain.gain.value = 0.08;
        osc.connect(gain); gain.connect(audio.destination);
        osc.start(audio.currentTime + delay); osc.stop(audio.currentTime + delay + 0.22);
      });
    } catch (error) {}
  }
  function focusCaptcha() {
    const box = document.getElementById("sadafCaptcha");
    if (!box) return;
    box.scrollIntoView({ behavior: "smooth", block: "center" });
    box.focus();
  }
  function askNotify() {
    try { if (window.Notification && Notification.permission === "default") Notification.requestPermission(); } catch (error) {}
  }
  function showAlarm(alarm) {
    const box = document.getElementById("sadafAlarm");
    if (!box) return;
    if (!alarm) {
      if (alarmAt) {
        box.classList.remove("on");
        clearInterval(alarmTimer); clearInterval(titleTimer);
        document.title = plainTitle || document.title;
        alarmAt = 0;
      }
      return;
    }
    document.getElementById("sadafAlarmText").textContent = alarm.message || "ربات سدف منتظر شماست.";
    if (alarm.at === alarmAt) return;
    if (!alarmAt) plainTitle = document.title;
    alarmAt = alarm.at; alarmMuted = false;
    box.classList.add("on");
    clearInterval(alarmTimer); clearInterval(titleTimer);
    tone();
    alarmTimer = setInterval(() => { if (!alarmMuted) tone(); }, 4000);
    let flip = false;
    titleTimer = setInterval(() => { flip = !flip; document.title = flip ? "⚠ سدف منتظر شماست" : plainTitle; }, 1000);
    try {
      if (window.Notification && Notification.permission === "granted") new Notification("ربات سدف منتظر شماست", { body: alarm.message || "" });
    } catch (error) {}
    if (alarm.kind !== "credentials") focusCaptcha();
  }
  function stat(label, value) {
    return h("article", { class: "sadaf-stat" }, [h("small", {}, [label]), h("strong", {}, [fa(value)])]);
  }
  function useSettings(data) {
    defaults = {};
    (data.request_fields || []).forEach((field) => { defaults[field.name] = field.value; });
    const browser = document.getElementById("sadafBrowser");
    if (browser && data.browser) browser.value = data.browser;
    const base = document.getElementById("sadafBase");
    if (base) base.value = data.base_url || "";
    scopeOf("sadafSettingsGroups").querySelectorAll("[data-field]").forEach((el) => {
      el.value = defaults[el.getAttribute("data-field")] || "";
    });
    showDefaults();
  }
  async function saveSettings() {
    const data = await api("/api/sadaf/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        browser: document.getElementById("sadafBrowser").value,
        base_url: document.getElementById("sadafBase").value.trim(),
        request_fields: valuesFromForm("sadafSettingsGroups"),
      }),
    });
    useSettings(data);
    note("تنظیمات ذخیره شد. از این به بعد همهٔ متقاضی‌ها با همین اطلاعات اجرا می‌شوند.", "ok");
  }
  async function openSite() {
    askNotify();
    let customerId = editingId;
    const username = document.getElementById("sadafUser") ? document.getElementById("sadafUser").value.trim() : "";
    const password = document.getElementById("sadafPass") ? document.getElementById("sadafPass").value : "";
    if (username && (password || editingId)) {
      const saved = await persist();
      customerId = saved.id;
    }
    await api("/api/sadaf/runs/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(customerId ? { customer_id: customerId } : {}),
    });
    note(customerId ? "صفحهٔ ورود باز شد و نام کاربری همین مشتری پر می‌شود. کد امنیتی را در همان پنجره بزنید." : "صفحهٔ ورود سامانه سدف باز شد.", "ok");
    await focus("run");
    await refresh();
  }
  async function runStart(force) {
    askNotify();
    if (force && !confirm("مشتری‌هایی که قبلاً ثبت موفق داشته‌اند دوباره اجرا شوند؟")) return;
    await api("/api/sadaf/runs/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_row: Number(document.getElementById("sadafStartRow").value || 1), force }),
    });
    await focus("run");
    await refresh();
  }
  async function runCustomer(id, alreadyDone) {
    askNotify();
    if (alreadyDone && !confirm("این مشتری قبلاً ثبت موفق دارد. دوباره اجرا شود؟")) return;
    await api("/api/sadaf/runs/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customer_id: id, force: !!alreadyDone }),
    });
    await focus("run");
    await refresh();
  }
  async function runOne() {
    const saved = await persist();
    await runCustomer(saved.id, saved.last_status === "success");
  }
  async function submitCaptcha() {
    const box = document.getElementById("sadafCaptcha");
    const code = (box && box.value || "").trim();
    await api("/api/sadaf/runs/captcha", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (box) box.value = "";
    note("کد فرستاده شد.", "ok");
    await refresh();
  }
  async function submitSms() {
    const box = document.getElementById("sadafSms");
    const text = (box && box.value || "").trim();
    if (!text) throw new Error("کد یا متن پیامک را بنویسید.");
    try {
      const data = await api("/api/sadaf/sms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      note(data.message || "پیامک ثبت شد.", "ok");
    } finally {
      if (box) box.value = "";
    }
    await refresh();
    await loadJahad().catch(() => {});
  }
  function downloadJahad() { window.location = "/api/sadaf/jahad.csv"; }
  async function loadJahad() {
    const data = await api("/api/sadaf/jahad");
    const items = data.items || [];
    document.getElementById("sadafJahadCount").textContent = fa(items.length);
    const titles = ["نام کاربر", "نام کاربری", "کد رهگیری", "فرآورده", "مقدار", "تاریخ پیامک", "وضعیت پخش"];
    const head = h("thead", {}, [h("tr", {}, titles.map((title) => h("th", {}, [title])))]);
    const rows = items.map((item) => h("tr", {}, [
      h("td", {}, [item.full_name || "—"]),
      h("td", { class: "sadaf-latin" }, [item.username || "—"]),
      h("td", { class: "sadaf-latin" }, [item.tracking]),
      h("td", {}, [item.product || "—"]),
      h("td", {}, [item.amount ? fa(item.amount) + " لیتر" : "—"]),
      h("td", { class: "sadaf-latin" }, [item.sms_at || "—"]),
      h("td", {}, [item.delivery || "در انتظار پخش"]),
    ]));
    if (!rows.length) rows.push(h("tr", {}, [h("td", { colspan: "7", class: "sadaf-empty" }, ["هنوز پیامک «ثبت درخواست» نرسیده است."])]));
    document.getElementById("sadafJahad").replaceChildren(head, h("tbody", {}, rows));
  }
  async function command(name) {
    await api("/api/sadaf/runs/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: name }),
    });
    await refresh();
  }
  function downloadReport() { window.location = "/api/sadaf/reports/latest"; }
  async function backup() {
    await api("/api/sadaf/backup", { method: "POST" });
    note("نسخه پشتیبان دیتابیس سدف ذخیره شد.", "ok");
  }
  async function loadSettings() {
    useSettings(await api("/api/sadaf/settings"));
  }
  function markNav(name) {
    const links = document.querySelectorAll("#sadafNav .nav-link");
    const index = { form: 0, run: 1, people: 2, log: 3, settings: 4 }[name];
    links.forEach((link, i) => link.classList.toggle("active", i === index));
  }
  async function focus(name) {
    await enter();
    markNav(name);
    const el = document.getElementById("sadaf-" + name);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function enter() {
    await shell();
    const view = document.getElementById("view-sadaf");
    if (view) view.classList.add("active-view");
    await loadSettings().catch((error) => note(error.message, "err"));
    await refresh().catch((error) => note(error.message, "err"));
    await loadCustomers().catch((error) => note(error.message, "err"));
    await loadJahad().catch(() => {});
    if (!document.querySelector("#sadafNav .nav-link.active")) markNav("form");
    clearInterval(timer);
    let ticks = 0;
    timer = setInterval(() => {
      refresh().catch(() => {});
      ticks += 1;
      if (ticks % 5 === 0) loadJahad().catch(() => {});
    }, 2000);
  }
  function leave() { clearInterval(timer); }
  function resumeModule() {
    try {
      if (sessionStorage.getItem("mahanModule") === "sadaf") switchModule("sadaf");
    } catch (error) {}
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", resumeModule);
  else resumeModule();
  return { enter, leave, focus, newApplicant };
})();
