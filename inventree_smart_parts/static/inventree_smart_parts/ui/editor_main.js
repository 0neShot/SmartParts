/* editor_main.js – Search flow, editor render, collect & save */

let _searchData   = null;
let _existingData = null;
let _suppliers    = [];
let _defaultQty   = 0;   // populated from BOM quantity or API data

// Scan data injected by the barcode scanner listener before search fires
let _pendingScan  = null;  // { quantity, batch, supplierSku, mpn, raw }

/* ── Search ────────────────────────────────────────────────────── */
function doSearch(e) {
  e.preventDefault();
  const mpn = document.getElementById('mpnInput').value.trim();
  if (!mpn) return false;

  const btn     = document.getElementById('searchBtn');
  const spinner = document.getElementById('searchSpinner');
  btn.disabled  = true;
  spinner.style.display = 'inline-flex';
  document.getElementById('searchResults').style.display = 'none';

  fetch('api/search/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
    body: JSON.stringify({ mpn }),
  })
  .then(r => r.json())
  .then(data => {
    btn.disabled = false;
    spinner.style.display = 'none';
    renderResults(data);
  })
  .catch(err => {
    btn.disabled = false;
    spinner.style.display = 'none';
    showError('Search failed: ' + err.message);
  });
  return false;
}

/* ── Results router ────────────────────────────────────────────── */
function renderResults(data) {
  _searchData  = data;
  _existingData = null;

  if (!data.merged) {
    document.getElementById('searchResults').style.display = 'block';
    document.getElementById('searchResults').innerHTML =
      `<div class="sp-card"><p style="color:var(--sp-danger)">
        <i class="fas fa-exclamation-triangle"></i>
        No results for <strong>${escHtml(data.mpn)}</strong>.
      </p></div>`;
    return;
  }

  if (data.duplicate) {
    fetch(`api/part/${data.duplicate.part_id}/`)
      .then(r => r.json())
      .then(existing => { _existingData = existing; renderEditor(data, existing); })
      .catch(()       => renderEditor(data, null));
  } else {
    renderEditor(data, null);
  }
}

/* ── Editor render ─────────────────────────────────────────────── */
function renderEditor(data, existing) {
  const m  = data.merged;
  const ex = existing;

  _suppliers = buildInitialSuppliers(data, ex);
  const paramRows = mergeParams(m.parameters || [], ex ? ex.parameters : []);

  /* ── Duplicate banner ─────────────────────────────────────── */
  let dupBanner = '';
  if (data.duplicate) {
    const d = data.duplicate;
    dupBanner = `
      <div class="sp-card" style="border-color:var(--sp-warning);background:rgba(245,158,11,.04);margin-bottom:1rem">
        <h3><i class="fas fa-exclamation-triangle" style="color:var(--sp-warning)"></i>
          Duplicate found &mdash; Update Mode
        </h3>
        <p style="margin:.25rem 0">
          <strong>${escHtml(d.part_name)}</strong> (ID: ${d.part_id}) already exists
          with MPN <code>${escHtml(d.existing_mpn)}</code>.
          Different fields are <span style="color:var(--sp-warning);font-weight:700">marked in orange</span>.
        </p>
      </div>`;
  }

  /* ── Source badges ────────────────────────────────────────── */
  let srcBadges = '';
  ['mouser','digikey','lcsc'].forEach(s => {
    const src = data.sources[s];
    if (!src) return;
    srcBadges += src.error
      ? `<span class="sp-badge sp-badge-error">${s}: ✗</span> `
      : `<span class="sp-badge sp-badge-new">${s}: ✓</span> `;
  });

  /* ── Image preview ────────────────────────────────────────── */
  const imgHtml = m.image_url
    ? `<div style="text-align:center">
         <img src="${escHtml(m.image_url)}" style="max-width:160px;max-height:160px;border-radius:8px;border:1px solid var(--sp-border)"
              onerror="this.style.display='none'">
       </div>`
    : '';

  /* ── Supplier cards ───────────────────────────────────────── */
  const supplierCards = _suppliers.map((s, i) => buildSupplierCard(s, i)).join('');

  /* ── Category ───────────────────────────────────────────── */
  const catId = data.category_match?.id || (ex?.category_id) || null;
  const catSelect = buildCategorySelect(data.categories, catId);
  const catBadge  = data.category_match?.id
    ? `<span class="sp-badge sp-badge-info" style="margin-bottom:.3rem;display:inline-block">
         Auto: ${escHtml(data.category_match.path)} (${data.category_match.score}%)
       </span><br>`
    : '';

  // Store for the save payload so the backend can learn from corrections
  window._editorDistributorCategory  = data.category_match?.distributor_category || '';
  window._editorSuggestedCategoryId  = data.category_match?.id || null;

  /* ── Assemble ─────────────────────────────────────────────── */
  const html = `
    ${dupBanner}
    <div class="sp-card">
      <h3>
        <i class="fas fa-edit" style="color:var(--sp-primary)"></i>
        Part Editor
        <span style="margin-left:auto;font-size:.8rem;font-weight:400">${srcBadges}</span>
      </h3>

      <!-- Identification -->
      <div class="sp-editor-section">
        <p class="sp-section-title">Identification</p>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 1.5rem">
          <div>
            ${buildField('edit-mpn',  'MPN',          m.mpn,          ex?.mpn,          'input')}
            ${buildField('edit-mfr',  'Manufacturer',   m.manufacturer, ex?.manufacturer, 'input')}
          </div>
          <div>${imgHtml}</div>
        </div>
      </div>

      <!-- Description -->
      <div class="sp-editor-section">
        <p class="sp-section-title">Description</p>
        ${buildField('edit-name', 'Name',        m.mpn,          ex?.name,        'input')}
        ${buildField('edit-desc', 'Description', m.description, ex?.description, 'textarea')}
      </div>

      <!-- Links -->
      <div class="sp-editor-section">
        <p class="sp-section-title">Links</p>
        ${buildField('edit-ds',  'Datasheet-URL', m.datasheet_url, ex?.link,      'input')}
        ${buildField('edit-img', 'Image-URL',      m.image_url,     ex?.image_url, 'input')}
      </div>

      <!-- Category -->
      <div class="sp-editor-section">
        <p class="sp-section-title">Category</p>
        ${catBadge}${catSelect}
      </div>

      <!-- Parameters -->
      <div class="sp-editor-section">
        <p class="sp-section-title">Parameters</p>
        ${buildParamsTable(paramRows)}
      </div>

      <!-- Suppliers -->
      <div class="sp-editor-section">
        <p class="sp-section-title" style="display:flex;justify-content:space-between">
          <span>Suppliers</span>
          <button type="button" class="sp-btn sp-btn-outline sp-btn-sm" onclick="addSupplier()">
            <i class="fas fa-plus"></i> Add Supplier
          </button>
        </p>
        <div id="supplierList">${supplierCards}</div>
      </div>

      <!-- Receive Stock -->
      ${buildReceiveStockSection(_defaultQty || 0)}

      <!-- Label Printing -->
      ${buildLabelSection()}

      <!-- Actions -->
      <div style="display:flex;gap:.75rem;justify-content:flex-end;padding-top:1rem;border-top:1px solid var(--sp-border)">
        <button type="button" class="sp-btn sp-btn-outline" onclick="cancelEditor()">
          <i class="fas fa-times"></i> Cancel
        </button>
        ${data.duplicate
          ? `<button type="button" class="sp-btn sp-btn-success" onclick="saveFromEditor(true,${data.duplicate.part_id})">
               <i class="fas fa-sync"></i> Update existing part
             </button>`
          : `<button type="button" class="sp-btn sp-btn-success" onclick="saveFromEditor(false,null)">
               <i class="fas fa-plus-circle"></i> Create new part
             </button>`
        }
      </div>
    </div>`;

  const container = document.getElementById('searchResults');
  container.style.display = 'block';
  container.innerHTML = html;

  // Kick off async data loading for the new stock/label dropdowns
  loadStockLocations();
  loadLabelTemplates();

  // Show label section if receive checkbox starts checked
  const enableCb = document.getElementById('enableReceive');
  if (enableCb && enableCb.checked) {
    const ls = document.getElementById('labelSection');
    if (ls) ls.style.display = '';
  }

  // ── Apply pending barcode-scan data ──────────────────────────────
  const scanData = _pendingScan || window._pendingScan || null;
  _pendingScan = null;
  if (window._pendingScan) window._pendingScan = null;

  if (scanData) {
    // Auto-enable the receive section
    const enableCb2 = document.getElementById('enableReceive');
    const fields2   = document.getElementById('receiveStockFields');
    if (enableCb2 && !enableCb2.checked) {
      enableCb2.checked = true;
      if (fields2) fields2.style.display = '';
      const ls2 = document.getElementById('labelSection');
      if (ls2) ls2.style.display = '';
    }

    // Pre-fill quantity (scan > BOM default)
    if (scanData.quantity !== null && scanData.quantity > 0) {
      const qEl = document.getElementById('stockQty');
      if (qEl) qEl.value = scanData.quantity;
    }

    // Pre-fill batch / lot code
    if (scanData.batch) {
      const bEl = document.getElementById('stockBatch');
      if (bEl && !bEl.value) bEl.value = scanData.batch;
    }
  }
}

/* ── Parameter row interactions ────────────────────────────────── */
function addParamRow() {
  const tbody = document.getElementById('paramTbody');
  if (!tbody) return;
  const row = { name:'', value:'', unit:'', status:'new', dbVal:null, manual:true };
  const tr = document.createElement('tbody');
  tr.innerHTML = buildParamRow(row);
  tbody.appendChild(tr.firstElementChild);
}

function delParamRow(btn) {
  btn.closest('tr').remove();
}

/* ── Supplier interactions ─────────────────────────────────────── */
function addSupplier() {
  const s = { supplier_name:'', supplier_sku:'', supplier_url:'', price_breaks:[] };
  _suppliers.push(s);
  const idx = _suppliers.length - 1;
  const div = document.createElement('div');
  div.innerHTML = buildSupplierCard(s, idx);
  document.getElementById('supplierList').appendChild(div.firstElementChild);
}

function delSupplier(btn) {
  btn.closest('.sp-supplier-card').remove();
}

function addPbRow(btn) {
  const tbody = btn.previousElementSibling.querySelector('tbody');
  if (!tbody) return;
  const tr = document.createElement('tbody');
  tr.innerHTML = buildPriceRow({ quantity:1, price:'', currency:'EUR' });
  tbody.appendChild(tr.firstElementChild);
}

function delPbRow(btn) {
  btn.closest('tr').remove();
}

/* ── Collect form data ─────────────────────────────────────────── */
function collectFormData() {
  const g = id => (document.getElementById(id)?.value ?? '').trim();

  /* Parameters */
  const params = [];
  document.querySelectorAll('#paramTbody tr').forEach(tr => {
    const inputs = tr.querySelectorAll('input');
    const name  = inputs[0]?.value.trim();
    const value = inputs[1]?.value.trim();
    const unit  = inputs[2]?.value.trim() || '';
    if (name && value) params.push({ name, value, unit });
  });

  /* Suppliers */
  const supplierData = [];
  document.querySelectorAll('.sp-supplier-card').forEach(card => {
    const inputs = card.querySelectorAll('input.sp-input');
    const name = inputs[0]?.value.trim();
    const sku  = inputs[1]?.value.trim();
    const url  = inputs[2]?.value.trim() || '';
    if (!name || !sku) return;
    const pbs = [];
    card.querySelectorAll('.sp-price-table tbody tr').forEach(tr => {
      const cells = tr.querySelectorAll('input');
      pbs.push({
        qty:      parseInt(cells[0]?.value) || 1,
        price:    parseFloat(cells[1]?.value) || 0,
        currency: cells[2]?.value.trim() || 'EUR',
      });
    });
    supplierData.push({ supplier_name: name, supplier_sku: sku, supplier_url: url, price_breaks: pbs });
  });

  // Per-source image URLs – ordered with most reliable first (digikey works server-side)
  const sourceImageUrls = [];
  ['digikey', 'lcsc', 'mouser'].forEach(src => {
    const s = _searchData?.sources?.[src];
    if (s && s.image_url && !s.error) {
      sourceImageUrls.push({ source: src, url: s.image_url });
    }
  });

  // Stock / label fields
  const enableReceive = document.getElementById('enableReceive')?.checked;
  const stockQty    = enableReceive ? (parseFloat(document.getElementById('stockQty')?.value) || 0) : 0;
  const stockLoc    = enableReceive ? (parseInt(document.getElementById('stockLocation')?.value) || null) : null;
  const stockBatch  = enableReceive ? (document.getElementById('stockBatch')?.value.trim() || '') : '';
  const stockDod    = enableReceive ? (document.getElementById('stockDeleteOnDeplete')?.checked ?? true) : true;
  const printLabel  = enableReceive && (document.getElementById('printLabel')?.checked ?? true);
  const labelTplId  = parseInt(document.getElementById('labelTemplate')?.value) || null;

  return {
    mpn:                    g('edit-mpn'),
    manufacturer:           g('edit-mfr'),
    name:                   g('edit-name'),
    description:            g('edit-desc'),
    package:                null,  // removed from UI - covered by Parameters section
    datasheet_url:          g('edit-ds'),
    image_url:              g('edit-img'),
    source_image_urls:      sourceImageUrls,
    category_id:            parseInt(document.getElementById('categorySelect')?.value) || null,
    // Category learning: tell the backend what the matcher originally suggested
    distributor_category:   window._editorDistributorCategory  || '',
    suggested_category_id:  window._editorSuggestedCategoryId  || null,
    parameters:             params,
    supplier_data:          supplierData,
    // Stock
    stock_quantity:         stockQty,
    stock_location_id:      stockLoc,
    stock_batch:            stockBatch,
    stock_delete_on_deplete: stockDod,
    // Label
    print_label:            printLabel,
    label_template_id:      labelTplId,
  };
}

/* ── Save ──────────────────────────────────────────────────────── */
function saveFromEditor(updateExisting, existingId) {
  const payload = collectFormData();
  payload.update_existing  = !!updateExisting;
  payload.existing_part_id = existingId || null;

  const saveBtns = document.querySelectorAll('.sp-btn-success');
  saveBtns.forEach(b => { b.disabled = true; b.innerHTML = '<span class="sp-loading"></span> Saving…'; });

  fetch('create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(res => {
    if (res.success) {
      // Build extra status lines for stock / label
      let extraLines = '';
      if (res.stock) {
        const ico = res.stock.success ? '📦' : '⚠️';
        extraLines += `<p style="font-size:.85rem;margin:.25rem 0">${ico} ${escHtml(res.stock.message)}</p>`;
      }
      if (res.label) {
        const ico = res.label.success ? '🏷️' : '⚠️';
        extraLines += `<p style="font-size:.85rem;margin:.25rem 0">${ico} ${escHtml(res.label.message)}</p>`;
      }

      document.getElementById('searchResults').innerHTML = `
        <div class="sp-card" style="border-color:var(--sp-success);background:rgba(16,185,129,.04)">
          <h3><i class="fas fa-check-circle" style="color:var(--sp-success)"></i>
            Part successfully ${res.action === 'created' ? 'created' : 'updated'}!
          </h3>
          <p>${escHtml(res.message)}</p>
          ${extraLines}
          <div style="display:flex;gap:.5rem;margin-top:.75rem">
            <a href="/part/${res.part_id}/" class="sp-btn sp-btn-primary" target="_blank">
              <i class="fas fa-eye"></i> View Part
            </a>
            ${res.stock?.stock_id
              ? `<a href="/stock/item/${res.stock.stock_id}/" class="sp-btn sp-btn-outline" target="_blank">
                   <i class="fas fa-boxes"></i> View Stock
                 </a>`
              : ''
            }
            <button type="button" class="sp-btn sp-btn-outline" onclick="cancelEditor()">
              <i class="fas fa-search"></i> New Search
            </button>
          </div>
        </div>`;
    } else {
      saveBtns.forEach(b => { b.disabled = false; b.innerHTML = '<i class="fas fa-save"></i> Save'; });
      showError('Saving failed: ' + res.message);
    }
  })
  .catch(err => {
    saveBtns.forEach(b => { b.disabled = false; });
    showError('Error: ' + err.message);
  });
}

/* ── Helpers ───────────────────────────────────────────────────── */
function cancelEditor() {
  document.getElementById('searchResults').style.display = 'none';
  document.getElementById('searchResults').innerHTML = '';
  _searchData = null; _existingData = null; _suppliers = []; _defaultQty = 0;
}

function showError(msg) {
  const c = document.getElementById('searchResults');
  c.style.display = 'block';
  c.innerHTML = `<div class="sp-card" style="border-color:var(--sp-danger)">
    <p style="color:var(--sp-danger)"><i class="fas fa-exclamation-triangle"></i> ${escHtml(msg)}</p>
  </div>`;
}

/* ── Auto-search from ?mpn= URL parameter ──────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  const params = new URLSearchParams(window.location.search);
  const mpn = params.get('mpn') || params.get('MPN') || '';
  if (mpn) {
    const input = document.getElementById('mpnInput');
    if (input) {
      input.value = mpn;
      // Small delay so the page fully renders before triggering search
      setTimeout(function() {
        const btn = document.getElementById('searchBtn');
        if (btn) btn.click();
      }, 300);
    }
  }
});

/* ── Stock locations loader ─────────────────────────────────────── */
function loadStockLocations() {
  const sel = document.getElementById('stockLocation');
  if (!sel) return;
  fetch('api/stock/locations/')                            // relative to /plugin/smartparts/
    .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
    .then(data => {
      let html = '<option value="">(No specific location)</option>';
      (data.locations || []).forEach(loc => {
        const desc = loc.description ? ` — ${loc.description}` : '';
        const label = escHtml(loc.name + desc);
        const title = loc.path ? escHtml(loc.path + (loc.description ? ` (${loc.description})` : '')) : label;
        html += `<option value="${loc.id}" title="${title}">${label}</option>`;
      });
      sel.innerHTML = html;
    })
    .catch(err => {
      console.warn('SmartParts: could not load stock locations:', err);
      sel.innerHTML = '<option value="">(Could not load – check console)</option>';
    });
}

/* ── Label templates loader ─────────────────────────────────────── */
function loadLabelTemplates() {
  const sel = document.getElementById('labelTemplate');
  if (!sel) return;
  fetch('api/label/templates/')                            // relative to /plugin/smartparts/
    .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
    .then(data => {
      if (!data.templates || !data.templates.length) {
        sel.innerHTML = '<option value="">(No label templates configured)</option>';
        return;
      }
      let html = '<option value="">(None)</option>';
      data.templates.forEach(t => {
        const selected = (t.id === data.default_id) ? 'selected' : '';
        html += `<option value="${t.id}" ${selected}>${escHtml(t.name)}</option>`;
      });
      sel.innerHTML = html;
      // Uncheck print if no usable template is selected
      const printCb = document.getElementById('printLabel');
      if (printCb && !data.templates.length) printCb.checked = false;
    })
    .catch(err => {
      console.warn('SmartParts: could not load label templates:', err);
      sel.innerHTML = '<option value="">(Could not load – check console)</option>';
    });
}
