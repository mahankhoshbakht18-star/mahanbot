"""
browser_fingerprint.py — Stealth & Anti-Detection Shield for MAHANBOT
Protects browser instances from bot detection, firewall fingerprinters,
and automated browser flags on ve.cbi.ir.
"""
from typing import Any, Dict, List

# Core JavaScript evasions injected into every frame before scripts load
STEALTH_EVASION_JS = """
(() => {
    // 1. Hide navigator.webdriver
    try {
        const newProto = Object.getPrototypeOf(navigator);
        delete newProto.webdriver;
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true,
            enumerable: true
        });
    } catch (e) {}

    // 2. Realistic Persian / Iranian language settings
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['fa-IR', 'fa', 'en-US', 'en'],
            configurable: true
        });
        Object.defineProperty(navigator, 'language', {
            get: () => 'fa-IR',
            configurable: true
        });
    } catch (e) {}

    // 3. Genuine window.chrome runtime mock
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        window.chrome.runtime = window.chrome.runtime || {
            PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
            PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
            connect: function() {},
            sendMessage: function() {}
        };
        window.chrome.loadTimes = window.chrome.loadTimes || function() {
            return {
                requestTime: (Date.now() - 500) / 1000,
                startLoadTime: (Date.now() - 400) / 1000,
                commitLoadTime: (Date.now() - 200) / 1000,
                finishDocumentLoadTime: Date.now() / 1000,
                finishLoadTime: Date.now() / 1000,
                firstPaintTime: (Date.now() - 100) / 1000,
                firstPaintAfterLoadTime: 0,
                navigationType: 'Other',
                wasFetchedViaSpdy: false,
                wasNpnNegotiated: false,
                npnNegotiatedProtocol: '',
                wasAlternateProtocolAvailable: false,
                connectionInfo: 'http/1.1'
            };
        };
        window.chrome.csi = window.chrome.csi || function() {
            return {
                startE: Date.now() - 500,
                onloadT: Date.now(),
                pageT: 500.0,
                tran: 15
            };
        };
        window.chrome.app = window.chrome.app || {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        };
    } catch (e) {}

    // 4. Genuine navigator.plugins & mimeTypes mock
    try {
        const fakePlugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = Object.create(PluginArray.prototype);
                fakePlugins.forEach((p, i) => {
                    const plugin = Object.create(Plugin.prototype);
                    Object.assign(plugin, p);
                    arr[i] = plugin;
                });
                arr.length = fakePlugins.length;
                return arr;
            },
            configurable: true
        });
    } catch (e) {}

    // 5. Mask WebGL vendor and renderer (spoof genuine hardware GPU)
    try {
        const getParameterProto = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) {
                return 'Google Inc. (Intel)';
            }
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) {
                return 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
            }
            return getParameterProto.apply(this, arguments);
        };

        if (typeof WebGL2RenderingContext !== 'undefined') {
            const getParameterProto2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) {
                    return 'Google Inc. (Intel)';
                }
                if (parameter === 37446) {
                    return 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
                }
                return getParameterProto2.apply(this, arguments);
            };
        }
    } catch (e) {}

    // 6. Permissions query mock
    try {
        if (navigator.permissions && navigator.permissions.query) {
            const origQuery = navigator.permissions.query;
            navigator.permissions.query = function(parameters) {
                if (parameters && parameters.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return origQuery.apply(this, arguments);
            };
        }
    } catch (e) {}
})();
"""


def get_stealth_args() -> List[str]:
    """Return Chromium CLI arguments that disable bot signatures and automation banners."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--lang=fa-IR,fa,en-US,en",
        "--disable-web-security=false",
    ]


get_stealth_browser_args = get_stealth_args


def get_stealth_scripts() -> List[str]:
    """Return all stealth evasion JavaScript snippets."""
    return [STEALTH_EVASION_JS]


def apply_stealth_shield(context: Any) -> None:
    """Inject anti-detection script into Playwright browser context."""
    if context is None:
        return
    try:
        context.add_init_script(STEALTH_EVASION_JS)
    except Exception:
        pass
