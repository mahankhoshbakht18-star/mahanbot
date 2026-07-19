from __future__ import annotations

import re
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


STYLE_TAG = '<link rel="stylesheet" href="/static/ui_v2.css?v=20260718" />'
SCRIPT_TAG = '<script src="/static/ui_v2.js?v=20260718" defer></script>'
MODEL_STYLE_TAG = '<link rel="stylesheet" href="/static/model_lab.css?v=20260719" />'
MODEL_SCRIPT_TAG = '<script src="/static/model_lab.js?v=20260719" defer></script>'
ACCESSIBILITY_STYLE_TAG = '<link rel="stylesheet" href="/static/accessibility_v1.css?v=20260719" />'
ACCESSIBILITY_SCRIPT_TAG = '<script src="/static/accessibility_v1.js?v=20260719" defer></script>'
BATCH_STYLE_TAG = '<link rel="stylesheet" href="/static/batch_automation.css?v=20260719" />'
BATCH_SCRIPT_TAG = '<script src="/static/batch_automation.js?v=20260719" defer></script>'


def _modernize_html(text: str) -> str:
    # The dashboard now uses a local/system font stack. Removing this remote font
    # saves a blocking request and keeps first paint fast on restricted networks.
    text = re.sub(
        r'<link[^>]+rastikerdar[^>]+Vazirmatn[^>]*>\s*',
        '',
        text,
        flags=re.IGNORECASE,
    )

    # Remove the legacy inline dashboard theme. All visual rules live in ui_v2.css.
    text = re.sub(
        r'<style>\s*/\*\s*استایل[^<]*داشبورد\s*\*/.*?</style>\s*',
        '',
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if '<body class="mahan-ui-v2">' not in text:
        text = text.replace('<body>', '<body class="mahan-ui-v2">', 1)
    for tag in (STYLE_TAG, MODEL_STYLE_TAG, ACCESSIBILITY_STYLE_TAG, BATCH_STYLE_TAG):
        if tag not in text:
            text = text.replace('</head>', f'    {tag}\n</head>', 1)
    for tag in (SCRIPT_TAG, MODEL_SCRIPT_TAG, ACCESSIBILITY_SCRIPT_TAG, BATCH_SCRIPT_TAG):
        if tag not in text:
            text = text.replace('</body>', f'    {tag}\n</body>', 1)
    return text


def install_ui_v2(app: FastAPI) -> None:
    if getattr(app.state, 'mahan_ui_v2_installed', False):
        return
    app.state.mahan_ui_v2_installed = True

    @app.middleware('http')
    async def inject_modern_dashboard(request: Request, call_next: Callable):
        response = await call_next(request)
        if request.method != 'GET' or request.url.path != '/':
            return response
        if response.status_code != 200:
            return response
        if 'text/html' not in str(response.headers.get('content-type') or '').lower():
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk
        text = _modernize_html(body.decode('utf-8', errors='replace'))

        headers = dict(response.headers)
        headers.pop('content-length', None)
        headers.pop('content-type', None)
        headers['cache-control'] = 'no-store, max-age=0'
        return HTMLResponse(content=text, status_code=response.status_code, headers=headers)
