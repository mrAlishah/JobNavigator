// extension/lib/ats_combobox.js — custom (non-native) combobox driver.
//
// Ported near-verbatim from freehire (https://github.com/strelov1/freehire),
// files extension/lib/combobox.ts + extension/lib/form.ts, which are MIT-licensed:
//
//   MIT License. Copyright (c) strelov1 / freehire contributors.
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction... The above copyright notice and this
//   permission notice shall be included in all copies or substantial portions of
//   the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
//
// Adapted to vanilla JS for an MV3 content script: TypeScript types removed,
// cross-file imports inlined, and option-picking delegated to a caller-supplied
// matcher (window.__autofillMatch) so our synonym dictionaries choose the option
// instead of freehire's exact-text equality.
(function () {
  'use strict';

  // The ways a widget declares itself a combobox (freehire form.ts COMBO_WIDGET).
  const COMBO_WIDGET = '[role="combobox"], [aria-autocomplete], [aria-haspopup="listbox"]';
  // react-select's rendered value / placeholder, by emotion class name.
  const VALUE_NODE = '[class*="singleValue"], [class*="single-value"], [class*="multiValue"], [class*="multi-value"]';
  const PLACEHOLDER_NODE = '[class*="placeholder"]';

  const SETTLE_MS = 1000;
  const POLL_MS = 25;

  function text(el) {
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function _norm(s) {
    // Reuse the shared normalizer when present; fall back to a local copy.
    if (window.__autofillMatch && window.__autofillMatch.normalizeLabel) return window.__autofillMatch.normalizeLabel(s);
    return (s || '').toLowerCase().replace(/\*/g, '').replace(/\s+/g, ' ').trim();
  }

  // A native <select> is never a combobox; custom widgets match the ARIA set.
  function isComboWidget(el) {
    if (el instanceof window.HTMLSelectElement) return false;
    return !!(el.matches && el.matches(COMBO_WIDGET));
  }

  // The pointer sequence a real click produces, dispatched on the element itself
  // (freehire press()) — react-select and friends open on mousedown, and events
  // bubble to the ancestor handler.
  function press(el) {
    if (typeof PointerEvent === 'function') {
      el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, composed: true }));
    }
    for (const type of ['mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, composed: true, button: 0 }));
    }
  }

  // Wait for a condition the widget's framework may only satisfy on a later tick.
  function settle(holds) {
    return new Promise(resolve => {
      let waited = 0;
      const tick = () => {
        if (holds()) return resolve(true);
        waited += POLL_MS;
        if (waited >= SETTLE_MS) return resolve(holds());
        setTimeout(tick, POLL_MS);
      };
      tick();
    });
  }

  function isOnScreen(el) {
    if (el.closest('[hidden]')) return false;
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility()) return false;
    const view = el.ownerDocument.defaultView;
    if (!view) return true;
    for (let node = el; node; node = node.parentElement) {
      const style = view.getComputedStyle(node);
      if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      if (style.opacity === '0') return false;
    }
    return true;
  }

  function expandedState(widget) {
    if (widget.hasAttribute('aria-expanded')) return widget.getAttribute('aria-expanded');
    const owner = widget.closest(
      '[role="combobox"][aria-expanded], [aria-haspopup="listbox"][aria-expanded], [aria-autocomplete][aria-expanded]'
    );
    return owner ? owner.getAttribute('aria-expanded') : null;
  }

  // The listbox a widget declares as its own (resolved by id off aria-controls/
  // aria-owns), so a listbox portalled to document.body is still found.
  function comboListbox(el) {
    const ids = (el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '').split(/\s+/).filter(Boolean);
    const named = ids.map(id => el.ownerDocument.getElementById(id)).filter(Boolean);
    const listbox = named.find(n => n.getAttribute('role') === 'listbox' || n.querySelector('[role="option"]'));
    return listbox || named[0] || null;
  }

  function comboOptionNodes(el) {
    const listbox = comboListbox(el);
    if (!listbox) return [];
    // [role=option] covers react-select/ARIA; Workday renders options as
    // div[data-automation-id="promptOption"]; Oracle JET (oj-select-single, used
    // by Oracle HCM/ORC) renders .oj-listbox-result — all body-portaled.
    return Array.from(listbox.querySelectorAll(
      '[role="option"], [data-automation-id="promptOption"], .oj-listbox-result, .oj-listbox-option'
    )).filter(isOnScreen);
  }

  // React keeps a widget's real handlers on a __reactProps* key. Workday commits a
  // dropdown selection through onClick, not a bare DOM click — so we fire it as a
  // fallback when press() alone didn't take.
  function _reactProps(el) {
    for (const k in el) { if (k.indexOf('__reactProps') === 0) return el[k]; }
    return null;
  }

  function isOpen(widget) {
    const expanded = expandedState(widget);
    if (expanded !== null) return expanded === 'true';
    const listbox = comboListbox(widget);
    return listbox !== null && isOnScreen(listbox);
  }

  // The values the widget displays (freehire displayedValues) — stops the walk at
  // a second combobox so a neighbour's value can never be reported as ours.
  function displayedValues(widget) {
    for (let el = widget.parentElement; el; el = el.parentElement) {
      if (el.querySelectorAll(COMBO_WIDGET).length > 1) return [];
      const shown = Array.from(el.querySelectorAll(VALUE_NODE));
      if (shown.length) return shown.map(text).filter(Boolean);
      if (el.querySelector(PLACEHOLDER_NODE)) return [];
    }
    return [];
  }

  // Type text into a widget's search input so a searchable combobox filters its
  // options (city/country/location typeaheads render options only after input).
  function _typeInto(input, textVal) {
    const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if (desc && desc.set) desc.set.call(input, textVal); else input.value = textVal;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: (textVal.slice(-1) || 'a') }));
  }

  function _searchInput(widget) {
    if (widget.tagName === 'INPUT') return widget;
    return (widget.querySelector && widget.querySelector('input')) || null;
  }

  // Fallback option finder for typeaheads whose listbox isn't linked by
  // aria-controls (e.g. Rippling's Location). Safe because the caller's picker
  // only selects an option whose text matches the typed answer.
  function _looseOptions() {
    return Array.from(document.querySelectorAll(
      '[role="option"], [data-automation-id="promptOption"], .oj-listbox-result, .oj-listbox-option'
    )).filter(isOnScreen);
  }

  // High-level: open the widget, (optionally type `searchText` to filter a
  // searchable list), read its options, let `pick(optionTexts)` choose an index,
  // click it, and confirm it committed.
  // Returns 'filled' | 'no-option' | 'did-not-open' | 'did-not-commit'.
  async function fillCombobox(widget, pick, searchText) {
    const input = _searchInput(widget);
    if (input && input.focus) { try { input.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }
    if (!isOpen(widget)) { press(widget); await settle(() => isOpen(widget)); }
    if (searchText && input) {
      _typeInto(input, searchText);
      await settle(() => comboOptionNodes(widget).length > 0 || _looseOptions().length > 0);
    }
    let nodes = comboOptionNodes(widget);
    if (!nodes.length) nodes = _looseOptions();
    if (!nodes.length) return isOpen(widget) ? 'no-option' : 'did-not-open';
    const texts = nodes.map(text);
    const idx = pick(texts);
    if (idx == null || idx < 0 || !nodes[idx]) return 'no-option';
    press(nodes[idx]);
    // Workday-style widgets commit through React's onClick; fire it if the press
    // didn't close the listbox on its own. Harmless (idempotent) for react-select.
    await settle(() => !isOpen(widget) || displayedValues(widget).length > 0);
    if (isOpen(widget)) {
      try { const rp = _reactProps(nodes[idx]); if (rp && rp.onClick) rp.onClick({ preventDefault() {}, stopPropagation() {} }); } catch (e) { /* ignore */ }
      await settle(() => !isOpen(widget) || displayedValues(widget).length > 0);
    }
    if (isOpen(widget) && displayedValues(widget).length === 0) return 'did-not-commit';
    return 'filled';
  }

  window.__autofillCombobox = {
    COMBO_WIDGET, isComboWidget, press, settle, isOpen, comboListbox,
    comboOptionNodes, displayedValues, fillCombobox, isOnScreen,
  };
})();
