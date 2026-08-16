// extension/content_autofill.js
// Application Answer Autofill: anchors a Navigator button to focused application
// answer fields, extracts question/company/position context, asks the background
// service worker to generate an answer, and shows a review popover
// (Insert / Copy / Save to bank / Regenerate) before writing anything into the page.
(async () => {
  const cfg = await chrome.storage.sync.get(['autofillEnabled', 'autofillDefaultLength']);
  if (!cfg.autofillEnabled) return;
  const DEFAULT_LEN = parseInt(cfg.autofillDefaultLength, 10) || 250;  // popup-configurable

  const NAV_ICON = 'data:image/png;base64,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'; // real Navigator icon, inlined (no web-accessible resource)
  let popoverHost = null; // shadow host for the review popover
  let currentField = null; // field whose pill is currently shown (for popover focusout logic)
  const fieldState = new Map(); // fieldKey(el) -> { text, error, busy, promise, maxChars } — keyed by a stable id so state survives the page re-rendering the field element

  const isAnswerField = (el) => {
    if (!el || el.readOnly || el.disabled) return false;
    // Skip searchable dropdowns / comboboxes / autocomplete pickers — many ATS build
    // these as a text <input>, which would otherwise match the free-text heuristic.
    if (el.getAttribute('role') === 'combobox' ||
        el.getAttribute('aria-autocomplete') ||
        el.getAttribute('aria-haspopup') === 'listbox' ||
        el.hasAttribute('list')) return false;
    return (el.tagName === 'TEXTAREA') ||
      (el.tagName === 'INPUT' && el.type === 'text' && (el.maxLength > 60 || el.maxLength === -1)) ||
      el.isContentEditable;
  };

  function questionFor(el) {
    // label[for=id] -> aria-labelledby -> aria-label -> placeholder -> nearest preceding text
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return lab.innerText.trim();
    }
    const ariaId = el.getAttribute('aria-labelledby');
    if (ariaId) { const n = document.getElementById(ariaId); if (n) return n.innerText.trim(); }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    if (el.placeholder) return el.placeholder.trim();
    let p = el.closest('label') || el.parentElement;
    for (let i = 0; p && i < 4; i++, p = p.parentElement) {
      const t = (p.innerText || '').trim();
      if (t && t.length < 300) return t.split('\n')[0].trim();
    }
    return '';
  }

  function pageCompany() {
    // JSON-LD JobPosting -> og:site_name -> hostname
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const j = JSON.parse(s.textContent);
        const o = Array.isArray(j) ? j.find(x => x['@type'] === 'JobPosting') : j;
        if (o && o.hiringOrganization && o.hiringOrganization.name) return o.hiringOrganization.name;
      } catch {}
    }
    const og = document.querySelector('meta[property="og:site_name"]');
    return (og && og.content) || location.hostname.replace(/^www\./, '');
  }

  function pagePosition() {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const j = JSON.parse(s.textContent);
        const o = Array.isArray(j) ? j.find(x => x['@type'] === 'JobPosting') : j;
        if (o && o.title) return o.title;
      } catch {}
    }
    const og = document.querySelector('meta[property="og:title"]');
    return (og && og.content) || document.title;
  }

  function maxCharsFor(el) {
    if (el.maxLength && el.maxLength > 0) return el.maxLength;
    return null;  // background/backend applies the default from settings
  }

  function fieldKey(el) {
    // Stable key so per-field state survives the page re-rendering the element.
    return el.id || el.name || (questionFor(el) ? 'q:' + questionFor(el) : 'autofill-field');
  }

  function removePopover() { if (popoverHost) { popoverHost.remove(); popoverHost = null; } }

  // ── Field widget ───────────────────────────────────────────────────────────
  // ONE persistent element per field that morphs pill ⇄ loading ⇄ done IN PLACE,
  // so state changes animate via CSS transitions instead of swapping DOM nodes
  // (the standard "button that turns into a spinner" pattern). Keyed by fieldKey
  // so several fields can be mid-generation at once, and it survives the popover
  // opening/closing and the field losing focus while a generation is in flight.
  const widgets = new Map(); // fieldKey -> { host, node, el, key, state }
  const WIDGET_CSS = `
    .wrap{position:relative;width:26px;height:26px;pointer-events:none}
    .btn{pointer-events:auto;position:absolute;right:0;top:0;box-sizing:border-box;height:26px;
         max-width:26px;display:flex;flex-direction:row-reverse;align-items:center;
         border-radius:13px;border:1.5px solid #3B82F6;background:#fff;cursor:pointer;
         box-shadow:0 1px 4px rgba(0,0,0,.2);overflow:hidden;padding:0;white-space:nowrap;
         transition:max-width .35s cubic-bezier(.4,0,.2,1),border-color .2s}
    .wrap.pill .btn:hover{max-width:260px}
    .wrap.loading .btn,.wrap.done .btn{cursor:default}
    .wrap.loading .btn{border-color:transparent}  /* the spinning ring becomes the border */
    .icon{width:18px;height:18px;margin:3px;flex:0 0 auto;transition:opacity .2s}
    .lbl{font:600 12px system-ui;color:#3B82F6;padding:0 4px 0 10px;flex:0 0 auto}
    .ring{position:absolute;inset:0;box-sizing:border-box;border:1.5px solid rgba(59,130,246,.25);
          border-top-color:#3B82F6;border-radius:50%;opacity:0;transition:opacity .2s;
          animation:sp .7s linear infinite;pointer-events:none}
    .wrap.loading .ring{opacity:1}
    .chk{position:absolute;left:50%;top:50%;width:16px;height:16px;fill:none;stroke:#3B82F6;
         stroke-width:3;stroke-linecap:round;stroke-linejoin:round;pointer-events:none;
         opacity:0;transform:translate(-50%,-50%) scale(.3);transition:opacity .2s,transform .2s}
    .wrap.done .chk{opacity:1;transform:translate(-50%,-50%) scale(1)}
    .wrap.done .icon{opacity:0}
    @keyframes sp{to{transform:rotate(360deg)}}`;

  function positionWidget(w) {
    const r = w.el.getBoundingClientRect();
    // Right-aligned rail so the pill grows leftward while the icon stays pinned
    // to the field's bottom-right corner (3px inset).
    w.host.style.top = `${window.scrollY + r.bottom - 29}px`;
    w.host.style.left = `${window.scrollX + r.right - 273}px`;
  }

  function widgetFor(el) {
    const key = fieldKey(el);
    let w = widgets.get(key);
    if (!w) {
      const host = document.createElement('div');
      host.style.cssText = 'position:absolute;z-index:2147483647;display:flex;justify-content:flex-end;align-items:flex-end;width:270px;height:26px;pointer-events:none;';
      const root = host.attachShadow({ mode: 'open' });
      root.innerHTML = `<style>${WIDGET_CSS}</style>
        <div class="wrap pill">
          <button class="btn" title="Generate with JobNavigator">
            <img class="icon" src="${NAV_ICON}" alt="Navigator">
            <span class="lbl">Generate with JobNavigator</span>
          </button>
          <span class="ring"></span>
          <svg class="chk" viewBox="0 0 24 24"><path d="M4 12l5 5L20 6"/></svg>
        </div>`;
      root.querySelector('.btn').addEventListener('click', (ev) => {
        ev.preventDefault(); ev.stopPropagation();
        // Opens the popover in the right mode for the field's state: first-time
        // generate (pill), the live loader (loading), or the saved answer (done).
        const cur = widgets.get(key);
        if (cur) onGenerate(cur.el);
      });
      document.body.appendChild(host);
      w = { host, node: root.querySelector('.wrap'), el, key, state: 'pill' };
      widgets.set(key, w);
    }
    w.el = el;              // the page may have re-rendered the field element
    positionWidget(w);
    return w;
  }

  function setWidgetState(w, state) {
    w.node.classList.remove('pill', 'loading', 'done');
    w.node.classList.add(state);
    w.state = state;
  }
  function removeWidget(key) {
    const w = widgets.get(key);
    if (w) { w.host.remove(); widgets.delete(key); }
  }

  // Generation lifecycle → widget state (works whether the popover is open or not).
  function markLoading(el) { setWidgetState(widgetFor(el), 'loading'); }
  function settleWidget(key) {
    // After the check has shown (or on error): back to pill if the field is still
    // focused, otherwise remove the widget entirely.
    const w = widgets.get(key);
    if (!w) return;
    if (document.activeElement === w.el) setWidgetState(w, 'pill'); else removeWidget(key);
  }
  function markError(key) { settleWidget(key); }
  function markDone(key) {
    const w = widgets.get(key);
    if (!w) return;
    setWidgetState(w, 'done');            // loader → blue check
    setTimeout(() => { const cur = widgets.get(key); if (cur === w && w.state === 'done') settleWidget(key); }, 1800);
  }

  function showButton(el) {
    removePopover();
    currentField = el;
    const w = widgetFor(el);
    if (w.state !== 'loading' && w.state !== 'done') setWidgetState(w, 'pill');
  }

  function onGenerate(el) {
    const fieldMax = maxCharsFor(el);
    const key = fieldKey(el);
    let st = fieldState.get(key);
    if (!st) {
      st = { text: null, error: null, busy: false, promise: null, maxChars: fieldMax || DEFAULT_LEN };
      fieldState.set(key, st);
    }
    showPopover(el, { question: questionFor(el), company: pageCompany(), position: pagePosition(), fieldMax }, st);
  }

  function showPopover(el, ctx, st) {
    removePopover();  // the field widget stays and morphs to loading via generate()
    const key = fieldKey(el);
    currentField = null;
    const pop = document.createElement('div');
    pop.style.cssText = 'position:absolute;z-index:2147483647;';
    const root = pop.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        .card{width:720px;max-width:92vw;background:#111827;color:#fff;border-radius:10px;
              padding:12px;box-shadow:0 6px 24px rgba(0,0,0,.35);font:13px system-ui}
        textarea{width:100%;min-height:150px;background:#1f2937;color:#fff;border:1px solid #374151;
                 border-radius:6px;padding:8px;box-sizing:border-box;resize:vertical}
        .row{display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap}
        button{border:0;border-radius:6px;padding:6px 10px;cursor:pointer;font-weight:600}
        button:disabled{opacity:.5;cursor:default}
        label{color:#9CA3AF;display:flex;align-items:center;gap:4px}
        input[type=number]{width:64px;background:#1f2937;color:#fff;border:1px solid #374151;
               border-radius:6px;padding:5px 6px;font:13px system-ui}
        .primary{background:#3B82F6;color:#fff}.ghost{background:#374151;color:#fff}
        .count{margin-left:auto;color:#9CA3AF}
        .spin{width:13px;height:13px;border:2px solid #9CA3AF;border-top-color:transparent;
              border-radius:50%;display:inline-block;animation:sp .7s linear infinite;vertical-align:middle;margin-right:5px}
        @keyframes sp{to{transform:rotate(360deg)}}
      </style>
      <div class="card">
        <textarea id="ans" readonly placeholder="Generating…"></textarea>
        <div class="row">
          <label>Length <input id="len" type="number" min="50" max="4000" step="50" value="${st.maxChars}"> chars</label>
          <button class="primary" id="insert" disabled>Insert</button>
          <button class="ghost" id="copy" disabled>Copy</button>
          <button class="ghost" id="save" disabled>Save to bank</button>
          <button class="ghost" id="regen" disabled>Regenerate</button>
          <span class="count" id="count"></span>
        </div>
      </div>`;
    const r = el.getBoundingClientRect();
    pop.style.top = `${window.scrollY + r.bottom + 6}px`;
    pop.style.left = `${window.scrollX + r.left}px`;
    document.body.appendChild(pop);
    popoverHost = pop;

    const ta = root.getElementById('ans');
    const count = root.getElementById('count');
    const lenInp = root.getElementById('len');
    const saveBtn = root.getElementById('save');
    const actionBtns = ['insert', 'copy', 'save', 'regen'].map(id => root.getElementById(id));
    const upd = () => { count.textContent = `${ta.value.length}/${st.maxChars}`; };
    ta.addEventListener('input', () => { upd(); st.text = ta.value; });  // persist edits per field
    // Focus so the popover keeps focus (the button that opened it was just removed;
    // otherwise the focusout handler tears the popover down ~150ms later).
    ta.focus();

    const setBusy = (busy) => {
      actionBtns.forEach(b => { b.disabled = busy; });
      ta.readOnly = busy;
      if (busy) { ta.value = ''; count.innerHTML = '<span class="spin"></span>Generating…'; }
    };

    const render = () => {
      setBusy(false);
      if (st.text != null) { ta.value = st.text; upd(); ta.focus(); }
      else { ta.value = ''; count.textContent = 'Failed: ' + (st.error || 'unknown'); }
    };

    const generate = async () => {
      setBusy(true);
      // Keep focus in the popover: setBusy disables the action buttons, and if one
      // (e.g. Regenerate) was focused, blurring it would drop focus to <body> and the
      // focusout handler would close the popover mid-generation.
      ta.focus();
      saveBtn.textContent = 'Save to bank';
      st.busy = true; st.text = null; st.error = null;
      markLoading(el);  // field pill morphs to the loader (popover open or closed)
      const p = chrome.runtime.sendMessage({
        type: 'autofill_generate',
        question: ctx.question, company: ctx.company, position: ctx.position,
        max_chars: st.maxChars,
      });
      st.promise = p;
      const resp = await p;
      // Store the result on the field even if this popover was closed meanwhile,
      // so reopening the same field shows it instead of regenerating from scratch.
      st.busy = false;
      if (resp && resp.answer) { st.text = resp.answer; st.error = null; }
      else { st.text = null; st.error = (resp && resp.error) || 'unknown'; }
      if (popoverHost === pop) render();
      // Field indicator resolves the same way whether the popover is open or closed:
      if (st.error) markError(key); else markDone(key);  // loader → blue check → settle
    };

    // Route through removePopover so the busy badge (if generating) is shown.
    const close = () => { if (popoverHost === pop) removePopover(); else pop.remove(); };
    root.getElementById('insert').onclick = () => { fillField(el, ta.value); close(); };
    root.getElementById('copy').onclick = () => navigator.clipboard.writeText(ta.value);
    saveBtn.onclick = async () => {
      const resp = await chrome.runtime.sendMessage({ type: 'autofill_save', question: ctx.question, answer: ta.value });
      const ok = resp && resp.count !== undefined && !resp.error;
      saveBtn.textContent = ok ? 'Saved' : 'Save failed';
    };
    root.getElementById('regen').onclick = generate;
    lenInp.addEventListener('change', () => {
      const v = parseInt(lenInp.value, 10);
      if (v > 0) { st.maxChars = v; generate(); }
    });
    document.addEventListener('mousedown', function onOut(ev) {
      if (!pop.contains(ev.target)) { close(); document.removeEventListener('mousedown', onOut); }
    });

    // Restore the field's prior state instead of always regenerating.
    if (st.busy && st.promise) {
      markLoading(el);                                 // keep the field loader visible
      setBusy(true);                                   // a generation is still in flight
      st.promise.then(() => { if (popoverHost === pop) render(); });
    } else if (st.text != null) {
      render();                                        // show the answer we already have
    } else {
      generate();                                      // first time for this field
    }
  }

  function fillField(el, value) {
    if (el.isContentEditable) {
      el.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, value);  // works for most contenteditable; Draft/Slate deferred to v2
    } else {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  document.addEventListener('focusin', (e) => {
    if (isAnswerField(e.target)) showButton(e.target);
  }, true);
  document.addEventListener('focusout', () => {
    setTimeout(() => {
      const active = document.activeElement;
      // Remove only idle (pill) widgets that lost focus; loading/done widgets
      // persist through generation and the brief check regardless of focus.
      for (const [key, w] of widgets) {
        if (w.state === 'pill' && active !== w.el && !w.host.contains(active)) removeWidget(key);
      }
      if (popoverHost && active !== currentField && !popoverHost.contains(active)) removePopover();
    }, 150);
  }, true);
  // Keep every widget pinned to its field as the page scrolls.
  window.addEventListener('scroll', () => { for (const w of widgets.values()) positionWidget(w); }, true);
})();
