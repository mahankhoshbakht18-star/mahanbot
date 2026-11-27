# -*- coding: utf-8 -*-
"""Page script for the Sadaf form. It finds and marks elements; Python clicks them.

Buttons are marked with data-sadaf-click="<tag>" and clicked by Playwright
after the ASP.NET postback has finished, so a click is never lost inside a
partial postback. The runner reloads this file when it changes on disk.
"""

HELPERS_JS = r"""
(() => {
  const V = '__SADAF_VERSION__';
  if (window.__sadaf && window.__sadaf.v === V) return;
  const SELECT_IDS = {'بخش مصرف':'ddlKarbordType','گروه کاربرد':'ddlKarbordGroups','کاربرد':'ddlKarbordss','کاربرد مورد نظر':'ddlUsersKarbords'};
  const toLatin = s => String(s ?? '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[٠-٩]/g, d => '٠١٢٣٤٥٦٧٨٩'.indexOf(d));
  const norm = s => toLatin(s).replace(/[يى]/g,'ی').replace(/ك/g,'ک').replace(/ئ/g,'ی').replace(/[ۀة]/g,'ه').replace(/[آأإ]/g,'ا').replace(/[‌‎‏\s:：*]/g,'');
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const after = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const inModal = el => !!(el && el.closest && el.closest('.modal,[role=dialog],.jconfirm,.ui-dialog'));
  const FIELD = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=image]):not([type=checkbox]):not([type=radio]):not([type=password]),select,textarea';

  function labelCandidates(key) {
    const k = norm(key); const ex = [], st = [], inc = [];
    for (const el of document.querySelectorAll('td,th,span,label,div,p,b,strong,font,li')) {
      const t = norm(el.textContent);
      if (!t.includes(k) || t.length > k.length + 40) continue;
      if ([...el.children].some(c => norm(c.textContent).includes(k))) continue;
      (t === k ? ex : t.startsWith(k) ? st : inc).push(el);
    }
    // Visible labels outside dialogs first: the seller dialog has its own «نام فروشندگی».
    const score = el => (visible(el) ? 0 : 2) + (inModal(el) ? 1 : 0);
    const order = list => list.map((el, i) => [el, i]).sort((a, b) => score(a[0]) - score(b[0]) || a[1] - b[1]).map(x => x[0]);
    return [...order(ex), ...order(st), ...order(inc)];
  }
  function fieldNear(lab) {
    const pick = n => {
      if (!n || !n.querySelectorAll) return null;
      if (n.matches && n.matches('select')) return n;
      if (n.matches && n.matches(FIELD) && visible(n)) return n;
      return [...n.querySelectorAll(FIELD)].find(visible) || [...n.querySelectorAll('select')].find(el => el.options && el.options.length > 1) || null;
    };
    let f = pick(lab); if (f) return f;
    let a = lab;
    for (let i = 0; i < 4 && a && a.tagName !== 'TR'; i++, a = a.parentElement)
      for (let s = a.nextElementSibling; s; s = s.nextElementSibling) { f = pick(s); if (f) return f; }
    return null;
  }
  function findField(key) {
    if (key.startsWith('#')) return document.getElementById(key.slice(1));
    for (const [name, id] of Object.entries(SELECT_IDS))
      if (norm(name) === norm(key)) { const el = document.getElementById(id); if (el) return el; }
    if (key.includes('|')) {
      const [prod, lab] = key.split('|');
      const prodCell = labelCandidates(prod)[0]; if (!prodCell) return null;
      const labs = labelCandidates(lab).filter(l => after(prodCell, l)).sort((a, b) => after(a, b) ? -1 : 1);
      for (const l of labs) { const f = fieldNear(l); if (f && after(prodCell, f)) return f; }
      return null;
    }
    for (const l of labelCandidates(key)) { const f = fieldNear(l); if (f) return f; }
    return null;
  }
  function setText(el, v) {
    v = toLatin(v);
    if (el.value === v) return false;
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  }
  function loginFields() {
    const pw = [...document.querySelectorAll('input[type=password]')].find(visible);
    if (!pw) return null;
    const texts = [...document.querySelectorAll('input[type=text],input:not([type])')].filter(visible);
    return {pw, user: texts.filter(t => after(t, pw)).pop(), cap: texts.find(t => after(pw, t))};
  }
  window.__sadaf = {
    v: V,
    isLoginPage: () => !!loginFields(),
    fillLogin(user, pass, onlyIfEmpty) {
      const f = loginFields(); if (!f || !f.user) return 'nologin';
      if (onlyIfEmpty && f.pw.value) return 'kept';
      setText(f.user, user); setText(f.pw, pass);
      if (f.cap && !f.cap.value) { f.cap.style.outline = '3px solid #00BFB3'; f.cap.style.outlineOffset = '2px'; f.cap.scrollIntoView({block: 'center'}); }
      return 'ok';
    },
    captchaBlank() {
      const box = document.getElementById('txtCode');
      return !box || !String(box.value || '').trim();
    },
    fillCaptcha(code) {
      const box = document.getElementById('txtCode');
      if (!box || !code) return false;
      const text = String(code).toLowerCase().slice(0, box.maxLength > 0 ? box.maxLength : 4);
      setText(box, text);
      box.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
      return box.value === text;
    },
    freshCaptcha() {
      const box = document.getElementById('txtCode');
      if (box) box.value = '';
      if (typeof LBD_ReloadImage === 'function') {
        LBD_ReloadImage('login_captcha1_CaptchaImage');
        return true;
      }
      const link = document.querySelector("#LBD_CaptchaIcons a");
      if (link) { link.click(); return true; }
      return false;
    },
    loginLines() {
      return (document.body.innerText || '').split(/\n/).map(s => s.trim()).filter(Boolean);
    },
    loginProblem() {
      const lines = this.loginLines();
      const bad = lines.filter(s => ['اشتباه', 'نادرست', 'نامعتبر', 'صحیح نیست'].some(k => s.includes(k)));
      if (!bad.length) return '';
      const line = bad.join(' ');
      if (line.includes('کد') || line.includes('امني') || line.includes('کپچا')) return 'captcha';
      if (line.includes('کاربری') || line.includes('عبور') || line.includes('رمز')) return 'credentials';
      return 'captcha';
    },
    loginDetail() {
      const lines = this.loginLines();
      const hit = lines.filter(s => ['نامعتبر', 'اشتباه', 'نادرست', 'صحیح نیست', 'کد اختصاصی'].some(k => s.includes(k)));
      return hit.slice(0, 2).join(' — ');
    },
    pageHasUser: user => norm(document.body.innerText).includes(norm(user)),
    siteUserName() {
      // Header of the protected pages: «Menu1_lblName» holds the account owner's name.
      const el = document.getElementById('Menu1_lblName') || document.querySelector('[id$="_lblName"]');
      return el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '';
    },
    lines() {
      return (document.body.innerText || '').split(/\n/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
    },
    busy() {
      const prm = window.Sys && Sys.WebForms && Sys.WebForms.PageRequestManager && Sys.WebForms.PageRequestManager.getInstance();
      const ajax = !!(window.jQuery && window.jQuery.active > 0);
      return !!(prm && prm.get_isInAsyncPostBack()) || ajax || document.readyState !== 'complete';
    },
    inModal: el => inModal(el),
    openModal() {
      const shown = [...document.querySelectorAll('.modal,[role=dialog],.ui-dialog')].filter((m) => visible(m) && getComputedStyle(m).display !== 'none' && getComputedStyle(m).visibility !== 'hidden');
      return shown.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0] || null;
    },
    mark(el, tag) {
      document.querySelectorAll('[data-sadaf-click="' + tag + '"]').forEach((item) => item.removeAttribute('data-sadaf-click'));
      if (!el) return false;
      el.setAttribute('data-sadaf-click', tag);
      el.scrollIntoView({block: 'center'});
      return true;
    },
    buttons(scope) {
      return [...(scope || document).querySelectorAll('input[type=submit],input[type=button],input[type=image],button,a')].filter(visible);
    },
    buttonText(el) {
      return norm(el.value || el.textContent || el.title || '');
    },
    selectStep(pairs) {
      const cascade = ['بخش مصرف', 'گروه کاربرد', 'کاربرد', 'کاربرد مورد نظر', 'نوع فروشندگی', 'نوع مصرف سوخت'];
      const rank = k => { const i = cascade.findIndex(n => norm(n) === norm(k)); return i < 0 ? 99 : i; };
      const ranked = [...pairs].sort((a, b) => rank(a[0]) - rank(b[0]));
      const placeholder = t => ['', 'انتخابکنید', 'انتخاب', 'همه', '-'].includes(norm(t));
      const deferred = [];
      for (const [k, v] of ranked) {
        if (norm(k) === norm('نام فروشندگی')) continue;
        const el = findField(k);
        if (!el || el.tagName !== 'SELECT' || inModal(el)) continue;
        const want = norm(v), opts = [...el.options];
        const o = opts.find(x => norm(x.text) === want)
          || opts.find(x => norm(x.value) === want)
          || opts.find(x => want && norm(x.text).includes(want) && !placeholder(x.text));
        if (!o) {
          if (rank(k) === 99) continue;
          const loaded = opts.some(x => !placeholder(x.text));
          if (!loaded) { deferred.push(k); continue; }
          return {error: k, options: opts.map(x => x.text.trim()).filter(Boolean)};
        }
        const shown = document.getElementById('select2-' + el.id + '-container');
        if (el.value === o.value) {
          if (shown && norm(shown.textContent) !== norm(o.text)) { shown.textContent = o.text.trim(); shown.title = o.text.trim(); }
          continue;
        }
        if (window.jQuery) window.jQuery(el).val(o.value).trigger('change');
        else {
          el.value = o.value;
          el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        if (shown) { shown.textContent = o.text.trim(); shown.title = o.text.trim(); }
        return {changed: k};
      }
      if (deferred.length) {
        const k = deferred[0];
        const el = findField(k);
        const options = el ? [...el.options].map(x => x.text.trim()).filter(Boolean) : [];
        return {error: k, options, empty: true};
      }
      return {done: true};
    },
    fillTexts(pairs) {
      const missing = [];
      for (const [k, v] of pairs) {
        if (norm(k) === norm('نام فروشندگی')) continue;
        const el = findField(k);
        if (!el) { missing.push(k); continue; }
        if (el.tagName !== 'SELECT' && !inModal(el)) setText(el, v);
      }
      return {missing};
    },
    wrongFields(pairs) {
      const wrong = [];
      for (const [k, v] of pairs) {
        if (norm(k) === norm('نام فروشندگی')) continue;
        const el = findField(k);
        if (!el || inModal(el)) continue;
        if (el.tagName === 'SELECT') {
          const text = ((el.options[el.selectedIndex] || {}).text || '');
          if (norm(text) !== norm(v) && !norm(text).includes(norm(v))) wrong.push(k + ' = ' + text.trim());
        } else if (norm(el.value) !== norm(v)) wrong.push(k + ' = ' + el.value);
      }
      return wrong;
    },
    sellerDialog() {
      const modal = this.openModal();
      if (modal && /فروشند/.test(modal.innerText || '')) return modal;
      const nodes = [...document.querySelectorAll('div,table')].filter((el) => {
        if (!visible(el) || !el.querySelector('input,button')) return false;
        return /لیست فروشندگی|ليست فروشندگي/.test(el.innerText || '');
      });
      return nodes.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0] || null;
    },
    sellerGrid() {
      const root = this.sellerDialog();
      if (!root) return null;
      const tables = [...root.querySelectorAll('table')].filter(visible);
      const hits = tables.filter((table) => [...table.querySelectorAll('tr')].some((row) =>
        [...row.querySelectorAll('input,button,a')].some((el) => visible(el) && this.buttonText(el) === norm('انتخاب'))));
      return hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0] || null;
    },
    markOpenSellers() {
      if (this.sellerSearchBox()) return 'open';
      const all = this.buttons().filter((el) => !inModal(el));
      const button = all.find((el) => this.buttonText(el) === norm('انتخاب فروشندگی'))
        || all.find((el) => this.buttonText(el).includes(norm('انتخاب فروشند')));
      return this.mark(button, 'sellers') ? 'marked' : '';
    },
    sellerSearchBox() {
      const root = this.sellerDialog();
      if (!root) return null;
      const want = norm('نام فروشندگی');
      const label = [...root.querySelectorAll('td,th,span,label,div')].find((el) => {
        if (!visible(el)) return false;
        const text = norm(el.textContent);
        if (!(text === want || text.startsWith(want)) || text.length > want.length + 6) return false;
        return ![...el.children].some((child) => norm(child.textContent).includes(want));
      });
      let box = label ? fieldNear(label) : null;
      if (!box || box.tagName === 'SELECT' || !visible(box)) {
        box = [...root.querySelectorAll('input[type=text],input:not([type]),input[type=search]')].find(visible) || null;
      }
      return box;
    },
    searchSeller(name) {
      const root = this.sellerDialog();
      const box = this.sellerSearchBox();
      if (!root || !box) return '';
      setText(box, name);
      if (!norm(box.value).includes(norm(name))) return '';
      document.querySelectorAll('[data-sadaf-click="sellerbox"]').forEach((el) => el.removeAttribute('data-sadaf-click'));
      box.setAttribute('data-sadaf-click', 'sellerbox');
      const all = this.buttons(root);
      const button = all.find((el) => this.buttonText(el) === norm('جستجو')) || all.find((el) => this.buttonText(el).includes(norm('جستجو')));
      return this.mark(button, 'search') ? 'marked' : 'typed';
    },
    sellerRows() {
      const table = this.sellerGrid();
      if (!table) return null;
      const found = [];
      for (const row of table.querySelectorAll('tr')) {
        const button = [...row.querySelectorAll('input,button,a')].find((el) => visible(el) && this.buttonText(el) === norm('انتخاب'));
        if (!button) continue;
        const cells = [...row.children].filter((cell) => !cell.contains(button));
        const texts = cells.map((cell) => (cell.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
        const label = texts.find((text) => /[آ-یءيك]/.test(text) && text.length <= 60) || texts[0] || '';
        found.push({label, button, row});
      }
      return found;
    },
    chooseSeller(name) {
      const want = norm(name);
      const found = this.sellerRows();
      if (!found) return {waiting: true, names: []};
      const exact = found.filter((item) => norm(item.label) === want);
      const hits = exact.length ? exact : found.filter((item) => norm(item.label).includes(want));
      const names = found.map((item) => item.label);
      document.querySelectorAll('[data-sadaf-row]').forEach((el) => el.removeAttribute('data-sadaf-row'));
      if (hits.length === 1) {
        this.mark(hits[0].button, 'pick');
        hits[0].row.setAttribute('data-sadaf-row', '1');
        return {picked: hits[0].label, names};
      }
      return {names, many: hits.length > 1};
    },
    pickedRow() {
      const row = document.querySelector('[data-sadaf-row="1"]');
      if (!row) return {gone: true};
      return {cls: String(row.className || ''), bg: getComputedStyle(row).backgroundColor, text: (row.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120)};
    },
    sellerHighlighted() {
      // The grid paints the selected row with an inline background colour (e.g. #6666FF).
      const found = this.sellerRows() || [];
      const hit = found.find((item) => /background-color/i.test(item.row.getAttribute('style') || '')
        || /select/i.test(String(item.row.className || '')));
      return hit ? hit.label : '';
    },
    sellerPagerLinks() {
      const table = this.sellerGrid();
      if (!table) return [];
      return [...table.querySelectorAll('a[href*="Page$"]')].filter(visible).map((a) => {
        const match = /Page\$(\d+)/.exec(a.getAttribute('href') || '');
        return match ? {page: Number(match[1]), link: a} : null;
      }).filter(Boolean);
    },
    sellerPageTargets() {
      return [...new Set(this.sellerPagerLinks().map((item) => item.page))].sort((a, b) => a - b);
    },
    sellerCurrentPage() {
      const links = this.sellerPagerLinks();
      if (!links.length) return 0;
      const pager = links[0].link.closest('tr');
      const span = pager && [...pager.querySelectorAll('span')].find((el) => visible(el) && /^\d+$/.test(toLatin(el.textContent).trim()));
      return span ? Number(toLatin(span.textContent).trim()) : 0;
    },
    markSellerPage(n) {
      const hit = this.sellerPagerLinks().find((item) => item.page === Number(n));
      return this.mark(hit && hit.link, 'sellerpage');
    },
    clearSellerSearch() {
      const box = this.sellerSearchBox();
      if (!box) return false;
      setText(box, '');
      return true;
    },
    markConfirmSeller() {
      const root = this.sellerDialog() || document;
      const ok = this.buttons(root).find((el) => {
        if (this.buttonText(el) !== norm('تایید')) return false;
        let node = el;
        for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
          if (this.buttons(node).some((item) => this.buttonText(item) === norm('انصراف'))) return true;
        }
        return false;
      });
      return this.mark(ok, 'sellerok');
    },
    sellerOpen() {
      return this.buttons().some((el) => this.buttonText(el) === norm('انصراف'));
    },
    chosenSeller() {
      if (this.sellerOpen()) return '';
      const want = norm('نام فروشندگی');
      const labels = [...document.querySelectorAll('td,th,span,label,div')].filter((el) => {
        if (!visible(el) || inModal(el)) return false;
        const text = norm(el.textContent);
        if (!(text === want || text.startsWith(want)) || text.length > want.length + 6) return false;
        return ![...el.children].some((child) => norm(child.textContent).includes(want));
      });
      for (const label of labels) {
        const host = label.closest('td,div') || label.parentElement;
        const scopes = [label.nextElementSibling, host && host.nextElementSibling, host];
        for (const scope of scopes) {
          if (!scope) continue;
          const input = [...scope.querySelectorAll('input[type=text],input:not([type]),textarea')].find((el) => visible(el) && el.value);
          if (input) return input.value.replace(/\s+/g, ' ').trim();
          const select = [...scope.querySelectorAll('select')].find((el) => el.selectedIndex >= 0);
          if (select) {
            const text = (select.options[select.selectedIndex] || {}).text || '';
            if (text && norm(text) !== norm('همه')) return text.trim();
          }
          if (scope !== host) {
            const text = (scope.innerText || '').replace(/\s+/g, ' ').trim();
            if (text && norm(text) !== want && text.length <= 80) return text;
          }
        }
      }
      return '';
    },
    markNext() {
      const next = this.buttons().find((el) => {
        if (el.id === 'SubCon' || inModal(el)) return false;
        return this.buttonText(el) === norm('ادامه');
      });
      return this.mark(next, 'next');
    },
    markTerms() {
      const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter((el) => visible(el) && !inModal(el));
      const box = boxes.find((el) => {
        const text = ((el.closest('label,td,span,div') || el.parentElement || el).innerText) || '';
        if (/دیزل|ديزل|ژنراتور/.test(text)) return false;
        return /ماده\s*(44|۴۴)|تعهد|دستورالعمل|مسئولیت|مسئوليت|ماده/.test(text);
      });
      if (!box) return '';
      this.mark(box, 'terms');
      return box.checked ? 'checked' : 'marked';
    },
    termsChecked() {
      const box = document.querySelector('[data-sadaf-click="terms"]');
      return !!(box && box.checked);
    },
    formCaptcha() {
      const label = [...document.querySelectorAll('td,div,span,label,p')].find((el) => {
        if (!visible(el)) return false;
        const text = el.innerText || '';
        if (!/کد مقابل|كد مقابل/.test(text) || text.length > 120) return false;
        return ![...el.children].some((child) => /کد مقابل|كد مقابل/.test(child.innerText || ''));
      });
      if (!label) return null;
      let image = null;
      let node = label;
      for (let depth = 0; depth < 6 && node && !image; depth += 1, node = node.parentElement) {
        image = [...node.querySelectorAll('img')].find((el) => visible(el) && !/login_captcha/i.test(el.id || '') && el.naturalWidth > 30) || null;
      }
      if (!image) return null;
      document.querySelectorAll('[data-sadaf-cap]').forEach((el) => el.removeAttribute('data-sadaf-cap'));
      image.setAttribute('data-sadaf-cap', '1');
      let box = null;
      node = label;
      for (let depth = 0; depth < 6 && node && !box; depth += 1, node = node.parentElement) {
        box = [...node.querySelectorAll('input[type=text],input:not([type])')].find((el) => visible(el) && !el.readOnly && after(label, el)) || null;
      }
      if (!box) box = fieldNear(label);
      if (box && box.tagName === 'SELECT') box = null;
      document.querySelectorAll('[data-sadaf-capbox]').forEach((el) => el.removeAttribute('data-sadaf-capbox'));
      if (box) box.setAttribute('data-sadaf-capbox', '1');
      return {src: image.getAttribute('src') || '', max: box && box.maxLength > 0 ? box.maxLength : 0, box: !!box, value: box ? box.value : ''};
    },
    fillFormCaptcha(code) {
      const box = document.querySelector('[data-sadaf-capbox="1"]');
      if (!box || !code) return false;
      const text = String(code).slice(0, box.maxLength > 0 ? box.maxLength : 8);
      setText(box, text);
      return norm(box.value) === norm(text);
    },
    markRequestCode() {
      const button = this.buttons().find((el) => {
        if (inModal(el)) return false;
        const text = this.buttonText(el);
        return text.includes(norm('دریافت کد')) || text.includes(norm('ارسال مجدد')) || text.includes(norm('دریافت پیامک')) || text.includes(norm('ارسال کد'));
      });
      return this.mark(button, 'getcode');
    },
    smsBox() {
      // «کد پيامك شده»: appears after the first «ثبت مشخصات» press has sent the code.
      const direct = document.getElementById('txtsecurityCode');
      if (direct && visible(direct)) return direct;
      const cap = document.querySelector('[data-sadaf-capbox="1"]');
      for (const key of ['کد پیامک', 'کد ارسال', 'کد دریافتی', 'کد تایید', 'کد یکبار', 'پیامک']) {
        for (const lab of labelCandidates(key)) {
          if (!visible(lab) || inModal(lab)) continue;
          const el = fieldNear(lab);
          if (el && el.tagName !== 'SELECT' && el !== cap && visible(el)) return el;
        }
      }
      const getcode = document.querySelector('[data-sadaf-click="getcode"]');
      if (getcode) {
        const scope = getcode.closest('tr') || getcode.parentElement;
        const near = scope ? [...scope.querySelectorAll('input[type=text],input:not([type]),input[type=tel],input[type=number]')].find((el) => visible(el) && el !== cap) : null;
        if (near) return near;
      }
      return null;
    },
    smsField() {
      return !!this.smsBox();
    },
    smsPanel() {
      const box = document.getElementById('txtsecurityCode');
      const note = document.getElementById('lblReciveCodeSms');
      const resend = document.getElementById('lnkbtn');
      const open = !!(box && visible(box)) || !!(note && visible(note) && this.smsBox()) || !!(resend && visible(resend) && this.smsBox());
      const mess = document.getElementById('lblMess');
      return {open, message: mess ? (mess.innerText || '').replace(/\s+/g, ' ').trim() : ''};
    },
    markResendSms() {
      const link = document.getElementById('lnkbtn');
      const el = link && visible(link) ? link
        : this.buttons().find((item) => !inModal(item) && this.buttonText(item).includes(norm('ارسال مجدد')));
      return this.mark(el || null, 'resend');
    },
    clearMessages(lines) {
      // Blank the message labels the last press showed, so the same error shown again counts as new.
      const want = new Set((lines || []).map((s) => String(s).replace(/\s+/g, ' ').trim()).filter(Boolean));
      if (!want.size) return 0;
      let count = 0;
      for (const el of document.querySelectorAll('span,div,td,p,font,b,label,li')) {
        if (el.children.length || !visible(el)) continue;
        if (!want.has((el.innerText || '').replace(/\s+/g, ' ').trim())) continue;
        const hint = (el.id || '') + ' ' + String(el.className || '') + ' ' + (el.getAttribute('style') || '');
        if (!/mess|msg|err|alert|warn|red/i.test(hint)) continue;
        el.textContent = '';
        count += 1;
      }
      return count;
    },
    fillSms(code) {
      const el = this.smsBox();
      if (!el) return false;
      el.setAttribute('data-sadaf-sms', '1');
      setText(el, code);
      return norm(el.value) === norm(code);
    },
    markConfirmRequest() {
      if (this.sellerOpen()) return false;
      const all = this.buttons().filter((el) => !inModal(el));
      const cap = document.querySelector('[data-sadaf-capbox="1"]');
      const named = all.find((el) => this.buttonText(el) === norm('ثبت مشخصات'));
      if (named) return this.mark(named, 'final');
      const oks = all.filter((el) => this.buttonText(el) === norm('تایید') && (!cap || after(cap, el)));
      return this.mark(oks[0] || null, 'final');
    },
  };
})();
"""
