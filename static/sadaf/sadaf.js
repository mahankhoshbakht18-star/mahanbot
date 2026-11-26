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
  let editingId = null, extras = [], editingExtra = -1, restored = false;
  const DEFAULT_FIELDS = [
    ["بخش مصرف", "کشاورزی"],
    ["گروه کاربرد", "گلخانه"],
    ["کاربرد", "گلخانه"],
    ["کاربرد مورد نظر", "تولید محصولات سبزی و صیفی در گلخانه / بهره برداری گلخانه کوچک مقیاس (1027)"],
    ["آمار تولید دوره کشت قبل", "299"],
    ["نوع کشت", "خیار"],
    ["سطح زیر کشت فعال", "299"],
    ["شروع دوره کشت", "1405/07/01"],
    ["مدت زمان کشت", "8ماه"],
    ["نوع فروشندگی", "فروشندگی"],
    ["نام فروشندگی", "تعاوني کهن شهر-زابلي"],
    ["نوع مصرف سوخت", "مصرف سوخت جهت گرمایش"],
    ["از تاریخ", "1405/08/01"],
    ["تا تاریخ", "1405/08/30"],
    ["نفتگاز|مقدار درخواستی در بخش یارانه", "2000"],
  ];

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
    if (host.dataset.ready === "4") return Promise.resolve();
    if (buildPromise) return buildPromise;
    buildPromise = (async () => {
      try {
      catalog = await ensureCatalog();
      root = host;
      host.classList.add("view-section");
      host.replaceChildren(h("div", { class: "sadaf-wrap" }, [
        h("section", { class: "sadaf-hero" }, [
          h("div", {}, [
            h("div", { class: "sadaf-kicker" }, ["سوخت سدف"]),
            h("h2", {}, ["اطلاعات مشتری و اجرای درخواست"]),
            h("p", { id: "sadafMessage", class: "sadaf-message" }, ["نام کاربری، رمز و اطلاعات درخواست را ذخیره کنید. اجرای هر مشتری از همان رکورد دیتابیس خوانده می‌شود. کپچا و پیامک با شماست."]),
          ]),
          h("span", { id: "sadafSaveState", class: "sadaf-pill" }, ["هنوز ذخیره نشده"]),
        ]),
        h("div", { class: "sadaf-stats", id: "sadafStats" }),
        h("article", { class: "sadaf-card", id: "sadaf-form" }, [
          h("h3", { id: "sadafFormTitle" }, [h("i", { class: "fa-solid fa-user" }), "مشخصات مشتری"]),
          h("form", { id: "sadafForm", onsubmit: saveForm }, [
            h("div", { class: "sadaf-grid" }, [
              h("label", {}, ["نام و نام خانوادگی", h("input", { id: "sadafName", class: "sadaf-input", autocomplete: "name" })]),
              h("label", {}, ["شماره تلفن", h("input", { id: "sadafPhone", class: "sadaf-input", inputmode: "tel", autocomplete: "tel" })]),
              h("label", {}, ["نام کاربری سامانه سدف ", h("span", { class: "sadaf-req" }, ["*"]), h("input", { id: "sadafUser", class: "sadaf-input sadaf-latin", autocomplete: "off", required: "required" })]),
              h("label", {}, ["رمز عبور سامانه ", h("span", { class: "sadaf-req", id: "sadafPassMark" }, ["*"]),
                h("input", { id: "sadafPass", class: "sadaf-input sadaf-latin", type: "password", autocomplete: "new-password", placeholder: "برای حفظ رمز قبلی خالی بگذارید" }),
              ]),
              h("p", { class: "sadaf-hint sadaf-span-2" }, ["رمز رمزنگاری‌شده ذخیره می‌شود و دوباره نشان داده نمی‌شود. هنگام ویرایش، اگر خالی بماند همان رمز قبلی می‌ماند."]),
              h("p", { class: "sadaf-hint sadaf-span-2" }, ["متقاضی جدید با تنظیم گلخانه پر می‌شود. برای صنعتی فقط سطح زیر کشت و لیتر نفتگاز را عوض کنید. تاریخ این پیش‌فرض ۱ تا ۳۰ آبان ۱۴۰۵ است."]),
            ]),
            h("div", { id: "sadafGroups" }, (catalog.groups || []).map((group) => h("section", {}, [
              h("h3", { class: "sadaf-sub" }, [group.title]),
              group.hint ? h("p", { class: "sadaf-hint" }, [group.hint]) : "",
              h("div", { class: "sadaf-grid" }, group.fields.map(fieldControl)),
            ]))),
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
            h("div", { class: "sadaf-actions" }, [
              button("افزودن فیلد", addExtra, "sadaf-btn ghost"),
              h("button", { class: "sadaf-btn primary", type: "submit" }, ["ذخیره اطلاعات"]),
              button("فرم تازه", clearForm, "sadaf-btn ghost"),
              button("اجرای همین مشتری", runOne, "sadaf-btn teal"),
            ]),
          ]),
        ]),
        h("article", { class: "sadaf-card", id: "sadaf-run" }, [
          h("h3", {}, [h("i", { class: "fa-solid fa-play" }), "اجرای عملیات"]),
          h("p", { id: "sadafRunLine" }, ["مرورگر سدف هنوز باز نشده است."]),
          h("div", { class: "sadaf-inline" }, [
            h("label", {}, ["مرورگر", h("select", { id: "sadafBrowser", class: "sadaf-input", onchange: saveBrowser }, [
              h("option", { value: "chrome" }, ["Chrome"]),
              h("option", { value: "edge" }, ["Edge"]),
            ])]),
            h("label", {}, ["شروع صف از ردیف", h("input", { id: "sadafStartRow", class: "sadaf-input", type: "number", min: "1", value: "1" })]),
          ]),
          h("div", { class: "sadaf-inline" }, [
            h("label", {}, ["کد امنیتی تصویر", h("input", { id: "sadafCaptcha", class: "sadaf-input sadaf-captcha", maxlength: "4", autocomplete: "off", spellcheck: "false", placeholder: "۴ حرف", onkeydown: async (event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              try { await submitCaptcha(); }
              catch (error) { note(error.message || "عملیات انجام نشد.", "err"); }
            } })]),
            button("ورود با این کد", submitCaptcha, "sadaf-btn primary"),
          ]),
          h("p", { class: "sadaf-hint" }, ["کد را از پنجرهٔ سدف بخوانید و اینجا بنویسید. رمز ذخیره پر می‌شود و دکمهٔ ورود زده می‌شود. اگر سایت قبول کند، همان تصویر برای آموزش مدل سدف نگه داشته می‌شود."]),
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
          h("h3", {}, [h("i", { class: "fa-solid fa-users" }), "فهرست مشتریان ", h("span", { id: "sadafPeopleCount", class: "sadaf-pill" }, ["۰"])]),
          h("div", { class: "sadaf-inline" }, [
            h("input", { id: "sadafSearch", class: "sadaf-input", placeholder: "جست‌وجوی نام، تلفن یا نام کاربری", oninput: queueSearch }),
            button("جست‌وجو", loadCustomers, "sadaf-btn ghost"),
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
      ]));
      toggleExtraOptions();
      fillForm(null);
      host.dataset.ready = "4";
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
  function valuesFromForm() {
    const fields = [];
    root.querySelectorAll("[data-field]").forEach((el) => {
      const kind = el.getAttribute("data-kind") || "text";
      let value = "";
      if (kind === "select") {
        const typed = (el.value || "").trim();
        value = choicesFor(el.getAttribute("data-field")).indexOf(typed) >= 0 ? typed : "";
      } else value = (el.value || "").trim();
      if (value) fields.push({ name: el.getAttribute("data-field"), value, kind });
    });
    extras.forEach((item) => {
      if (item.name && item.value) fields.push(item);
    });
    return fields;
  }
  function setControl(name, value) {
    const el = root.querySelector('[data-field="' + CSS.escape(name) + '"]');
    if (!el) return;
    el.value = value;
  }
  function fillForm(item) {
    editingId = item ? item.id : null;
    document.getElementById("sadafFormTitle").textContent = item ? "ویرایش مشتری" : "مشخصات مشتری";
    document.getElementById("sadafPassMark").textContent = item ? "" : "*";
    document.getElementById("sadafName").value = item ? item.full_name || "" : "";
    document.getElementById("sadafPhone").value = item ? item.phone || "" : "";
    document.getElementById("sadafUser").value = item ? item.username || "" : "";
    document.getElementById("sadafPass").value = "";
    const known = new Set(builtinNames());
    extras = [];
    root.querySelectorAll("[data-field]").forEach((el) => { el.value = ""; });
    (item ? item.fields || [] : DEFAULT_FIELDS.map(([name, value]) => ({ name, value }))).forEach((field) => {
      if (known.has(field.name)) setControl(field.name, field.value);
      else extras.push({ name: field.name, kind: field.kind || "text", value: field.value, options: field.options || "" });
    });
    drawExtras();
    document.getElementById("sadafSaveState").textContent = item ? "در حال ویرایش" : "فرم تازه";
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
  function clearForm() {
    fillForm(null);
    note("فرم متقاضی جدید با تنظیم گلخانه پر شد. برای صنعتی فقط سطح زیر کشت و لیتر را عوض کنید.", "ok");
  }
  async function persist() {
    const payload = {
      full_name: document.getElementById("sadafName").value.trim(),
      username: document.getElementById("sadafUser").value.trim(),
      phone: document.getElementById("sadafPhone").value.trim(),
      password: document.getElementById("sadafPass").value,
      fields: valuesFromForm(),
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
    document.getElementById("sadafFormTitle").textContent = "ویرایش مشتری";
    document.getElementById("sadafSaveState").textContent = "ذخیره شد";
    note("اطلاعات ذخیره شد. با «اجرای همین مشتری» همین رکورد از دیتابیس خوانده می‌شود.", "ok");
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
    const head = h("thead", {}, [h("tr", {}, ["نام", "تلفن", "نام کاربری", "وضعیت اطلاعات", "آخرین اجرا", "تاریخ", ""].map((title) => h("th", {}, [title])))]);
    const body = h("tbody", {}, items.map((item) => h("tr", {}, [
      h("td", {}, [item.full_name || "—"]),
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
    const events = await api("/api/sadaf/events?limit=12");
    document.getElementById("sadafLog").replaceChildren(...(events.items || []).map((item) => h("div", {}, [item.at + " — " + item.message])));
  }
  function stat(label, value) {
    return h("article", { class: "sadaf-stat" }, [h("small", {}, [label]), h("strong", {}, [fa(value)])]);
  }
  async function saveBrowser() {
    await api("/api/sadaf/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser: document.getElementById("sadafBrowser").value, base_url: "https://newtejaratasan.niopdc.ir" }),
    });
  }
  async function openSite() {
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
    note("کد فرستاده شد. ورود زده می‌شود.", "ok");
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
    const data = await api("/api/sadaf/settings");
    const box = document.getElementById("sadafBrowser");
    if (box && data.browser) box.value = data.browser;
  }
  function markNav(name) {
    const links = document.querySelectorAll("#sadafNav .nav-link");
    const index = { form: 0, run: 1, people: 2, log: 3 }[name];
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
    await loadSettings().catch(() => {});
    await refresh().catch((error) => note(error.message, "err"));
    await loadCustomers().catch((error) => note(error.message, "err"));
    await loadJahad().catch(() => {});
    await restoreSaved().catch((error) => note(error.message, "err"));
    if (!document.querySelector("#sadafNav .nav-link.active")) markNav("form");
    clearInterval(timer);
    let ticks = 0;
    timer = setInterval(() => {
      refresh().catch(() => {});
      ticks += 1;
      if (ticks % 5 === 0) loadJahad().catch(() => {});
    }, 2000);
  }
  async function restoreSaved() {
    if (restored || editingId) return;
    const username = document.getElementById("sadafUser");
    if (username && username.value.trim()) return;
    restored = true;
    const data = await api("/api/sadaf/customers");
    const items = data.items || [];
    if (!items.length) return;
    const latest = items.slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    const full = await api("/api/sadaf/customers/" + latest.id);
    fillForm(full);
    note("اطلاعات ذخیره‌شدهٔ «" + (full.full_name || full.username) + "» برگشت. رمز برای امنیت دوباره نشان داده نمی‌شود.", "ok");
  }
  function leave() { clearInterval(timer); }
  function resumeModule() {
    try {
      if (sessionStorage.getItem("mahanModule") === "sadaf") switchModule("sadaf");
    } catch (error) {}
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", resumeModule);
  else resumeModule();
  return { enter, leave, focus };
})();
