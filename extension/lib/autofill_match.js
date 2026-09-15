// extension/lib/autofill_match.js — pure, no DOM. Loaded before content_autofill_fill.js.
(function () {
  function normalizeLabel(s) {
    return (s || '')
      .toLowerCase()
      .replace(/['']/g, "'")
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function matchFieldKey(signature, fieldPatterns) {
    const sig = normalizeLabel(signature);
    if (!sig) return null;
    let best = null, bestLen = 0;
    for (const key in fieldPatterns) {
      for (const syn of fieldPatterns[key]) {
        const n = normalizeLabel(syn);
        if (!n) continue;
        if ((' ' + sig + ' ').includes(' ' + n + ' ') && n.length > bestLen) {
          best = key; bestLen = n.length;
        }
      }
    }
    return best;
  }

  function matchOption(canonicalValue, optionTexts, synonymsForKey) {
    if (!canonicalValue || !synonymsForKey) return null;
    const syns = (synonymsForKey[canonicalValue] || []).map(normalizeLabel).sort((a, b) => b.length - a.length);
    let best = null, bestLen = 0;
    optionTexts.forEach((txt, i) => {
      const n = normalizeLabel(txt);
      for (const syn of syns) {
        if (syn && n.includes(syn) && syn.length > bestLen) { best = { index: i, text: txt }; bestLen = syn.length; }
      }
    });
    return best;
  }

  function boolToOption(boolValue, optionTexts, boolSynonyms) {
    const key = boolValue ? 'true' : 'false';
    return matchOption(key, optionTexts, boolSynonyms);
  }

  window.__autofillMatch = { normalizeLabel, matchFieldKey, matchOption, boolToOption };
})();
