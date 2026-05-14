/**
 * Smart Parts – PUI Navigation Redirect
 *
 * The InvenTree PUI uses React Router (SPA navigation) for tab clicks.
 * Since our plugin pages are Django-rendered (not React routes), we need
 * to intercept the SPA navigation and redirect to a full page load.
 *
 * This script watches for URL changes to '/smartparts' and redirects
 * to the actual Django-served plugin page.
 */

// Intercept navigation to smartparts route
(function() {
    const PLUGIN_URL = '/plugin/smartparts/';
    const ROUTE_MARKER = 'smartparts';

    // Watch for popstate and pushstate to detect SPA navigation
    const originalPushState = history.pushState;
    history.pushState = function() {
        originalPushState.apply(this, arguments);
        checkAndRedirect();
    };

    const originalReplaceState = history.replaceState;
    history.replaceState = function() {
        originalReplaceState.apply(this, arguments);
        checkAndRedirect();
    };

    window.addEventListener('popstate', checkAndRedirect);

    function checkAndRedirect() {
        const path = window.location.pathname;
        if (path.endsWith('/' + ROUTE_MARKER) || path.endsWith('/' + ROUTE_MARKER + '/')) {
            window.location.href = PLUGIN_URL;
        }
    }

    // Initial check
    checkAndRedirect();
})();

// Export the render function (required by UserInterfaceMixin source field)
export function renderSmartParts(context) {
    // If this function is actually called for rendering, redirect
    window.location.href = '/plugin/smartparts/';
    return null;
}
