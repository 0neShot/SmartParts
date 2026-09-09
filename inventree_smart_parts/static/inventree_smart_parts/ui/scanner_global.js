/**
 * SmartParts Global Barcode Scanner Integration
 * ============================================
 * Captures rapid keystroke bursts from USB/Bluetooth hardware barcode scanners
 * across the InvenTree web UI (PUI / classic), sanitizes ANSI MH10.8.2 / ISO 15434
 * control characters, looks up part existence, and either navigates directly
 * to the part detail page or prompts to import unknown parts via SmartParts.
 *
 * Coexists cleanly with the dedicated PureScan terminal (/plugin/smartparts/purescan/).
 */

(function () {
  'use strict';

  // ── Coexistence Guard: Never intercept on dedicated PureScan route ────────
  if (typeof window !== 'undefined' && window.location.pathname.includes('/purescan')) {
    return;
  }

  // ── Idempotency Guard: Ensure single listener registration ────────────────
  if (typeof window !== 'undefined' && window._smartparts_global_scanner_initialized) {
    if (typeof module !== 'undefined' && module.exports && window.SmartPartsGlobalScanner) {
      module.exports = window.SmartPartsGlobalScanner;
    }
    return;
  }
  if (typeof window !== 'undefined') {
    window._smartparts_global_scanner_initialized = true;
  }

  // ── Context & Route Awareness Helper ──────────────────────────────────────
  /**
   * Determine if the user is currently on a SmartParts-managed page or panel.
   * Checks global flags, URL path/query/hash signatures, and DOM elements.
   *
   * @returns {boolean}
   */
  function isSmartPartsActive() {
    if (typeof window === 'undefined') return false;

    // 1. Explicit window state flags
    if (
      window.SMARTPARTS_ACTIVE_PANEL === true ||
      window._smartparts_active === true ||
      window.SmartPartsActive === true ||
      window._smartparts_local_scanner_active === true
    ) {
      return true;
    }

    // 2. URL route / query / hash checks
    try {
      const loc = window.location;
      const pathname = (loc.pathname || '').toLowerCase();
      const hash = (loc.hash || '').toLowerCase();
      const search = (loc.search || '').toLowerCase();

      if (
        pathname.includes('/smartparts') ||
        pathname.includes('/plugin/smartparts') ||
        pathname.includes('smartparts-panel') ||
        pathname.includes('/purescan') ||
        hash.includes('smartparts') ||
        search.includes('smartparts')
      ) {
        return true;
      }
    } catch (_) {
      // Ignore location access errors in sandbox/test environments
    }

    // 3. DOM container and panel attribute checks
    if (typeof document !== 'undefined') {
      try {
        if (
          document.querySelector('#sp-root') ||
          document.querySelector('#smartparts-root') ||
          document.querySelector('[data-smartparts-panel]') ||
          document.querySelector('.smartparts-panel') ||
          document.querySelector('#searchForm') ||
          document.querySelector('#mpnInput') ||
          document.querySelector('#purescan-app') ||
          document.querySelector('.purescan-container') ||
          document.querySelector('#purescan-input')
        ) {
          return true;
        }

        const ae = document.activeElement;
        if (ae && ae.closest && ae.closest('#sp-root, [data-smartparts-panel], #searchForm, #smartparts-root')) {
          return true;
        }
      } catch (_) {
        // Ignore querySelector errors
      }
    }

    return false;
  }

  /**
   * Forward a scanned MPN directly to an active SmartParts panel without modal prompt.
   */
  function _forwardScanToActivePanel(mpn) {
    if (!mpn || typeof document === 'undefined') return;

    if (typeof window !== 'undefined' && typeof window._smartparts_panel_search === 'function') {
      try {
        window._smartparts_panel_search(mpn);
        showToast(`⚡ Searching SmartParts panel for ${mpn}...`, 'info', 2000);
        return;
      } catch (e) {
        console.warn('SmartParts: _smartparts_panel_search invocation failed:', e);
      }
    }

    const panelInput = document.getElementById('sp-mpn');
    const panelBtn = document.getElementById('sp-search-btn');
    if (panelInput && panelBtn) {
      panelInput.value = mpn;
      showToast(`⚡ Searching SmartParts panel for ${mpn}...`, 'info', 2000);
      panelBtn.click();
    }
  }

  // ── Constants & Parsing Pipeline (from scanner.js) ────────────────────────
  const GS  = '\x1d';
  const RS  = '\x1e';
  const EOT = '\x04';

  const GS_SUBSTITUTES = [
    /\{GS\}/gi,
    /\[GS\]/gi,
    /\u241d/g,
    /\|(?=[1-9A-Z])/g,
    /~(?=[1-9A-Z])/g,
  ];

  const RS_SUBSTITUTES = [
    /\{RS\}/gi,
    /\[RS\]/gi,
    /\u241e/g,
  ];

  const ANSI_HEADER_RE = /(?:\x05\x06)?\[\)?>(?:\x1e)?(?:05|06)(?:\x1d)?/;
  const HAS_GS_RE = /\x1d|\{GS\}|\[GS\]|\u241d/;

  function _extractFields(fields, result) {
    for (const f of fields) {
      const t = f.trim();
      if (!t) continue;
      if (t === '[)>' || t === '06' || t === '05' || t.startsWith('[)>')) continue;

      if (t.length > 2 && t.startsWith('1P')) {
        result.mpn = result.mpn || t.slice(2).trim();
        continue;
      }
      if (t.length > 3 && t.startsWith('30P')) {
        result.supplierSku = result.supplierSku || t.slice(3).trim();
        continue;
      }
      if (t.length > 1 && t.startsWith('P') && !t.startsWith('PO')) {
        if (!result.mpn) {
          result.mpn = t.slice(1).trim();
        } else {
          result.supplierSku = result.supplierSku || t.slice(1).trim();
        }
        continue;
      }
      if (t.length > 1 && t.startsWith('Q') && /^[0-9]/.test(t.slice(1))) {
        const n = parseInt(t.slice(1), 10);
        if (!isNaN(n) && result.quantity === null) result.quantity = n;
        continue;
      }
      if (t.length > 2 && t.startsWith('1T')) {
        result.batch = result.batch || t.slice(2).trim();
        continue;
      }
      if (t.length > 1 && (t.startsWith('K') || t.startsWith('1K'))) {
        const pfx = t.startsWith('1K') ? 2 : 1;
        result.poNumber = result.poNumber || t.slice(pfx).trim();
        continue;
      }
    }
  }

  function _tryAnsiParse(raw, result) {
    let s = raw;
    for (const pat of RS_SUBSTITUTES) s = s.replace(pat, RS);
    for (const pat of GS_SUBSTITUTES) s = s.replace(pat, GS);

    const isAnsi = ANSI_HEADER_RE.test(s) || (HAS_GS_RE.test(s) && (s.includes('1P') || s.includes('30P') || s.includes('P')));
    if (!isAnsi) return false;

    s = s.replace(ANSI_HEADER_RE, '').replace(new RegExp(`^${RS}+`), '').replace(new RegExp(`${EOT}.*$`), '');
    const records = s.split(RS);
    const allFields = [];
    for (const record of records) {
      const fields = record.split(GS);
      for (const f of fields) {
        if (f.trim()) allFields.push(f.trim());
      }
    }

    // If separators were stripped, let the strong-boundary heuristic parser handle it
    if (allFields.length < 2) return false;

    _extractFields(allFields, result);

    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found) result.source = 'ansi';
    return found;
  }

  const _STRONG_BOUNDARY_RE = /(?:1P|1T|1K|30P|6D|(?<=[^A-Z]|^)Q\d)/g;

  function _tryHeuristicParse(raw, result) {
    let s = raw;
    for (const pat of RS_SUBSTITUTES) s = s.replace(pat, RS);
    for (const pat of GS_SUBSTITUTES) s = s.replace(pat, GS);

    const match = s.match(/(?:\x05\x06)?\[\)?>(?:05|06|1e05|1e06)?/i);
    if (!match) return false;

    let body = s.slice(match.index + match[0].length).replace(/^[\x1e\x1d\s]+/, '').replace(/[\x04\x1e\r\n]+$/, '');
    if (!body) return false;

    const indices = [];
    let m;
    _STRONG_BOUNDARY_RE.lastIndex = 0;
    while ((m = _STRONG_BOUNDARY_RE.exec(body)) !== null) {
      indices.push(m.index);
    }
    if (indices.length === 0 || indices[0] !== 0) indices.unshift(0);

    const chunks = [];
    for (let i = 0; i < indices.length; i++) {
      const start = indices[i];
      const end = (i + 1 < indices.length) ? indices[i + 1] : body.length;
      chunks.push(body.slice(start, end));
    }

    const fields = [];
    for (const chunk of chunks) {
      const gsSub = chunk.split(/\x1d+/);
      for (const piece of gsSub) {
        const t = piece.trim();
        if (t) fields.push(t);
      }
    }

    _extractFields(fields, result);
    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found) result.source = 'heuristic';
    return found;
  }

  function _tryRegexParse(raw, result) {
    const mpnMatch = raw.match(/(?:1P|P[:\s])([A-Za-z0-9_\-\.\/]{3,})/);
    if (mpnMatch && !result.mpn) result.mpn = mpnMatch[1].trim();

    const skuMatch = raw.match(/(?:30P|SKU[:\s])([A-Za-z0-9_\-\.\/]{3,})/);
    if (skuMatch && !result.supplierSku) result.supplierSku = skuMatch[1].trim();

    const qtyMatch = raw.match(/Q[:\s]?(\d+)/);
    if (qtyMatch && result.quantity === null) result.quantity = parseInt(qtyMatch[1], 10);

    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found) result.source = 'regex';
    return found;
  }

  function _fallbackParse(raw, result) {
    const cleaned = raw.replace(/[\x00-\x1f\x7f]+/g, '').trim();
    if ((cleaned.startsWith('{') && cleaned.endsWith('}')) || cleaned.startsWith('INV-')) {
      result.source = 'native_internal';
      return;
    }
    if (cleaned) {
      result.mpn = cleaned;
      result.source = 'fallback';
    }
  }

  function parseBarcode(raw) {
    if (window.SmartPartsScanner && typeof window.SmartPartsScanner.parse === 'function') {
      try {
        return window.SmartPartsScanner.parse(raw);
      } catch (e) {}
    }
    const res = {
      mpn: '',
      quantity: null,
      batch: '',
      supplierSku: '',
      poNumber: '',
      raw: raw || '',
      source: '',
    };
    if (!raw || !raw.trim()) return res;
    if (_tryAnsiParse(raw, res)) return res;
    if (_tryHeuristicParse(raw, res)) return res;
    if (_tryRegexParse(raw, res)) return res;
    _fallbackParse(raw, res);
    return res;
  }

  // ── Input Protection & Keystroke Wedge Listener ───────────────────────────
  const MAX_INTER_KEY_MS = 50;
  const MIN_SCAN_LENGTH  = 4;
  const IDLE_RESET_MS    = 600;

  let _buffer = '';
  let _times = [];
  let _lastTime = 0;
  let _initialInputValue = null;
  let _activeInputElem = null;

  function getCsrfToken() {
    const name = 'csrftoken';
    for (const cookie of (document.cookie || '').split(';')) {
      const [k, v] = cookie.trim().split('=');
      if (k === name) return decodeURIComponent(v || '');
    }
    return '';
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ── MPN Validation Guard ──────────────────────────────────────────────────
  function isValidMpn(str) {
    if (!str || typeof str !== 'string') return false;
    const t = str.trim();
    if (t.length < 2 || t.length > 80) return false;
    if (t.includes('{') || t.includes('}') || t.includes('[') || t.includes(']')) return false;
    if (t.startsWith('INV-') || t.startsWith('IN:')) return false;
    if (!/[a-zA-Z0-9]/.test(t)) return false;
    return true;
  }

  // ── Native InvenTree Model Route Registry ─────────────────────────────────
  function normalizeModelKey(key) {
    if (!key || typeof key !== 'string') return '';
    return key.toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  const NATIVE_MODEL_ROUTES = {
    // Orders & Manufacturing
    purchaseorder: { route: '/web/purchasing/purchase-order/', label: 'Purchase Order', model: 'purchaseorder' },
    build: { route: '/web/manufacturing/build-order/', label: 'Build Order', model: 'build' },
    buildorder: { route: '/web/manufacturing/build-order/', label: 'Build Order', model: 'build' },
    salesorder: { route: '/web/sales/sales-order/', label: 'Sales Order', model: 'salesorder' },
    returnorder: { route: '/web/sales/return-order/', label: 'Return Order', model: 'returnorder' },
    salesordershipment: { route: '/web/sales/sales-order/', label: 'Sales Order Shipment', model: 'salesordershipment' },
    shipment: { route: '/web/sales/sales-order/', label: 'Sales Order Shipment', model: 'salesordershipment' },

    // Parts & Companies
    supplierpart: { route: '/web/purchasing/supplier-part/', label: 'Supplier Part', model: 'supplierpart' },
    manufacturerpart: { route: '/web/part/manufacturer-part/', label: 'Manufacturer Part', model: 'manufacturerpart' },
    part: { route: '/web/part/', label: 'Part', model: 'part' },

    // Stock
    stocklocation: { route: '/web/stock/location/', label: 'Stock Location', model: 'stocklocation' },
    location: { route: '/web/stock/location/', label: 'Stock Location', model: 'stocklocation' },
    stockitem: { route: '/web/stock/item/', label: 'Stock Item', model: 'stockitem' },
    item: { route: '/web/stock/item/', label: 'Stock Item', model: 'stockitem' },
  };

  // ── Barcode Classification ────────────────────────────────────────────────
  function classifyBarcode(raw) {
    if (!raw) return { type: 'unknown' };
    const trimmed = raw.trim();

    // 1. Detect JSON format: {"purchaseorder": 12}, {"build": 7}, {"stocklocation": 5}, etc.
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const obj = JSON.parse(trimmed);
        if (obj && typeof obj === 'object') {
          for (const rawKey of Object.keys(obj)) {
            const normKey = normalizeModelKey(rawKey);
            const routeDef = NATIVE_MODEL_ROUTES[normKey];
            if (routeDef) {
              const val = obj[rawKey];
              let id = null;
              if (typeof val === 'number' || typeof val === 'string') {
                id = parseInt(val, 10);
              } else if (val && typeof val === 'object') {
                id = parseInt(val.pk || val.id, 10);
              }
              if (!isNaN(id) && id > 0) {
                return {
                  type: 'native_' + routeDef.model,
                  model: routeDef.model,
                  label: routeDef.label,
                  id: id,
                  url: `${routeDef.route}${id}/`,
                  raw: trimmed,
                };
              }
              return { type: 'native_invalid_json', raw: trimmed, model: routeDef.model };
            }
          }
          return { type: 'native_json_unknown', raw: trimmed };
        }
      } catch (e) {
        return { type: 'malformed_json', raw: trimmed };
      }
    }

    // 2. Detect Distributor 2D DataMatrix (e.g. [)>06..., [)>05...)
    if (ANSI_HEADER_RE.test(trimmed) || trimmed.startsWith('[)>') || trimmed.includes('[)>')) {
      return { type: 'distributor_datamatrix', raw: trimmed };
    }

    // 3. InvenTree Short Codes (INV-...)
    if (trimmed.startsWith('INV-') || /^[A-Za-z0-9]{2,6}-([0-9A-Za-z$%*+.\/:]{2})(\d+)$/.test(trimmed)) {
      return { type: 'possible_short_code', raw: trimmed };
    }

    return { type: 'clean_string', raw: trimmed };
  }

  // ── Toast Notification ───────────────────────────────────────────────────
  function showToast(message, type = 'info', duration = 3000) {
    let container = document.getElementById('sp-scanner-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'sp-scanner-toast-container';
      container.style.cssText = `
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 100000;
        display: flex;
        flex-direction: column;
        gap: 8px;
        pointer-events: none;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      `;
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColor = type === 'success' ? '#10b981' : type === 'warning' ? '#f59e0b' : '#3b82f6';
    toast.style.cssText = `
      background: ${bgColor};
      color: #ffffff;
      padding: 12px 18px;
      border-radius: 8px;
      font-size: 0.9rem;
      font-weight: 500;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.2s ease, transform 0.2s ease;
      pointer-events: auto;
      display: flex;
      align-items: center;
      gap: 10px;
    `;
    toast.innerHTML = `<span>⚡</span><div>${escapeHtml(message)}</div>`;
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateY(0)';
    });

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 200);
    }, duration);
  }

  // ── Confirmation Modal for Unknown Parts ─────────────────────────────────
  function showImportConfirmModal(mpn) {
    // Context check: If already on SmartParts dashboard or active panel, NEVER show modal!
    if (isSmartPartsActive()) {
      console.log('SmartParts: Suppressed global import modal (SmartParts is already active on this page).');
      return;
    }

    // Safety guard: Raw JSON strings and internal payloads must NEVER trigger modal
    if (!isValidMpn(mpn)) {
      console.warn('SmartParts: Aborted import confirmation modal for invalid MPN payload:', mpn);
      return;
    }

    // Remove existing modal if any
    const existing = document.getElementById('sp-scanner-modal-overlay');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    const overlay = document.createElement('div');
    overlay.id = 'sp-scanner-modal-overlay';
    overlay.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(15, 23, 42, 0.65);
      backdrop-filter: blur(4px);
      z-index: 999999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      box-sizing: border-box;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    `;

    const card = document.createElement('div');
    card.id = 'sp-scanner-modal';
    card.style.cssText = `
      background: #ffffff;
      color: #1e293b;
      max-width: 480px;
      width: 100%;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.2), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
      border: 1px solid #e2e8f0;
      animation: spModalFadeIn 0.15s ease-out;
    `;

    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
        <div style="background:#eef2ff;color:#6366f1;width:44px;height:44px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.4rem;">
          ⚡
        </div>
        <div>
          <h3 style="margin:0;font-size:1.15rem;font-weight:700;color:#0f172a;">SmartParts &middot; Part Lookup</h3>
          <span style="font-size:0.8rem;color:#64748b;">Hardware Scanner Integration</span>
        </div>
      </div>
      <p style="margin:0 0 24px 0;font-size:0.95rem;line-height:1.55;color:#334155;">
        Part <strong>${escapeHtml(mpn)}</strong> was not found in InvenTree. Do you want to open SmartParts to import it?
      </p>
      <div style="display:flex;justify-content:flex-end;gap:10px;">
        <button id="sp-modal-cancel-btn" style="
          padding: 10px 18px;
          border-radius: 8px;
          border: 1px solid #cbd5e1;
          background: #f8fafc;
          color: #475569;
          font-weight: 600;
          font-size: 0.9rem;
          cursor: pointer;
          transition: background 0.15s ease;
        ">Cancel</button>
        <button id="sp-modal-confirm-btn" style="
          padding: 10px 20px;
          border-radius: 8px;
          border: none;
          background: #6366f1;
          color: #ffffff;
          font-weight: 600;
          font-size: 0.9rem;
          cursor: pointer;
          box-shadow: 0 2px 4px rgba(99, 102, 241, 0.3);
          transition: background 0.15s ease;
        ">Open SmartParts</button>
      </div>
    `;

    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const cancelBtn = card.querySelector('#sp-modal-cancel-btn');
    const confirmBtn = card.querySelector('#sp-modal-confirm-btn');

    function closeModal() {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.removeEventListener('keydown', handleKeyModal);
    }

    function doConfirm() {
      closeModal();
      window.location.href = `/plugin/smartparts/?mpn=${encodeURIComponent(mpn)}&auto_search=true`;
    }

    function handleKeyModal(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeModal();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        doConfirm();
      }
    }

    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
    if (confirmBtn) confirmBtn.addEventListener('click', doConfirm);
    if (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
      });
    }

    document.addEventListener('keydown', handleKeyModal);
    // Autofocus confirm button for instant Enter confirmation
    setTimeout(() => {
      if (confirmBtn) confirmBtn.focus();
    }, 50);
  }

  // ── Native Route Navigator & Resolver ─────────────────────────────────────
  function navigateToRoute(targetUrl, label, id) {
    if (!targetUrl) return;
    const msg = label && id
      ? `Navigating to ${label} #${id}...`
      : `Navigating to ${label || 'object'}...`;
    console.log(`SmartParts Scanner: ${msg} -> ${targetUrl}`);
    showToast(`⚡ ${msg}`, 'success', 2500);

    // Completely reset scan buffers to prevent duplicate triggers
    _buffer = '';
    _times = [];
    _initialInputValue = null;
    _activeInputElem = null;

    setTimeout(() => {
      if (typeof window.location.assign === 'function') {
        window.location.assign(targetUrl);
      } else {
        window.location.href = targetUrl;
      }
    }, 250);
  }

  function resolveNativeUrlFromApiResponse(data) {
    if (!data || typeof data !== 'object') return null;

    // 1. Direct top-level url / web_url
    if (data.web_url || data.url) {
      const directUrl = data.web_url || data.url;
      let label = 'InvenTree Object';
      let id = data.pk || data.id || null;
      if (data.model) {
        const normModel = normalizeModelKey(data.model);
        if (NATIVE_MODEL_ROUTES[normModel]) {
          label = NATIVE_MODEL_ROUTES[normModel].label;
        }
      }
      return { url: directUrl, label: label, id: id };
    }

    // 2. Explicit model + pk pattern: { "model": "purchaseorder", "pk": 12 }
    if (data.model && (data.pk !== undefined || data.id !== undefined)) {
      const norm = normalizeModelKey(data.model);
      const routeDef = NATIVE_MODEL_ROUTES[norm];
      const pk = data.pk !== undefined ? data.pk : data.id;
      if (routeDef && pk) {
        return {
          url: `${routeDef.route}${pk}/`,
          label: routeDef.label,
          id: pk,
        };
      }
    }

    // 3. Nested model key pattern: { "purchaseorder": { "pk": 12, ... } }
    // or { "build": { "pk": 7, "web_url": "/web/manufacturing/build-order/7/" } }
    for (const rawKey of Object.keys(data)) {
      const norm = normalizeModelKey(rawKey);
      const routeDef = NATIVE_MODEL_ROUTES[norm];
      if (routeDef && data[rawKey] && typeof data[rawKey] === 'object') {
        const obj = data[rawKey];
        const pk = obj.pk !== undefined ? obj.pk : obj.id;
        const url = obj.web_url || obj.url || (pk ? `${routeDef.route}${pk}/` : null);
        if (url) {
          const label = (obj.instance && (obj.instance.name || obj.instance.reference)) ||
                        obj.reference ||
                        obj.name ||
                        routeDef.label;
          return {
            url: url,
            label: label,
            id: pk || null,
          };
        }
      }
    }

    // 4. Fallback check for any object property containing web_url or url
    for (const key of Object.keys(data)) {
      const val = data[key];
      if (val && typeof val === 'object' && (val.web_url || val.url)) {
        return {
          url: val.web_url || val.url,
          label: key,
          id: val.pk || val.id || null,
        };
      }
    }

    return null;
  }

  // ── Barcode Dispatch & Lookup ─────────────────────────────────────────────
  function handleBarcodeScan(captured) {
    if (window.location.pathname.includes('/purescan')) {
      return;
    }

    const classification = classifyBarcode(captured);

    // ── 1. Native InvenTree JSON Barcodes ──────────────────────────────────
    if (classification.url) {
      navigateToRoute(classification.url, classification.label, classification.id);
      return;
    }

    if (classification.type === 'native_json_unknown' || classification.type === 'native_invalid_json' || classification.type === 'malformed_json') {
      showToast('Internal InvenTree barcode payload (no matching object).', 'warning', 3000);
      console.warn('SmartParts: Ignored internal JSON barcode:', captured);
      return;
    }

    // ── 2. Native Short Codes or Clean Strings: Check InvenTree Core First ─
    if (classification.type === 'possible_short_code' || classification.type === 'clean_string') {
      showToast(`Scanning: ${captured.slice(0, 24)}...`, 'info', 1800);

      fetch('/api/barcode/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify({ barcode: captured }),
      })
        .then(res => (res.ok ? res.json() : null))
        .then(data => {
          if (data) {
            const resolved = resolveNativeUrlFromApiResponse(data);
            if (resolved && resolved.url) {
              navigateToRoute(resolved.url, resolved.label, resolved.id);
              return;
            }
          }

          // InvenTree core did not match:
          // If this was an InvenTree short code (INV-...), NEVER treat as MPN!
          if (captured.startsWith('INV-')) {
            showToast(`InvenTree barcode ${captured} not found in database.`, 'warning', 3000);
            return;
          }

          // Otherwise, forward to SmartParts barcode lookup
          querySmartPartsLookup(captured);
        })
        .catch(err => {
          console.warn('SmartParts: /api/barcode/ check failed:', err);
          if (captured.startsWith('INV-')) {
            showToast(`InvenTree barcode ${captured} lookup error.`, 'warning', 3000);
            return;
          }
          querySmartPartsLookup(captured);
        });
      return;
    }

    // ── 3. Distributor 2D DataMatrix (e.g. [)>06...) ────────────────────────
    querySmartPartsLookup(captured);
  }

  function querySmartPartsLookup(captured) {
    const parsed = parseBarcode(captured);
    const lookupMpn = parsed.mpn || '';
    const lookupSku = parsed.supplierSku || '';

    showToast(`SmartParts: Looking up ${lookupMpn || 'barcode'}...`, 'info', 2000);

    fetch('/plugin/smartparts/api/barcode/lookup/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify({
        barcode: captured,
        mpn: lookupMpn,
        sku: lookupSku,
      }),
    })
      .then(res => res.json())
      .then(data => {
        if (data.found && data.part_url) {
          navigateToRoute(data.part_url, data.part_name || data.mpn || lookupMpn, data.part_id || null);
        } else if (data.is_native) {
          showToast(data.error || 'InvenTree object not found in database.', 'warning', 3000);
        } else {
          const rawMpn = data.mpn || lookupMpn || '';
          if (isValidMpn(rawMpn)) {
            if (isSmartPartsActive()) {
              console.log('SmartParts: User is already on SmartParts page/panel. Suppressing modal.');
              _forwardScanToActivePanel(rawMpn);
              return;
            }
            showImportConfirmModal(rawMpn);
          } else {
            console.warn('SmartParts: Suppressed part import prompt for non-MPN payload:', rawMpn);
          }
        }
      })
      .catch(err => {
        console.warn('SmartParts: barcode lookup error:', err);
        if (isValidMpn(lookupMpn)) {
          if (isSmartPartsActive()) {
            console.log('SmartParts: Suppressed global import modal on error (SmartParts is active on this page).');
            _forwardScanToActivePanel(lookupMpn);
            return;
          }
          showImportConfirmModal(lookupMpn);
        }
      });
  }


  // ── Global Keydown Listener ───────────────────────────────────────────────
  function onKeydown(e) {
    // PureScan exclusion check
    if (typeof window !== 'undefined' && window.location.pathname && window.location.pathname.includes('/purescan')) {
      return;
    }

    // De-duplication: If another listener already handled this event
    if (e && e._handledBySmartParts) {
      return;
    }

    // Yield if a local dedicated scanner is active on this page (e.g. dashboard with scanner.js)
    if (typeof window !== 'undefined' && window._smartparts_local_scanner_active === true) {
      return;
    }

    const now = Date.now();
    const delta = now - _lastTime;
    _lastTime = now;

    if (e.key === 'Enter') {
      const captured = _buffer;
      const capturedTimes = _times.slice();
      _buffer = '';
      _times = [];

      if (captured.length < MIN_SCAN_LENGTH) {
        _initialInputValue = null;
        _activeInputElem = null;
        return;
      }

      let fastGaps = 0;
      for (let i = 1; i < capturedTimes.length; i++) {
        if (capturedTimes[i] - capturedTimes[i - 1] < MAX_INTER_KEY_MS) {
          fastGaps++;
        }
      }
      const totalGaps = Math.max(capturedTimes.length - 1, 1);
      const fastRatio = fastGaps / totalGaps;

      if (fastRatio >= 0.8) {
        // Barcode scan burst confirmed
        e.preventDefault();
        e.stopPropagation();

        // Restore corrupted active text input
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
          if (_activeInputElem === ae && _initialInputValue !== null) {
            ae.value = _initialInputValue;
          } else {
            const val = ae.value || '';
            const firstChunk = captured.slice(0, Math.min(8, captured.length));
            if (val.includes(firstChunk) || val.endsWith(captured.slice(-4))) {
              ae.value = '';
            }
          }
        }

        _initialInputValue = null;
        _activeInputElem = null;

        handleBarcodeScan(captured);
      } else {
        _initialInputValue = null;
        _activeInputElem = null;
      }
      return;
    }

    if (e.key.length > 1) return;

    if (delta > IDLE_RESET_MS && _buffer.length > 0) {
      _buffer = '';
      _times = [];
      _initialInputValue = null;
      _activeInputElem = null;
    }

    // Record initial value of focused input at the start of a burst
    if (_buffer.length === 0) {
      const ae = document.activeElement;
      if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
        _activeInputElem = ae;
        _initialInputValue = ae.value || '';
      } else {
        _activeInputElem = null;
        _initialInputValue = null;
      }
    }

    _buffer += e.key;
    _times.push(now);
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('keydown', onKeydown, { capture: true });
  }

  // ── Public Export for InvenTree PUI Dynamic Feature Loader ────────────────
  const globalScannerApi = {
    init: function () {
      // Already running via capture listener
    },
    parseBarcode: parseBarcode,
    classifyBarcode: classifyBarcode,
    resolveNativeUrlFromApiResponse: resolveNativeUrlFromApiResponse,
    normalizeModelKey: normalizeModelKey,
    NATIVE_MODEL_ROUTES: NATIVE_MODEL_ROUTES,
    navigateToRoute: navigateToRoute,
    isValidMpn: isValidMpn,
    isSmartPartsActive: isSmartPartsActive,
    triggerScan: handleBarcodeScan,
  };

  if (typeof window !== 'undefined') {
    window.SmartPartsGlobalScanner = globalScannerApi;
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.__sp_api = globalScannerApi;
  }

  // Export entry point for UI mixin features and test environments
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = globalScannerApi;
  }
})();

export function initGlobalScanner(target, context) {
  // Invoked by InvenTree PUI navigation/feature loader
  return true;
}

const _api = (typeof globalThis !== 'undefined' && globalThis.__sp_api) || {};
export const parseBarcode = _api.parseBarcode;
export const classifyBarcode = _api.classifyBarcode;
export const resolveNativeUrlFromApiResponse = _api.resolveNativeUrlFromApiResponse;
export const normalizeModelKey = _api.normalizeModelKey;
export const NATIVE_MODEL_ROUTES = _api.NATIVE_MODEL_ROUTES;
export const isValidMpn = _api.isValidMpn;
export const navigateToRoute = _api.navigateToRoute;
export const handleBarcodeScan = _api.handleBarcodeScan;
export const triggerScan = _api.triggerScan;
export const isSmartPartsActive = _api.isSmartPartsActive;
