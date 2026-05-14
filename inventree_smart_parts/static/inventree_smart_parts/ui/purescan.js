/**
 * PureScan – Zero-Click Barcode State Machine  (v2 – Combo/Undo/Rich)
 * =====================================================================
 * A 100% mouse-free warehouse terminal.  Operators execute InvenTree
 * actions purely by sequential barcode scans.
 *
 * v2 additions:
 *   - Rich Info mode (part image, location path, tracking history)
 *   - Combo-timer quantity accumulation for Add/Remove/Stocktake
 *   - Action log with Undo stack
 */
(function (global) {
  'use strict';

  // ═══════════════════════════════════════════════════════════════════════════
  //  Constants
  // ═══════════════════════════════════════════════════════════════════════════

  const STATES = {
    IDLE:            'IDLE',
    AWAITING_ITEM:   'AWAITING_ITEM',
    AWAITING_PARAM:  'AWAITING_PARAM',
    COMBO:           'COMBO',          // NEW: accumulating qty scans
    PROCESSING:      'PROCESSING',
    SUCCESS:         'SUCCESS',
    ERROR:           'ERROR',
  };

  const ACTIONS = {
    TRANSFER:  'TRANSFER',
    INFO:      'INFO',
    ADD:       'ADD',
    REMOVE:    'REMOVE',
    STOCKTAKE: 'STOCKTAKE',
  };

  // Map control-code strings to actions
  const CONTROL_CODES = {
    'SYS:TRANSFER':  ACTIONS.TRANSFER,
    'SYS:INFO':      ACTIONS.INFO,
    'SYS:ADD':       ACTIONS.ADD,
    'SYS:REMOVE':    ACTIONS.REMOVE,
    'SYS:STOCKTAKE': ACTIONS.STOCKTAKE,
    // Aliases
    'SYS:MOVE':      ACTIONS.TRANSFER,
    'SYS:LOOKUP':    ACTIONS.INFO,
  };

  const RESULT_DISPLAY_MS = 4000;
  const COMBO_TIMEOUT_MS  = 3000;   // Time to wait for additional qty scans
  const MAX_UNDO_HISTORY  = 20;
  const LOCALSTORAGE_KEY  = 'purescan_history';

  // ═══════════════════════════════════════════════════════════════════════════
  //  State
  // ═══════════════════════════════════════════════════════════════════════════

  let _state        = STATES.IDLE;
  let _action       = null;
  let _stockItemId  = null;
  let _itemInfo     = null;
  let _resultTimer  = null;
  let _onStateChange = null;

  // Combo-timer state
  let _comboQty     = 0;
  let _comboTimer   = null;

  // Action history / undo stack
  let _actionLog  = [];    // { ts, description, payload, actionType, undone }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Barcode parsing helpers
  // ═══════════════════════════════════════════════════════════════════════════

  function _parseInvenTreeBarcode(raw) {
    const trimmed = raw.trim();

    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const obj = JSON.parse(trimmed);
        for (const key of ['stockitem', 'stocklocation', 'part']) {
          if (obj[key] != null) {
            return { type: key, id: parseInt(obj[key], 10) };
          }
        }
      } catch (e) { /* not JSON */ }
    }

    const kvMatch = trimmed.match(/^(stockitem|stocklocation|part)[=:](\d+)$/i);
    if (kvMatch) {
      return { type: kvMatch[1].toLowerCase(), id: parseInt(kvMatch[2], 10) };
    }

    return { type: null, id: null };
  }

  function _parseControlCode(raw) {
    const upper = raw.trim().toUpperCase();
    if (upper === 'SYS:UNDO') return '__UNDO__';
    return CONTROL_CODES[upper] || null;
  }

  function _parseQuantityCode(raw) {
    const m = raw.trim().toUpperCase().match(/^SYS:QTY:(\d+(?:\.\d+)?)$/);
    return m ? parseFloat(m[1]) : null;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  State transitions
  // ═══════════════════════════════════════════════════════════════════════════

  function _setState(newState, detail, extra) {
    _state = newState;
    if (_onStateChange) {
      _onStateChange({
        state:       _state,
        action:      _action,
        stockItemId: _stockItemId,
        itemInfo:    _itemInfo,
        detail:      detail || '',
        comboQty:    _comboQty,
        actionLog:   _actionLog,
        ...extra,
      });
    }
  }

  function _resetToIdle(detail) {
    _action      = null;
    _stockItemId = null;
    _itemInfo    = null;
    _comboQty    = 0;
    clearTimeout(_resultTimer);
    clearTimeout(_comboTimer);
    _setState(STATES.IDLE, detail || 'Ready — Scan an Action Code');
  }

  function _showResult(state, detail, autoResetMs) {
    _setState(state, detail);
    clearTimeout(_resultTimer);
    _resultTimer = setTimeout(() => _resetToIdle(), autoResetMs || RESULT_DISPLAY_MS);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Action Log / Undo  (persisted to localStorage)
  // ═══════════════════════════════════════════════════════════════════════════

  function _saveLog() {
    try {
      window.localStorage.setItem(LOCALSTORAGE_KEY, JSON.stringify(_actionLog));
    } catch (e) { /* quota exceeded or private mode — ignore */ }
  }

  function _loadLog() {
    try {
      const raw = window.localStorage.getItem(LOCALSTORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          _actionLog = parsed.slice(0, MAX_UNDO_HISTORY);
        }
      }
    } catch (e) { /* corrupt data — ignore */ }
  }

  function clearLog() {
    _actionLog.length = 0;
    _saveLog();
    // Re-render by emitting current state
    _setState(_state, 'History cleared');
  }

  function _pushLog(entry) {
    _actionLog.unshift(entry);
    if (_actionLog.length > MAX_UNDO_HISTORY) _actionLog.pop();
    _saveLog();
  }

  function _getLastUndoable() {
    return _actionLog.find(e => !e.undone);
  }

  async function _executeUndo() {
    const last = _getLastUndoable();
    if (!last) {
      _showResult(STATES.ERROR, 'Nothing to undo', 2000);
      return;
    }

    _setState(STATES.PROCESSING, `Undoing: ${last.description}`);
    try {
      const p = last.payload;
      let undoResult = '';

      if (last.actionType === 'ADD') {
        await _apiPost('/api/stock/remove/', {
          items: [{ pk: p.stockItemId, quantity: p.quantity }],
          notes: `PureScan UNDO: Reverting Add of ${p.quantity}`,
        });
        undoResult = `↩ Removed ${p.quantity}× "${p.partName}" (reverted Add)`;

      } else if (last.actionType === 'REMOVE') {
        await _apiPost('/api/stock/add/', {
          items: [{ pk: p.stockItemId, quantity: p.quantity }],
          notes: `PureScan UNDO: Reverting Remove of ${p.quantity}`,
        });
        undoResult = `↩ Added back ${p.quantity}× "${p.partName}" (reverted Remove)`;

      } else if (last.actionType === 'TRANSFER') {
        await _apiPost('/api/stock/transfer/', {
          items: [{ pk: p.stockItemId, quantity: p.quantity }],
          location: p.fromLocationId,
          notes: `PureScan UNDO: Reverting transfer back to ${p.fromLocation}`,
        });
        undoResult = `↩ Moved "${p.partName}" back to ${p.fromLocation} (reverted Transfer)`;

      } else if (last.actionType === 'STOCKTAKE') {
        await _apiPost('/api/stock/count/', {
          items: [{ pk: p.stockItemId, quantity: p.previousQty }],
          notes: `PureScan UNDO: Reverting Stocktake (was ${p.quantity}, restoring ${p.previousQty})`,
        });
        undoResult = `↩ Restored qty to ${p.previousQty} for "${p.partName}" (reverted Stocktake)`;

      } else {
        _showResult(STATES.ERROR, `Cannot undo action type: ${last.actionType}`, 3000);
        return;
      }

      last.undone = true;
      _pushLog({
        ts: new Date(),
        description: undoResult,
        actionType: 'UNDO',
        undone: true,
        payload: null,
      });
      _saveLog();   // Persist the undone flag change
      _showResult(STATES.SUCCESS, undoResult, 5000);

    } catch (e) {
      _showResult(STATES.ERROR, `Undo failed: ${e.message}`, 5000);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  API helpers
  // ═══════════════════════════════════════════════════════════════════════════

  function _getCSRF() {
    const el = document.getElementById('ps-csrf');
    if (el) return el.value;
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  async function _apiPost(url, body) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': _getCSRF(),
      },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.detail || errData.error || `HTTP ${resp.status}`);
    }
    return resp.json().catch(() => ({}));
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Fetch helpers (Rich Info)
  // ═══════════════════════════════════════════════════════════════════════════

  /** Fetch stock item with full part + location detail */
  async function _fetchStockItemInfo(stockItemId) {
    try {
      const resp = await fetch(
        `/api/stock/${stockItemId}/?part_detail=true&location_detail=true`, {
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      return {
        stock_id:     data.pk,
        part_id:      data.part,
        part_name:    data.part_detail?.full_name || data.part_detail?.name || `Part #${data.part}`,
        part_image:   data.part_detail?.thumbnail || data.part_detail?.image || '',
        quantity:     data.quantity,
        location:     data.location_detail?.pathstring || data.location_detail?.name || 'No location',
        location_id:  data.location,
        serial:       data.serial || '',
        batch:        data.batch || '',
        status:       data.status_text || '',
      };
    } catch (e) {
      console.error('[PureScan] Failed to fetch stock item:', e);
      return null;
    }
  }

  /** Fetch the last N tracking entries for a stock item */
  async function _fetchTrackingHistory(stockItemId, limit) {
    try {
      const resp = await fetch(
        `/api/stock/track/?item=${stockItemId}&limit=${limit || 3}&ordering=-date`, {
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) return [];
      const data = await resp.json();
      const results = Array.isArray(data) ? data : (data.results || []);
      return results.slice(0, limit || 3).map(entry => ({
        date:  entry.date || '',
        user:  entry.user_detail?.username || entry.user || '?',
        title: entry.title || entry.label || '',
        notes: entry.notes || '',
      }));
    } catch (e) {
      console.warn('[PureScan] Tracking history fetch failed:', e);
      return [];
    }
  }

  /** Fetch location metadata */
  async function _fetchLocationInfo(locationId) {
    try {
      const resp = await fetch(`/api/stock/location/${locationId}/`, {
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      return {
        id:        data.pk,
        name:      data.name || `Location #${data.pk}`,
        pathstring: data.pathstring || data.name || '',
        description: data.description || '',
        items:     data.items || 0,
      };
    } catch (e) {
      console.error('[PureScan] Failed to fetch location:', e);
      return null;
    }
  }

  /** Fetch all stock items at a given location */
  async function _fetchLocationInventory(locationId) {
    try {
      const resp = await fetch(
        `/api/stock/?location=${locationId}&part_detail=true&limit=100`, {
        headers: { 'Accept': 'application/json' },
      });
      if (!resp.ok) return [];
      const data = await resp.json();
      const results = Array.isArray(data) ? data : (data.results || []);
      return results.map(item => ({
        stock_id:   item.pk,
        part_id:    item.part,
        part_name:  item.part_detail?.full_name || item.part_detail?.name || `Part #${item.part}`,
        part_image: item.part_detail?.thumbnail || item.part_detail?.image || '',
        quantity:   item.quantity,
        serial:     item.serial || '',
        batch:      item.batch || '',
        status:     item.status_text || '',
      }));
    } catch (e) {
      console.warn('[PureScan] Location inventory fetch failed:', e);
      return [];
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Action handlers
  // ═══════════════════════════════════════════════════════════════════════════

  /** Rich info display with tracking (stock item) */
  async function _execInfo(stockItemId, info) {
    const tracking = await _fetchTrackingHistory(stockItemId, 3);
    _setState(STATES.SUCCESS, '', {
      richInfo: { info, tracking },
    });
    clearTimeout(_resultTimer);
    _resultTimer = setTimeout(() => _resetToIdle(), 10000);
  }

  /** Location inventory display */
  async function _execLocationInfo(locationId) {
    _setState(STATES.PROCESSING, 'Loading location inventory…');
    const [locInfo, inventory] = await Promise.all([
      _fetchLocationInfo(locationId),
      _fetchLocationInventory(locationId),
    ]);
    if (!locInfo) {
      _showResult(STATES.ERROR, `Location #${locationId} not found`);
      return;
    }
    _setState(STATES.SUCCESS, '', {
      locationInfo: { location: locInfo, inventory },
    });
    clearTimeout(_resultTimer);
    _resultTimer = setTimeout(() => _resetToIdle(), 20000);
  }

  /** Execute: Transfer stock item to a new location */
  async function _execTransfer(stockItemId, locationId) {
    const prevLocId   = _itemInfo?.location_id;
    const prevLocName = _itemInfo?.location;
    _setState(STATES.PROCESSING, 'Transferring stock item…');
    try {
      await _apiPost('/api/stock/transfer/', {
        items: [{ pk: stockItemId, quantity: _itemInfo?.quantity }],
        location: locationId,
        notes: `PureScan: Transferred "${_itemInfo?.part_name}" from ${_itemInfo?.location}`,
      });
      const updatedInfo = await _fetchStockItemInfo(stockItemId);
      const locName = updatedInfo?.location || `Location #${locationId}`;
      const desc = `Transferred "${_itemInfo?.part_name}" → ${locName}`;
      _pushLog({
        ts: new Date(),
        description: desc,
        actionType: 'TRANSFER',
        undone: false,
        payload: {
          stockItemId,
          quantity: _itemInfo?.quantity,
          partName: _itemInfo?.part_name,
          toLocationId: locationId,
          toLocation: locName,
          fromLocationId: prevLocId,
          fromLocation: prevLocName,
        },
      });

      // ── Auto-print label for the transferred item ──
      let printMsg = '';
      try {
        const printResult = await _apiPost('/plugin/smartparts/api/purescan/print/', {
          stock_item_id: stockItemId,
        });
        printMsg = printResult.success
          ? '\n🖨 Label sent to printer'
          : `\n⚠ Label print failed: ${printResult.error || 'unknown'}`;
      } catch (printErr) {
        printMsg = `\n⚠ Label print skipped: ${printErr.message}`;
      }

      _showResult(STATES.SUCCESS, `✓ ${desc}${printMsg}`);
    } catch (e) {
      _showResult(STATES.ERROR, `Transfer failed: ${e.message}`);
    }
  }

  // ── Combo-timer execution ─────────────────────────────────────────────────

  function _startCombo(qty) {
    clearTimeout(_comboTimer);
    _comboQty = qty;
    _setState(STATES.COMBO,
      `${_action}: "${_itemInfo?.part_name}"\nQuantity: ${_comboQty}   ⏳ Scan more QTY codes or wait 3s…`);
    _comboTimer = setTimeout(() => _executeCombo(), COMBO_TIMEOUT_MS);
  }

  function _addCombo(qty) {
    clearTimeout(_comboTimer);
    _comboQty += qty;
    _setState(STATES.COMBO,
      `${_action}: "${_itemInfo?.part_name}"\nQuantity: ${_comboQty}   ⏳ Scan more QTY codes or wait 3s…`);
    _comboTimer = setTimeout(() => _executeCombo(), COMBO_TIMEOUT_MS);
  }

  async function _executeCombo() {
    _comboTimer = null;
    const qty = _comboQty;
    const action = _action;
    const itemId = _stockItemId;
    const info = _itemInfo;

    if (action === ACTIONS.ADD) {
      _setState(STATES.PROCESSING, `Adding ${qty}× to "${info?.part_name}"…`);
      try {
        await _apiPost('/api/stock/add/', {
          items: [{ pk: itemId, quantity: qty }],
          notes: `PureScan: Add ${qty}`,
        });
        const newQty = (info?.quantity || 0) + qty;
        const desc = `Added ${qty}× "${info?.part_name}" (now ${newQty})`;
        _pushLog({
          ts: new Date(), description: desc, actionType: 'ADD', undone: false,
          payload: { stockItemId: itemId, quantity: qty, partName: info?.part_name, previousQty: info?.quantity },
        });
        _showResult(STATES.SUCCESS, `✓ ${desc}`);
      } catch (e) {
        _showResult(STATES.ERROR, `Add stock failed: ${e.message}`);
      }

    } else if (action === ACTIONS.REMOVE) {
      // ── Depletion safeguard: block removal that would zero-out the item ──
      const currentQty = info?.quantity || 0;
      if (qty >= currentQty) {
        _showResult(STATES.ERROR,
          `⛔ Cannot remove ${qty}× — only ${currentQty}× available.\n"${info?.part_name}" would be depleted/deleted.\nUse manual correction for full depletion.`, 6000);
        return;
      }

      _setState(STATES.PROCESSING, `Removing ${qty}× from "${info?.part_name}"…`);
      try {
        await _apiPost('/api/stock/remove/', {
          items: [{ pk: itemId, quantity: qty }],
          notes: `PureScan: Remove ${qty}`,
        });
        const newQty = Math.max(0, currentQty - qty);
        const desc = `Removed ${qty}× "${info?.part_name}" (now ${newQty})`;
        _pushLog({
          ts: new Date(), description: desc, actionType: 'REMOVE', undone: false,
          payload: { stockItemId: itemId, quantity: qty, partName: info?.part_name, previousQty: info?.quantity },
        });
        _showResult(STATES.SUCCESS, `✓ ${desc}`);
      } catch (e) {
        _showResult(STATES.ERROR, `Remove stock failed: ${e.message}`);
      }

    } else if (action === ACTIONS.STOCKTAKE) {
      _setState(STATES.PROCESSING, `Setting count to ${qty} for "${info?.part_name}"…`);
      try {
        await _apiPost('/api/stock/count/', {
          items: [{ pk: itemId, quantity: qty }],
          notes: `PureScan: Stocktake = ${qty}`,
        });
        const desc = `Stocktake: "${info?.part_name}" counted as ${qty}`;
        _pushLog({
          ts: new Date(), description: desc, actionType: 'STOCKTAKE', undone: false,
          payload: { stockItemId: itemId, quantity: qty, partName: info?.part_name, previousQty: info?.quantity },
        });
        _showResult(STATES.SUCCESS, `✓ ${desc}`);
      } catch (e) {
        _showResult(STATES.ERROR, `Stocktake failed: ${e.message}`);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Main scan handler
  // ═══════════════════════════════════════════════════════════════════════════

  async function handleScan(rawString) {
    const raw = (rawString || '').trim();
    if (!raw) return;

    // ── Global: SYS:CANCEL always resets ─────────────────────────────────
    if (raw.toUpperCase() === 'SYS:CANCEL' || raw.toUpperCase() === 'SYS:RESET') {
      clearTimeout(_comboTimer);
      _resetToIdle('Cancelled — scan a new Action Code');
      return;
    }

    // ── Global: SYS:CLEARLOG clears the action log ──────────────────────
    if (raw.toUpperCase() === 'SYS:CLEARLOG' || raw.toUpperCase() === 'SYS:CLEAR') {
      clearLog();
      _showResult(STATES.SUCCESS, '🗑 Action log cleared', 2000);
      return;
    }

    // ── Global: SYS:UNDO ─────────────────────────────────────────────────
    const ctrlCode = _parseControlCode(raw);
    if (ctrlCode === '__UNDO__') {
      clearTimeout(_comboTimer);
      await _executeUndo();
      return;
    }

    // ── STATE: COMBO — accumulating qty scans ────────────────────────────
    if (_state === STATES.COMBO) {
      // Action code switches abort combo and start new flow
      if (ctrlCode && ctrlCode !== '__UNDO__') {
        clearTimeout(_comboTimer);
        _comboQty = 0;
        _action = ctrlCode;
        _stockItemId = null;
        _itemInfo = null;
        _setState(STATES.AWAITING_ITEM,
          `Action: ${ctrlCode} — Now scan a Stock Item label`);
        return;
      }

      // Quantity code: accumulate
      const qty = _parseQuantityCode(raw);
      if (qty !== null && qty > 0) {
        if (_action === ACTIONS.STOCKTAKE) {
          // For stocktake, QTY replaces, not adds
          _startCombo(qty);
        } else {
          _addCombo(qty);
        }
        return;
      }

      // Re-scanning the same stock item → add 1 more
      const bc = _parseInvenTreeBarcode(raw);
      if (bc.type === 'stockitem' && bc.id === _stockItemId) {
        if (_action !== ACTIONS.STOCKTAKE) {
          _addCombo(1);
        }
        return;
      }

      // Unknown scan during combo — ignore
      return;
    }

    // ── STATE: IDLE / SUCCESS / ERROR ────────────────────────────────────
    if (_state === STATES.IDLE || _state === STATES.SUCCESS || _state === STATES.ERROR) {
      clearTimeout(_resultTimer);

      if (ctrlCode) {
        _action = ctrlCode;
        _stockItemId = null;
        _itemInfo = null;
        _setState(STATES.AWAITING_ITEM,
          `Action: ${ctrlCode} — Now scan a Stock Item label`);
        return;
      }

      // Quick-scan shortcut: stock item in IDLE → Info
      const bc = _parseInvenTreeBarcode(raw);
      if (bc.type === 'stockitem' && bc.id) {
        _action = ACTIONS.INFO;
        _stockItemId = bc.id;
        _setState(STATES.PROCESSING, 'Looking up stock item…');
        const info = await _fetchStockItemInfo(bc.id);
        if (!info) {
          _showResult(STATES.ERROR, `Stock Item #${bc.id} not found`);
          return;
        }
        _itemInfo = info;
        await _execInfo(bc.id, info);
        return;
      }

      // Quick-scan shortcut: location in IDLE → Location Info
      if (bc.type === 'stocklocation' && bc.id) {
        _action = ACTIONS.INFO;
        await _execLocationInfo(bc.id);
        return;
      }

      _showResult(STATES.ERROR,
        `Unknown scan: "${raw.slice(0, 40)}"\nScan an Action Code to begin`, 3000);
      return;
    }

    // ── STATE: AWAITING_ITEM ─────────────────────────────────────────────
    if (_state === STATES.AWAITING_ITEM) {
      if (ctrlCode) {
        _action = ctrlCode;
        _stockItemId = null;
        _itemInfo = null;
        _setState(STATES.AWAITING_ITEM,
          `Action: ${ctrlCode} — Now scan a Stock Item label`);
        return;
      }

      const bc = _parseInvenTreeBarcode(raw);

      // INFO mode also accepts location barcodes
      if (_action === ACTIONS.INFO && bc.type === 'stocklocation' && bc.id) {
        await _execLocationInfo(bc.id);
        return;
      }

      if (bc.type !== 'stockitem' || !bc.id) {
        _setState(STATES.AWAITING_ITEM,
          `⚠ Not a Stock Item label! Scan a valid Stock Item${_action === ACTIONS.INFO ? ' or Location' : ''}.\nAction: ${_action}`);
        return;
      }

      _stockItemId = bc.id;
      _setState(STATES.PROCESSING, 'Looking up stock item…');
      const info = await _fetchStockItemInfo(bc.id);
      if (!info) {
        _showResult(STATES.ERROR, `Stock Item #${bc.id} not found in InvenTree`);
        return;
      }
      _itemInfo = info;

      // Route based on action
      if (_action === ACTIONS.INFO) {
        await _execInfo(bc.id, info);
        return;
      }

      if (_action === ACTIONS.TRANSFER) {
        _setState(STATES.AWAITING_PARAM,
          `Transfer: "${info.part_name}" (${info.quantity}×)\n📍 From: ${info.location}\n→ Now scan the DESTINATION Location label`);
        return;
      }

      if (_action === ACTIONS.ADD || _action === ACTIONS.REMOVE || _action === ACTIONS.STOCKTAKE) {
        // Enter combo mode with qty=0 — wait for first qty scan
        _setState(STATES.AWAITING_PARAM,
          `${_action}: "${info.part_name}" (current: ${info.quantity}×)\n→ Scan a Quantity Code (SYS:QTY:nnn) or scan item again for +1`);
        return;
      }

      _showResult(STATES.ERROR, `Unknown action: ${_action}`);
      return;
    }

    // ── STATE: AWAITING_PARAM ────────────────────────────────────────────
    if (_state === STATES.AWAITING_PARAM) {
      if (ctrlCode) {
        _action = ctrlCode;
        _stockItemId = null;
        _itemInfo = null;
        _setState(STATES.AWAITING_ITEM,
          `Action: ${ctrlCode} — Now scan a Stock Item label`);
        return;
      }

      const bc = _parseInvenTreeBarcode(raw);

      // TRANSFER: needs a location
      if (_action === ACTIONS.TRANSFER) {
        if (bc.type === 'stocklocation' && bc.id) {
          await _execTransfer(_stockItemId, bc.id);
          return;
        }
        _setState(STATES.AWAITING_PARAM,
          `⚠ Not a Location label! Scan a Location barcode.\nTransfer: "${_itemInfo?.part_name}" → ???`);
        return;
      }

      // ADD / REMOVE / STOCKTAKE: enter combo mode
      if (_action === ACTIONS.ADD || _action === ACTIONS.REMOVE || _action === ACTIONS.STOCKTAKE) {
        // Quantity code starts combo
        const qty = _parseQuantityCode(raw);
        if (qty !== null && qty > 0) {
          _startCombo(qty);
          return;
        }

        // Re-scanning the stock item → combo with +1
        if (bc.type === 'stockitem' && bc.id === _stockItemId) {
          if (_action !== ACTIONS.STOCKTAKE) {
            _startCombo(1);
          }
          return;
        }

        _setState(STATES.AWAITING_PARAM,
          `⚠ Invalid quantity! Scan a QTY code (SYS:QTY:nnn) or scan item for +1\n${_action}: "${_itemInfo?.part_name}"`);
        return;
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Scanner wedge listener
  // ═══════════════════════════════════════════════════════════════════════════

  const MAX_INTER_KEY_MS = 50;
  const MIN_SCAN_LENGTH  = 3;
  const IDLE_RESET_MS    = 600;

  let _buffer   = '';
  let _times    = [];
  let _lastTime = 0;

  function _onKey(e) {
    const now   = Date.now();
    const delta = now - _lastTime;
    _lastTime   = now;

    if (e.key === 'Enter') {
      const captured      = _buffer;
      const capturedTimes = _times.slice();
      _buffer = '';
      _times  = [];

      if (captured.length < MIN_SCAN_LENGTH) return;

      let fastGaps = 0;
      for (let i = 1; i < capturedTimes.length; i++) {
        if (capturedTimes[i] - capturedTimes[i - 1] < MAX_INTER_KEY_MS) fastGaps++;
      }
      const totalGaps = Math.max(capturedTimes.length - 1, 1);
      const fastRatio = fastGaps / totalGaps;

      if (fastRatio >= 0.75) {
        e.preventDefault();
        e.stopPropagation();

        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
          ae.value = '';
        }
        handleScan(captured);
      }
      return;
    }

    if (e.key.length > 1) return;

    if (delta > IDLE_RESET_MS && _buffer.length > 0) {
      _buffer = '';
      _times  = [];
    }

    _buffer += e.key;
    _times.push(now);
  }

  // ═══════════════════════════════════════════════════════════════════════════
  //  Public API
  // ═══════════════════════════════════════════════════════════════════════════

  function init(onStateChange) {
    _onStateChange = onStateChange;
    _loadLog();   // Restore from localStorage
    document.addEventListener('keydown', _onKey, { capture: true });
    _resetToIdle();
  }

  function destroy() {
    document.removeEventListener('keydown', _onKey, { capture: true });
    _onStateChange = null;
    clearTimeout(_resultTimer);
    clearTimeout(_comboTimer);
    _buffer = '';
    _times  = [];
  }

  function getActionLog() {
    return _actionLog;
  }

  global.PureScan = {
    init,
    destroy,
    handleScan,
    getActionLog,
    clearLog,
    STATES,
    ACTIONS,
    CONTROL_CODES,
  };

})(window);
