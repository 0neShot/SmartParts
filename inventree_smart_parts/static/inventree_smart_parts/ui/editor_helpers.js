/* editor_helpers.js – Pure utility functions, no DOM side-effects on load */

function getCookie(name) {
  // Prefer Django-injected token (reliable in panel/iframe contexts)
  const el = document.getElementById('sp-csrf');
  if (el && el.value) return el.value;
  // Fallback: cookie
  const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
  return v ? v.pop() : '';
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ── Field row builder ─────────────────────────────────────────── */
function buildField(id, label, apiVal, dbVal, type) {
  const av = String(apiVal ?? '');
  const dv = String(dbVal ?? '');
  // Don't flag image URLs as "changed" when the DB value is a local InvenTree
  // media path – the image was already downloaded and stored by InvenTree.
  const isLocalImage = dv.startsWith('/media/') && (id === 'edit-img' || label.toLowerCase().includes('image'));
  const changed = dv !== '' && av.trim() !== dv.trim() && !isLocalImage;
  const cls   = changed ? 'sp-input sp-input-changed' : 'sp-input';
  const note  = changed
    ? `<span class="sp-ref-note">Current: "${escHtml(dv.substring(0,80))}"</span>`
    : '';
  const ctrl  = (type === 'textarea')
    ? `<textarea id="${id}" class="${cls}" rows="2" style="resize:vertical" data-original="${escHtml(av)}" oninput="trackEdit(this)">${escHtml(av)}</textarea>`
    : `<input type="text" id="${id}" class="${cls}" value="${escHtml(av)}" data-original="${escHtml(av)}" oninput="trackEdit(this)">`;
  return `
    <div class="sp-field">
      <label for="${id}">${escHtml(label)}</label>
      <div class="sp-input-wrap">${ctrl}${note}</div>
    </div>`;
}

/* ── Parameter helpers ─────────────────────────────────────────── */
function mergeParams(apiParams, dbParams) {
  const dbMap = {};
  (dbParams || []).forEach(p => { dbMap[p.name.toLowerCase()] = p; });
  const rows = [];
  const seen = new Set();

  (apiParams || []).forEach(p => {
    const key = p.name.toLowerCase();
    seen.add(key);
    const db = dbMap[key];
    let status = 'existing', dbVal = null;
    if (!db) { status = 'new'; }
    else if (String(p.value).trim() !== String(db.value).trim()) {
      status = 'changed'; dbVal = db.value;
    }
    rows.push({ name: p.name, value: p.value, unit: p.unit || '', status, dbVal, manual: false });
  });

  (dbParams || []).forEach(p => {
    if (!seen.has(p.name.toLowerCase()))
      rows.push({ name: p.name, value: p.value, unit: p.unit || '', status: 'db_only', dbVal: null, manual: false });
  });
  return rows;
}

function buildParamRow(p) {
  let rowCls = 'sp-param-row', badge = '', note = '';
  if (p.status === 'new' || p.manual) {
    rowCls += ' sp-param-new';
    badge = `<span class="sp-badge sp-badge-new">${p.manual ? 'MANUAL' : '⚡ NEW'}</span>`;
  } else if (p.status === 'changed') {
    rowCls += ' sp-param-changed';
    badge = '<span class="sp-badge sp-badge-warn">⚠ changed</span>';
    note  = `<br><small class="sp-ref-note">Previous: "${escHtml(String(p.dbVal||'').substring(0,50))}"</small>`;
  } else if (p.status === 'db_only') {
    badge = '<span class="sp-badge sp-badge-db">DB</span>';
  }
  const nameRo = (!p.manual && p.status !== 'new') ? 'readonly' : '';
  const valCls = p.status === 'changed' ? 'sp-param-input changed' : 'sp-param-input';
  return `<tr class="${rowCls}">
    <td><button type="button" class="sp-del-btn" onclick="delParamRow(this)" title="Delete">×</button></td>
    <td><input type="text" class="sp-param-input" value="${escHtml(p.name)}" ${nameRo} placeholder="Name"></td>
    <td><input type="text" class="${valCls}" value="${escHtml(p.value)}" placeholder="Value">${note}</td>
    <td><input type="text" class="sp-param-input" value="${escHtml(p.unit)}" placeholder="—" style="width:70px"></td>
    <td>${badge}</td>
  </tr>`;
}

function buildParamsTable(rows) {
  const body = rows.map(buildParamRow).join('');
  return `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem">
      <strong>Parameters</strong>
      <button type="button" class="sp-btn sp-btn-outline sp-btn-sm" onclick="addParamRow()">
        <i class="fas fa-plus"></i> Add Parameter
      </button>
    </div>
    <table class="sp-param-table">
      <thead><tr>
        <th style="width:32px"></th><th>Name</th><th>Value</th>
        <th style="width:80px">Unit</th><th style="width:110px"></th>
      </tr></thead>
      <tbody id="paramTbody">${body}</tbody>
    </table>`;
}

/* ── Supplier helpers ──────────────────────────────────────────── */
function buildInitialSuppliers(searchData, existingData) {
  const list = [];
  const seen = new Set();

  // Normalise a company name for dedup: lowercase, strip common suffixes so
  // "Mouser" and "Mouser Electronics" don't produce duplicate entries.
  function normName(n) {
    return String(n || '').toLowerCase()
      .replace(/\s+(electronics|components|semiconductor|inc\.?|ltd\.?|gmbh|co\.?)$/i, '')
      .trim();
  }
  function dedupeKey(name, sku) {
    return `${normName(name)}:${String(sku||'').trim().toLowerCase()}`;
  }

  ['mouser','digikey','lcsc'].forEach(src => {
    const s = searchData.sources[src];
    if (!s || s.error) return;
    // Fallback: if API confirmed a match but SKU is empty, use MPN
    const sku = s.supplier_sku || s.mpn || searchData.merged?.mpn || '';
    if (!sku) return;  // truly nothing to work with
    const skuMissing = !s.supplier_sku;
    const key = dedupeKey(s.supplier_name || src, sku);
    if (seen.has(key)) return;
    seen.add(key);
    list.push({
      supplier_name: s.supplier_name || src,
      supplier_sku:  sku,
      supplier_url:  s.supplier_url  || '',
      price_breaks:  s.price_breaks  || [],
      _skuMissing:   skuMissing,
    });
  });

  // Merged primary (fallback)
  const m = searchData.merged;
  if (m.supplier_sku) {
    const key = dedupeKey(m.supplier_name, m.supplier_sku);
    if (!seen.has(key)) {
      seen.add(key);
      list.push({ supplier_name: m.supplier_name, supplier_sku: m.supplier_sku,
                  supplier_url: m.supplier_url, price_breaks: m.price_breaks || [] });
    }
  }

  // Existing DB suppliers not already in the list
  (existingData?.supplier_parts || []).forEach(sp => {
    const key = dedupeKey(sp.supplier_name, sp.supplier_sku);
    if (!seen.has(key)) {
      seen.add(key);
      list.push({ ...sp, _dbOnly: true });
    }
  });
  return list;
}

function buildPriceRow(pb) {
  return `<tr>
    <td><input type="number" class="sp-price-input" value="${escHtml(pb.quantity)}" min="1" style="width:60px"></td>
    <td><input type="number" class="sp-price-input" value="${escHtml(pb.price)}" step="0.0001" style="width:80px"></td>
    <td><input type="text" class="sp-price-input" value="${escHtml(pb.currency||'EUR')}" style="width:50px"></td>
    <td><button type="button" class="sp-del-btn" onclick="delPbRow(this)">×</button></td>
  </tr>`;
}

function buildSupplierCard(s, idx) {
  const pbRows = (s.price_breaks || []).map(buildPriceRow).join('');
  return `
    <div class="sp-supplier-card" data-idx="${idx}">
      <div class="sp-supplier-header">
        <span class="sp-supplier-name">
          <i class="fas fa-store" style="color:var(--sp-primary)"></i>
          <input type="text" class="sp-input" value="${escHtml(s.supplier_name)}"
                 placeholder="Supplier Name" style="font-weight:700;max-width:220px"
                 data-original="${escHtml(s.supplier_name)}" oninput="trackEdit(this)">
          ${s._dbOnly ? '<span class="sp-badge sp-badge-db">DB</span>' : ''}
          ${s._skuMissing ? '<span class="sp-badge sp-badge-warn" title="Original SKU was empty — using MPN as fallback">⚠ SKU Fallback</span>' : ''}
        </span>
        <button type="button" class="sp-btn sp-btn-danger sp-btn-sm" onclick="delSupplier(this)">
          <i class="fas fa-trash"></i> Remove
        </button>
      </div>
      <div class="sp-field">
        <label>SKU</label>
        <div class="sp-input-wrap">
          <input type="text" class="sp-input" value="${escHtml(s.supplier_sku)}" placeholder="Supplier SKU"
                 data-original="${escHtml(s.supplier_sku)}" oninput="trackEdit(this)">
        </div>
      </div>
      <div class="sp-field">
        <label>URL</label>
        <div class="sp-input-wrap">
          <input type="text" class="sp-input" value="${escHtml(s.supplier_url)}" placeholder="Product Link"
                 data-original="${escHtml(s.supplier_url)}" oninput="trackEdit(this)">
        </div>
      </div>
      <div style="margin-top:.5rem">
        <strong style="font-size:.8rem">Price Breaks</strong>
        <table class="sp-price-table" style="margin-top:.25rem">
          <thead><tr><th>Quantity</th><th>Price</th><th>Currency</th><th></th></tr></thead>
          <tbody>${pbRows}</tbody>
        </table>
        <button type="button" class="sp-btn sp-btn-outline sp-btn-sm" style="margin-top:.4rem"
                onclick="addPbRow(this)"><i class="fas fa-plus"></i> Add Price</button>
      </div>
    </div>`;
}

/* ── Category select ───────────────────────────────────────────── */
function buildCategorySelect(categories, selectedId) {
  let opts = '<option value="">-- Select Category --</option>';
  (categories || []).forEach(c => {
    const sel = (c.id === selectedId) ? 'selected' : '';
    opts += `<option value="${c.id}" ${sel}>${escHtml(c.name)}</option>`;
  });
  return `<select id="categorySelect" class="sp-input">${opts}</select>`;
}

/* ── Receive Stock section ─────────────────────────────────────── */
function buildReceiveStockSection(defaultQty) {
  return `
    <div class="sp-editor-section" id="receiveStockSection">
      <p class="sp-section-title" style="display:flex;align-items:center;gap:.6rem">
        <span>Receive Stock</span>
        <label style="font-size:.8rem;font-weight:400;display:flex;align-items:center;gap:.3rem;cursor:pointer">
          <input type="checkbox" id="enableReceive" onchange="toggleReceiveStock(this)"
                 ${defaultQty > 0 ? 'checked' : ''} style="width:14px;height:14px">
          Enable
        </label>
      </p>
      <div id="receiveStockFields" style="${defaultQty > 0 ? '' : 'display:none'}">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 1.5rem">
          <div class="sp-field">
            <label for="stockQty">Quantity</label>
            <div class="sp-input-wrap">
              <input type="number" id="stockQty" class="sp-input" value="${Math.max(1, Math.round(defaultQty || 1))}"
                     min="1" step="1" placeholder="1"
                     style="-moz-appearance:textfield">
            </div>
          </div>
          <div class="sp-field">
            <label for="stockBatch">Batch / Serial</label>
            <div class="sp-input-wrap">
              <input type="text" id="stockBatch" class="sp-input" placeholder="Optional batch code">
            </div>
          </div>
        </div>
        <div class="sp-field" style="margin-top:.5rem">
          <label for="stockLocation">Location</label>
          <div class="sp-input-wrap">
            <select id="stockLocation" class="sp-input">
              <option value="">⏳ Loading locations…</option>
            </select>
          </div>
        </div>
        <div style="margin-top:.5rem">
          <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;font-size:.85rem">
            <input type="checkbox" id="stockDeleteOnDeplete" checked style="width:14px;height:14px">
            Delete stock item when quantity reaches zero
          </label>
        </div>
      </div>
    </div>`;
}

/* ── Label Printing section ────────────────────────────────────── */
function buildLabelSection() {
  return `
    <div class="sp-editor-section" id="labelSection" style="display:none">
      <p class="sp-section-title" style="display:flex;align-items:center;gap:.6rem">
        <i class="fas fa-tag" style="color:var(--sp-primary)"></i>
        <span>Label Printing</span>
      </p>
      <div style="display:grid;grid-template-columns:1fr auto;gap:.75rem;align-items:flex-end">
        <div class="sp-field" style="margin:0">
          <label for="labelTemplate">Label Template</label>
          <div class="sp-input-wrap">
            <select id="labelTemplate" class="sp-input">
              <option value="">⏳ Loading templates…</option>
            </select>
          </div>
        </div>
        <label style="display:flex;align-items:center;gap:.4rem;font-size:.85rem;
                      padding-bottom:.3rem;cursor:pointer;white-space:nowrap">
          <input type="checkbox" id="printLabel" checked style="width:14px;height:14px">
          Print on receive
        </label>
      </div>
      <div id="labelStatus" style="margin-top:.4rem;font-size:.8rem;display:none"></div>
    </div>`;
}

function toggleReceiveStock(cb) {
  const fields = document.getElementById('receiveStockFields');
  const labelSection = document.getElementById('labelSection');
  if (cb.checked) {
    fields.style.display = '';
    if (labelSection) labelSection.style.display = '';
  } else {
    fields.style.display = 'none';
    if (labelSection) labelSection.style.display = 'none';
  }
}
