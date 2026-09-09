/**
 * SmartParts Barcode Scanner  (v4 – Strong-Boundary DataMatrix Parser)
 * =====================================================================
 * Parses 2D distributor barcodes (Mouser, DigiKey, TE – ANSI MH10.8.2 /
 * ISO/IEC 15434) from a barcode scanner wedge (USB HID keyboard mode).
 *
 * Three-stage parsing pipeline
 * ----------------------------
 * Stage 1  – Strict ANSI/ISO DataMatrix:
 *   Detects the standard [)> header and GS/RS control-character
 *   separators (or scanner-specific substitutes like | ~ space).
 *   Extracts: 1P/P → MPN, Q → Qty, 1T → Batch, 30P → supplierSku, K → PO.
 *
 * Stage 2  – GS-Stripped DataMatrix (strong-boundary algorithm):
 *   Engaged when the string starts with [)>05 or [)>06 but contains NO
 *   GS/RS characters (keyboard wedge stripped them).
 *   Classifies DIs as STRONG (1P, 1T, 1K, 30P, 6D, Q+digit) or WEAK
 *   (BT, LT, DC, P, K, S, V).  Only strong DIs terminate field values,
 *   preventing false splits inside part numbers like "LMK107BBJ106MALT".
 *
 * Stage 3  – Graceful fallback:
 *   Treats the raw string as a bare MPN / single-field barcode.
 *   Fills result.mpn with the trimmed raw string so the scan is NEVER lost.
 *
 * Exported as window.SmartPartsScanner:
 *   .init(callback)   – start global scanner listener
 *   .destroy()        – remove listener
 *   .parse(rawStr)    – parse a raw barcode string
 *                       → { mpn, quantity, batch, supplierSku, poNumber,
 *                            raw, source }
 *                       source = 'ansi' | 'heuristic' | 'regex' | 'fallback'
 */
(function (global) {
  'use strict';

  // ── Constants ──────────────────────────────────────────────────────────────
  const GS  = '\x1d';   // ASCII 29 – Group Separator   (field delimiter)
  const RS  = '\x1e';   // ASCII 30 – Record Separator  (record delimiter)
  const EOT = '\x04';   // ASCII 4  – End Of Transmission

  // Alternative representations some scanner wedges emit instead of true GS
  const GS_SUBSTITUTES = [
    /\{GS\}/gi,           // {GS}
    /\[GS\]/gi,           // [GS]
    /\u241d/g,            // ␝ (visible control picture)
    /\|(?=[1-9A-Z])/g,   // pipe only when followed by a DI char
    /~(?=[1-9A-Z])/g,    // tilde only when followed by a DI char
  ];

  const RS_SUBSTITUTES = [
    /\{RS\}/gi,
    /\[RS\]/gi,
    /\u241e/g,
  ];

  // A DataMatrix barcode starts with [)> (sometimes with leading \x05\x06)
  // followed by a two-digit format code (05 or 06) then the record separator.
  const ANSI_HEADER_RE = /(?:\x05\x06)?\[\)?>(?:\x1e)?(?:05|06)(?:\x1d)?/;

  // Looser "looks like 2D" heuristic used when the header has been stripped
  // but field separators are still present.
  const HAS_GS_RE = /\x1d|\{GS\}|\[GS\]|\u241d/;


  // ────────────────────────────────────────────────────────────────────────────
  //  Core field extractor (for pre-split fields)
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * Given an array of already-split field strings (data identifiers + values),
   * populate a result object according to ANSI MH10.8.2 DI assignments.
   */
  function _extractFields(fields, result) {
    for (const f of fields) {
      const t = f.trim();
      if (!t) continue;

      // Skip format-header tokens
      if (t === '[)>' || t === '06' || t === '05' || t.startsWith('[)>')) continue;

      // 1P → Manufacturer Part Number  (check before plain 'P')
      if (t.length > 2 && t.startsWith('1P')) {
        result.mpn = result.mpn || t.slice(2).trim();
        continue;
      }
      // 30P → Distributor SKU (Mouser/DigiKey part number)
      if (t.length > 3 && t.startsWith('30P')) {
        result.supplierSku = result.supplierSku || t.slice(3).trim();
        continue;
      }
      // P → Generic supplier / customer part number
      if (t.length > 1 && t.startsWith('P') && !t.startsWith('PO')) {
        if (!result.mpn) {
          result.mpn = t.slice(1).trim();
        } else {
          result.supplierSku = result.supplierSku || t.slice(1).trim();
        }
        continue;
      }
      // Q → Quantity  (digit must immediately follow)
      if (t.length > 1 && t.startsWith('Q') && /^\d/.test(t[1])) {
        const q = parseInt(t.slice(1), 10);
        if (!isNaN(q) && q > 0 && result.quantity === null) result.quantity = q;
        continue;
      }
      // 1T → Lot / Batch Code
      if (t.length > 2 && t.startsWith('1T')) {
        result.batch = result.batch || t.slice(2).trim();
        continue;
      }
      // 4L → Country of Origin (informational, ignored)
      // K → Customer PO / Reference Number
      if (t.length > 1 && t.startsWith('K') && /\S/.test(t[1])) {
        result.poNumber = result.poNumber || t.slice(1).trim();
        continue;
      }
    }
  }


  // ────────────────────────────────────────────────────────────────────────────
  //  Stage 1 – Strict ANSI/ISO parser
  // ────────────────────────────────────────────────────────────────────────────

  function _tryAnsiParse(raw, result) {
    const hasHeader = raw.includes('[)>') || /^\x05\x06/.test(raw);
    const hasGs     = HAS_GS_RE.test(raw);
    if (!hasHeader && !hasGs) return false;

    // Normalise GS/RS substitutes → real control chars
    let norm = raw;
    for (const re of GS_SUBSTITUTES)  norm = norm.replace(re, GS);
    for (const re of RS_SUBSTITUTES)  norm = norm.replace(re, RS);
    norm = norm.replace(/\{EOT\}|\u2404/gi, EOT);

    // Split on any separator
    const fields = norm.split(/[\x1d\x1e\x04]/).map(f => f.trim()).filter(Boolean);

    // If we only got 1 field, separators were absent → don't claim success
    if (fields.length < 2) return false;

    _extractFields(fields, result);

    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found) result.source = 'ansi';
    return found;
  }


  // ────────────────────────────────────────────────────────────────────────────
  //  Stage 2 – GS-Stripped DataMatrix parser (strong-boundary algorithm)
  // ────────────────────────────────────────────────────────────────────────────
  //
  // When the keyboard wedge strips ASCII 29/30 separators, all DI fields are
  // concatenated into a single string.  Naively splitting on DI prefixes fails
  // because part numbers can contain letters that look like DI prefixes
  // (e.g. "STM32F103C8T6" contains S, "LMK107BBJ106MALT" contains LT).
  //
  // Solution: classify DIs as STRONG or WEAK.
  //   • Strong DIs (numeric-prefixed: 1P, 1T, 1K, 30P, 6D, Q+digit) are
  //     always treated as field boundaries when terminating a value.
  //   • Weak DIs (BT, LT, DC, PN, K, P, etc.) are only valid as field
  //     STARTERS when preceded by a non-alphanumeric char or at position 0.
  //     They are NEVER used as value terminators.

  /**
   * DI definition table: [prefix, resultKey, isStrong]
   * Ordered longest-prefix-first for greedy matching.
   */
  const DI_TABLE = [
    ['30P', 'supplierSku', true ],
    ['QTY', 'quantity',    true ],
    ['1K',  'poNumber',    true ],
    ['1P',  'mpn',         true ],
    ['1T',  'batch',       true ],
    ['1S',  null,          true ],
    ['2S',  null,          true ],
    ['3S',  null,          true ],
    ['4L',  null,          true ],
    ['6D',  null,          true ],   // Date code (strong)
    ['9D',  null,          false],   // Date code (weak – 9D at qty end causes false hits)
    ['PN',  'mpn',         false],
    ['PO',  'poNumber',    false],
    ['QT',  'quantity',    false],
    ['BT',  'batch',       false],
    ['BX',  null,          false],
    ['DC',  null,          false],
    ['LT',  'batch',       false],
    ['RV',  null,          false],
    ['K',   'poNumber',    false],
    ['Q',   'quantity',    false],
    ['P',   'mpn',         false],
    ['V',   null,          false],
    ['S',   null,          false],
  ];

  // Strong boundary lookahead: matches the START of any strong DI or end-of-string.
  // Used to determine where a field value ENDS.
  const _STRONG_BOUNDARY_RE = new RegExp(
    '(?='
    + '30P'
    + '|QTY'
    + '|1[PTKSE]'
    + '|2S|3S'
    + '|4L'
    + '|6D'
    + '|Q(?=\d)'
    + '|$'
    + ')'
  );

  /**
   * Iterative scanner with strong-boundary value extraction.
   * Walks through the body string, matching DI prefixes greedily,
   * then capturing values until the next STRONG DI boundary.
   */
  function _tryHeuristicParse(raw, result) {
    const headerMatch = raw.match(/^\[?\)?>?\s*\[\)>(?:05|06)/);
    if (!headerMatch) return false;

    const body = raw.slice(headerMatch[0].length);
    if (!body) return false;

    let pos = 0;
    let matchCount = 0;
    const fields = {};  // key → value (first wins)

    while (pos < body.length) {
      let matched = false;

      for (const [prefix, key, isStrong] of DI_TABLE) {
        if (body.length - pos < prefix.length) continue;
        if (body.substr(pos, prefix.length) !== prefix) continue;

        // ── Single-char DI validation ──
        if (prefix.length === 1) {
          if (prefix === 'Q') {
            // Q is only a DI when followed by a digit
            if (pos + 1 >= body.length || !/\d/.test(body[pos + 1])) continue;
          } else if (prefix === 'K' || prefix === 'P') {
            // Only valid at position 0 or after non-alphanumeric
            if (pos > 0 && /[A-Za-z0-9]/.test(body[pos - 1])) continue;
          } else {
            // S, V — too ambiguous as single-char starters, skip
            continue;
          }
        }

        // ── Weak 2-char DI validation ──
        // Only valid as field starters at position 0 or after non-alnum
        if (!isStrong && prefix.length === 2) {
          if (pos > 0 && /[A-Za-z0-9]/.test(body[pos - 1])) continue;
        }

        // ── Extract value ──
        const after = body.slice(pos + prefix.length);
        let value = '';

        if (key === 'quantity') {
          // Digit-by-digit scan: stop when remaining starts with a strong DI
          const digits = [];
          for (let i = 0; i < after.length; i++) {
            if (!/\d/.test(after[i])) break;
            // After the first digit, check if this position starts a strong DI
            if (i > 0 && _STRONG_BOUNDARY_RE.test(after.slice(i))) break;
            digits.push(after[i]);
          }
          value = digits.join('');
          if (value && !(key in fields)) {
            const q = parseInt(value, 10);
            if (!isNaN(q) && q > 0) fields[key] = q;
          }
        } else {
          // Use STRONG boundaries only for value termination
          const bm = _STRONG_BOUNDARY_RE.exec(after);
          if (bm && bm.index > 0) {
            value = after.slice(0, bm.index);
          } else if (bm && bm.index === 0) {
            value = '';
          } else {
            value = after;
          }

          if (value && key && !(key in fields)) {
            fields[key] = value.trim();
          }
        }

        pos += prefix.length + value.length;
        matchCount++;
        matched = true;
        break;
      }

      if (!matched) {
        pos++;
      }
    }

    // Populate result from extracted fields
    if (fields.mpn)         result.mpn         = fields.mpn;
    if (fields.quantity)    result.quantity     = fields.quantity;
    if (fields.batch)       result.batch       = fields.batch;
    if (fields.supplierSku) result.supplierSku = fields.supplierSku;
    if (fields.poNumber)    result.poNumber    = fields.poNumber;

    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found && matchCount >= 2) {
      result.source = 'heuristic';
      return true;
    }
    return false;
  }


  // ────────────────────────────────────────────────────────────────────────────
  //  Stage 2b – Regex scan (non-DataMatrix, DI labels with whitespace)
  // ────────────────────────────────────────────────────────────────────────────

  function _tryRegexParse(raw, result) {
    if (!/(?:^|\s)(?:1P|30P|P|Q|1T|K)/.test(raw)) return false;

    const m1P = raw.match(/(?:^|\s)1P([^\s]+)/);
    if (m1P && !result.mpn) result.mpn = m1P[1];

    const m30P = raw.match(/(?:^|\s)30P([^\s]+)/);
    if (m30P && !result.supplierSku) result.supplierSku = m30P[1];

    const mP = raw.match(/(?:^|\s)P([^\s]+)/);
    if (mP) {
      if (!result.mpn) result.mpn = mP[1];
      else result.supplierSku = result.supplierSku || mP[1];
    }

    if (result.quantity === null) {
      const mQ = raw.match(/(?:^|\s)Q(\d+)/);
      if (mQ) result.quantity = parseInt(mQ[1], 10);
    }

    if (!result.batch) {
      const m1T = raw.match(/(?:^|\s)1T([^\s]+)/);
      if (m1T) result.batch = m1T[1];
    }

    if (!result.poNumber) {
      const mK = raw.match(/(?:^|\s)K(\S+)/);
      if (mK) result.poNumber = mK[1];
    }

    const found = !!(result.mpn || result.quantity !== null || result.batch);
    if (found) result.source = 'regex';
    return found;
  }


  // ────────────────────────────────────────────────────────────────────────────
  //  Stage 3 – Graceful fallback
  // ────────────────────────────────────────────────────────────────────────────

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

  /**
   * Classify barcode payload into native internal routes or distributor DataMatrix.
   */
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

  function _fallbackParse(raw, result) {
    const cleaned = raw.replace(/[\x00-\x1f\x7f]+/g, '').trim();
    if ((cleaned.startsWith('{') && cleaned.endsWith('}')) || cleaned.startsWith('INV-')) {
      result.source = 'native_internal';
      const classification = classifyBarcode(cleaned);
      if (classification && classification.url) {
        result.nativeObject = classification;
      }
      return;
    }
    if (cleaned) {
      result.mpn    = cleaned;
      result.source = 'fallback';
    }
  }


  // ────────────────────────────────────────────────────────────────────────────
  //  Public: parse()
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * Main entry point.  Returns a normalised result object:
   * {
   *   mpn:         string  – Manufacturer Part Number (may be empty)
   *   quantity:    number|null
   *   batch:       string
   *   supplierSku: string
   *   poNumber:    string
   *   raw:         string  – original unmodified input
   *   source:      'ansi' | 'heuristic' | 'regex' | 'fallback' | ''
   * }
   */
  function parse(raw) {
    const result = {
      mpn:         '',
      quantity:    null,
      batch:       '',
      supplierSku: '',
      poNumber:    '',
      raw:         raw || '',
      source:      '',
    };

    if (!raw || !raw.trim()) return result;

    // Stage 1: full ANSI structured parse (real separators present)
    if (_tryAnsiParse(raw, result)) return result;

    // Stage 2: heuristic iterative scan (header present, separators stripped)
    if (_tryHeuristicParse(raw, result)) return result;

    // Stage 2b: regex scan for space-separated DI labels
    if (_tryRegexParse(raw, result)) return result;

    // Stage 3: plain barcode / manual input – never discard
    _fallbackParse(raw, result);
    return result;
  }


  // ── Scanner Wedge Listener ─────────────────────────────────────────────────
  //
  // Barcode scanners in USB HID / keyboard-wedge mode emit characters very
  // rapidly (< 2 ms between chars) and finish with an Enter keystroke.
  // Human typing is typically > 80 ms between keystrokes.
  //
  // Strategy:
  //   • Accumulate all keystrokes and their timestamps in a buffer.
  //   • On Enter, inspect timing: if ≥ 80 % of inter-key gaps were < 50 ms
  //     AND the buffer is long enough → treat as barcode scan.
  //   • Consume the Enter and invoke the callback with the raw string.
  //   • Clear any chars that accidentally landed in the active input element.

  const MAX_INTER_KEY_MS = 50;   // threshold: chars faster than this = scanner
  const MIN_SCAN_LENGTH  = 4;    // lowered from 6 – short MPNs (e.g. "LM35") still qualify
  const IDLE_RESET_MS    = 600;  // reset buffer after idle

  let _buffer   = '';
  let _times    = [];
  let _lastTime = 0;
  let _callback = null;

  function isSmartPartsActive() {
    if (typeof window === 'undefined') return false;

    if (
      window.SMARTPARTS_ACTIVE_PANEL === true ||
      window._smartparts_active === true ||
      window.SmartPartsActive === true ||
      window._smartparts_local_scanner_active === true
    ) {
      return true;
    }

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
    } catch (_) {}

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
      } catch (_) {}
    }

    return false;
  }

  function _onKey(e) {
    if (typeof window !== 'undefined' && window.location.pathname && window.location.pathname.includes('/purescan')) {
      return;
    }

    try {
      e._handledBySmartParts = true;
    } catch (_) {}

    const now   = Date.now();
    const delta = now - _lastTime;
    _lastTime   = now;

    // ── Enter = end of scan attempt ─────────────────────────────────────────
    if (e.key === 'Enter') {
      const captured      = _buffer;
      const capturedTimes = _times.slice();
      _buffer = '';
      _times  = [];

      if (captured.length < MIN_SCAN_LENGTH) return; // too short

      // Measure how many inter-key gaps were "scanner fast"
      let fastGaps = 0;
      for (let i = 1; i < capturedTimes.length; i++) {
        if (capturedTimes[i] - capturedTimes[i - 1] < MAX_INTER_KEY_MS) fastGaps++;
      }
      const totalGaps = Math.max(capturedTimes.length - 1, 1);
      const fastRatio = fastGaps / totalGaps;

      if (fastRatio >= 0.8) {
        // ✅ Barcode scan detected
        e.preventDefault();
        e.stopPropagation();

        // Clear any scan chars that landed in the focused input before interception
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
          const val = ae.value || '';
          const firstChunk = captured.slice(0, Math.min(8, captured.length));
          if (val.includes(firstChunk) || val.endsWith(captured.slice(-4))) {
            ae.value = '';
          }
        }

        // ── 1. Check for native InvenTree internal routes ──
        const classification = classifyBarcode(captured);
        if (classification.url) {
          navigateToRoute(classification.url, classification.label, classification.id);
          return;
        }

        if (_callback) _callback(captured, parse(captured));
      }
      return;
    }

    // Ignore non-printable / modifier keys
    if (e.key.length > 1) return;

    // Reset idle buffer
    if (delta > IDLE_RESET_MS && _buffer.length > 0) {
      _buffer = '';
      _times  = [];
    }

    _buffer += e.key;
    _times.push(now);
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

  function showToast(message, type = 'info', duration = 3000) {
    if (typeof document === 'undefined') return;
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

    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
      });
    }

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 200);
    }, duration);
  }

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

    setTimeout(() => {
      if (typeof window !== 'undefined' && window.location) {
        if (typeof window.location.assign === 'function') {
          window.location.assign(targetUrl);
        } else {
          window.location.href = targetUrl;
        }
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

  function init(callback) {
    _callback = callback;
    if (typeof window !== 'undefined') {
      window._smartparts_local_scanner_active = true;
    }
    document.addEventListener('keydown', _onKey, { capture: true });
  }

  function destroy() {
    document.removeEventListener('keydown', _onKey, { capture: true });
    _callback = null;
    _buffer   = '';
    _times    = [];
    if (typeof window !== 'undefined') {
      window._smartparts_local_scanner_active = false;
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  global.SmartPartsScanner = {
    init,
    destroy,
    parse,
    classifyBarcode,
    resolveNativeUrlFromApiResponse,
    normalizeModelKey,
    NATIVE_MODEL_ROUTES,
    navigateToRoute,
    isSmartPartsActive,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      init,
      destroy,
      parse,
      classifyBarcode,
      resolveNativeUrlFromApiResponse,
      normalizeModelKey,
      NATIVE_MODEL_ROUTES,
      navigateToRoute,
      isSmartPartsActive,
    };
  }

})(typeof window !== 'undefined' ? window : globalThis);
