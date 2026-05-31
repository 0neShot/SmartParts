/**
 * Smart Parts – PUI Panel
 *
 * Renders the Smart Parts MPN search UI directly inside a panel
 * on Part/Category pages. No iframe needed – all content is
 * rendered inline for full compatibility.
 */

export function renderSmartPartsPanel(target, context) {
    if (!target) {
        console.warn('SmartParts: Panel target is null, aborting render.');
        return;
    }

    const PLUGIN_API = '/plugin/smartparts/api/search/';
    const CREATE_API = '/plugin/smartparts/create/';
    const FULL_PAGE = '/plugin/smartparts/';

    target.innerHTML = `
        <div id="sp-root" style="font-family:system-ui,-apple-system,sans-serif;padding:16px;max-width:900px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
                <div style="display:flex;align-items:center;gap:10px;">
                    <span style="font-size:1.5rem;">⚡</span>
                    <div>
                        <strong style="font-size:1.1rem;">Smart Parts Lookup</strong>
                        <div style="font-size:.8rem;color:#6b7280;">Search Mouser, DigiKey, LCSC, element14 &amp; TME</div>
                    </div>
                </div>
                <a href="${FULL_PAGE}" target="_blank" 
                   style="font-size:.8rem;color:#6366f1;text-decoration:none;">
                    Open Full Dashboard ↗
                </a>
            </div>
            <div style="display:flex;gap:8px;margin-bottom:16px;">
                <input type="text" id="sp-mpn" placeholder="Enter MPN (e.g. LM7805, ATmega328P)..."
                    style="flex:1;padding:10px 14px;border:2px solid #e5e7eb;border-radius:8px;font-size:.95rem;outline:none;transition:border-color .2s;"
                    onfocus="this.style.borderColor='#6366f1'"
                    onblur="this.style.borderColor='#e5e7eb'"
                    onkeydown="if(event.key==='Enter')document.getElementById('sp-search-btn').click()"
                />
                <button id="sp-search-btn"
                    style="padding:10px 20px;background:#6366f1;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:.9rem;white-space:nowrap;"
                >
                    🔍 Search
                </button>
            </div>
            <div id="sp-status" style="display:none;padding:12px;border-radius:8px;margin-bottom:12px;font-size:.9rem;"></div>
            <div id="sp-results" style="display:none;"></div>
        </div>
    `;

    // Bind search
    const searchBtn = document.getElementById('sp-search-btn');
    const mpnInput = document.getElementById('sp-mpn');
    if (searchBtn && mpnInput) {
        searchBtn.addEventListener('click', function() {
            const mpn = mpnInput.value.trim();
            if (!mpn) return;
            doSearch(mpn);
        });
    } else {
        console.warn('SmartParts: Search elements not found in DOM.');
    }

    function getCsrf() {
        // Loop-based extraction avoids regex backslash-escaping pitfalls
        const name = 'csrftoken';
        for (const cookie of document.cookie.split(';')) {
            const [k, v] = cookie.trim().split('=');
            if (k === name) return decodeURIComponent(v || '');
        }
        return '';
    }

    function showStatus(msg, type) {
        const el = document.getElementById('sp-status');
        if (!el) return;
        el.style.display = 'block';
        el.style.background = type === 'error' ? '#fef2f2' : type === 'success' ? '#f0fdf4' : '#f0f4ff';
        el.style.color = type === 'error' ? '#dc2626' : type === 'success' ? '#16a34a' : '#2563eb';
        el.style.border = '1px solid ' + (type === 'error' ? '#fca5a5' : type === 'success' ? '#86efac' : '#93c5fd');
        el.innerHTML = msg;
    }

    function doSearch(mpn) {
        showStatus('⏳ Searching for <strong>' + mpn + '</strong> across all sources...', 'info');
        const resultsEl = document.getElementById('sp-results');
        if (resultsEl) resultsEl.style.display = 'none';

        const btn = document.getElementById('sp-search-btn');
        if (btn) btn.disabled = true;

        fetch(PLUGIN_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
            body: JSON.stringify({ mpn: mpn })
        })
        .then(r => {
            // Guard: reject non-JSON responses before parsing
            const ct = r.headers.get('content-type') || '';
            if (!r.ok || !ct.includes('application/json')) {
                return r.text().then(text => {
                    const hint =
                        r.status === 403 ? ' (CSRF / permission error)' :
                        r.status === 500 ? ' (server error – check logs)' :
                        r.status === 404 ? ' (endpoint not found)' : '';
                    throw new Error('HTTP ' + r.status + hint);
                });
            }
            return r.json();
        })
        .then(data => renderResults(data))
        .catch(err => showStatus('❌ Search failed: ' + err.message, 'error'))
        .finally(() => { if (btn) btn.disabled = false; });
    }

    function renderResults(data) {
        const container = document.getElementById('sp-results');
        if (!container) return;
        container.style.display = 'block';

        if (!data.merged) {
            showStatus('⚠️ No results found for <strong>' + (data.mpn || '') + '</strong>', 'error');
            container.innerHTML = '';
            return;
        }

        showStatus('✅ Found data from ' + Object.keys(data.sources || {}).filter(k => data.sources[k] && !data.sources[k].error).length + ' source(s)', 'success');

        const m = data.merged;
        let sourceBadges = '';
        ['mouser','digikey','lcsc','element14','tme'].forEach(s => {
            if (data.sources && data.sources[s] && !data.sources[s].error) {
                sourceBadges += '<span style="padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:600;background:#eff6ff;color:#2563eb;margin-right:4px;">' + s + ' ✓</span>';
            }
        });

        let paramsHtml = '';
        if (m.parameters && m.parameters.length > 0) {
            paramsHtml = '<div style="margin-top:12px;"><strong style="font-size:.85rem;">Parameters</strong>';
            paramsHtml += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;margin-top:6px;font-size:.8rem;">';
            m.parameters.slice(0, 12).forEach(p => {
                paramsHtml += '<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #f3f4f6;"><span style="color:#6b7280;">' + p.name + '</span><span><strong>' + p.value + '</strong> ' + (p.unit || '') + '</span></div>';
            });
            paramsHtml += '</div>';
            if (m.parameters.length > 12) paramsHtml += '<span style="font-size:.75rem;color:#9ca3af;">+ ' + (m.parameters.length - 12) + ' more</span>';
            paramsHtml += '</div>';
        }

        container.innerHTML = '' +
            '<div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px;background:#fff;">' +
                '<div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;">' +
                    '<div>' +
                        '<div style="font-size:1.1rem;font-weight:700;">' + (m.name || m.mpn) + '</div>' +
                        '<div style="font-size:.85rem;color:#6b7280;">' + (m.manufacturer || '') + ' · ' + (m.mpn || '') + '</div>' +
                    '</div>' +
                    '<div>' + sourceBadges + '</div>' +
                '</div>' +
                '<div style="font-size:.9rem;color:#374151;margin-bottom:8px;">' + (m.description || '') + '</div>' +
                '<div style="display:flex;gap:16px;font-size:.85rem;color:#6b7280;flex-wrap:wrap;">' +
                    (m.package ? '<span>📦 ' + m.package + '</span>' : '') +
                    (m.datasheet_url ? '<a href="' + m.datasheet_url + '" target="_blank" style="color:#6366f1;text-decoration:none;">📄 Datasheet</a>' : '') +
                '</div>' +
                paramsHtml +
                '<div style="margin-top:16px;display:flex;gap:8px;">' +
                    '<a href="' + FULL_PAGE + '" target="_blank" style="padding:8px 16px;background:#6366f1;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.85rem;text-decoration:none;display:inline-flex;align-items:center;gap:4px;">Open in Dashboard ↗</a>' +
                '</div>' +
            '</div>';
    }
}
