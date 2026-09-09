/**
 * Smart Parts – Dashboard Widget
 *
 * Quick-access widget on the InvenTree PUI dashboard home page.
 * Provides a mini MPN search form that redirects to the full plugin.
 */

import './scanner_global.js';

export function renderSmartPartsDashboard(target, context) {
    if (!target) return;

    target.innerHTML = `
        <div style="padding:16px;font-family:system-ui,-apple-system,sans-serif;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
                <span style="font-size:1.4rem;">🔍</span>
                <strong style="font-size:1rem;">Smart Parts Lookup</strong>
            </div>
            <p style="color:#6b7280;font-size:.85rem;margin:0 0 12px;">
                Search parts across Mouser, DigiKey, LCSC, element14 &amp; TME
            </p>
            <div style="display:flex;gap:8px;">
                <input 
                    type="text" 
                    id="sp-dash-mpn" 
                    placeholder="Enter MPN..." 
                    style="flex:1;padding:8px 12px;border:1px solid #d1d5db;border-radius:6px;font-size:.9rem;outline:none;"
                    onkeydown="if(event.key==='Enter'){document.getElementById('sp-dash-go').click()}"
                />
                <button 
                    id="sp-dash-go"
                    onclick="window.open('/plugin/smartparts/?mpn='+encodeURIComponent(document.getElementById('sp-dash-mpn').value),'_self')"
                    style="padding:8px 16px;background:#6366f1;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.85rem;"
                >
                    Search
                </button>
            </div>
            <div style="display:flex;gap:16px;margin-top:12px;align-items:center;">
                <a href="/plugin/smartparts/" 
                   style="font-size:.8rem;color:#6366f1;text-decoration:none;"
                   target="_self"
                >
                    Open Full Dashboard →
                </a>
                <a href="/plugin/smartparts/purescan/" 
                   style="font-size:.8rem;color:#f59e0b;text-decoration:none;font-weight:600;"
                   target="_blank"
                >
                    🎯 PureScan Terminal →
                </a>
            </div>
        </div>
    `;
}
