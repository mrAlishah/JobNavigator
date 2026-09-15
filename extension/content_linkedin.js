// LinkedIn Collection Page Capture
// Runs on: linkedin.com/jobs/collections/*
// Passively captures job IDs from cards as the user scrolls.
// The collections list is virtualized: LinkedIn keeps only a handful of cards in
// the DOM at once and swaps them as you scroll, so a one-shot scan gets ~7. We
// accumulate into a Set from every id source (the <li data-occludable-job-id>,
// data-job-id, and /jobs/view/{id} links), re-scanning on DOM mutations AND on
// scroll (capture phase, to catch the inner scroll container).

(() => {
  const capturedIds = new Set();
  let observer = null;
  let enabled = false;
  let onScroll = null;

  function throttle(fn, ms) {
    let last = 0, timer = null;
    return () => {
      const now = Date.now();
      const run = () => { last = Date.now(); timer = null; fn(); };
      if (now - last >= ms) run();
      else if (!timer) timer = setTimeout(run, ms - (now - last));
    };
  }

  function scan() {
    let added = 0;
    const add = (id) => {
      if (id && /^\d+$/.test(id) && !capturedIds.has(id)) { capturedIds.add(id); added++; }
    };
    // 1) job-card links
    document.querySelectorAll('a[href*="/jobs/view/"]').forEach(link => {
      const m = link.href.match(/\/jobs\/view\/(\d+)/);
      if (m) add(m[1]);
    });
    // 2) the virtualized <li> carries the id even without a rendered link
    document.querySelectorAll('[data-occludable-job-id]').forEach(el => add(el.getAttribute('data-occludable-job-id')));
    // 3) other data-job-id carriers (guard: some hold non-numeric values like "search")
    document.querySelectorAll('[data-job-id]').forEach(el => add(el.getAttribute('data-job-id')));

    if (added > 0) {
      chrome.runtime.sendMessage({ type: 'linkedin_ids', ids: Array.from(capturedIds) });
    }
  }

  const scheduleScan = throttle(scan, 250);

  function startCapturing() {
    if (observer) return;
    scan();
    // Observe the whole body — the results container class varies across LinkedIn
    // layouts, and body/subtree reliably catches card swaps.
    observer = new MutationObserver(scheduleScan);
    observer.observe(document.body, { childList: true, subtree: true });
    // Virtualized lists render new cards only as you scroll; capture-phase listener
    // catches both the window and the inner jobs scroll container.
    onScroll = throttle(scan, 300);
    window.addEventListener('scroll', onScroll, true);
  }

  function stopCapturing() {
    if (observer) { observer.disconnect(); observer = null; }
    if (onScroll) { window.removeEventListener('scroll', onScroll, true); onScroll = null; }
  }

  function checkEnabled() {
    chrome.storage.sync.get('linkedinCapture', (data) => {
      const shouldBeEnabled = !!data.linkedinCapture;
      if (shouldBeEnabled && !enabled) { enabled = true; startCapturing(); }
      else if (!shouldBeEnabled && enabled) { enabled = false; stopCapturing(); }
    });
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'sync' && changes.linkedinCapture) checkEnabled();
  });

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === 'linkedin_clear_ids') {
      capturedIds.clear();
      sendResponse({ ok: true });
    }
  });

  checkEnabled();
})();
