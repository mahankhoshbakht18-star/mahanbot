from __future__ import annotations

import re
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


STYLE_TAG = '<link rel="stylesheet" href="/static/ui_v2.css?v=20260720" />'
MODEL_STYLE_TAG = '<link rel="stylesheet" href="/static/model_lab.css?v=20260720" />'
SCRIPT_TAG = '<script src="/static/ui_v2.js?v=20260720" defer></script>'
RUNTIME_FIXES_SCRIPT_TAG = '<script src="/static/runtime_fixes.js?v=20260720" defer></script>'
ACTION_GUARD_SCRIPT_TAG = '<script src="/static/action_guard.js?v=20260720" defer></script>'
MODEL_SCRIPT_TAG = '<script src="/static/model_lab.js?v=20260720" defer></script>'


def _inject_before_closing(text: str, closing_tag: str, asset_tag: str) -> str:
    if asset_tag in text:
        return text
    match = re.search(re.escape(closing_tag), text, flags=re.IGNORECASE)
    if not match:
        return f"{text}\n{asset_tag}\n"
    return f"{text[:match.start()]}    {asset_tag}\n{text[match.start():]}"


def _add_body_class(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        class_match = re.search(r'\bclass\s*=\s*(["\'])(.*?)\1', attrs, flags=re.IGNORECASE | re.DOTALL)
        if class_match:
            classes = class_match.group(2).split()
            if "mahan-ui-v2" not in classes:
                classes.append("mahan-ui-v2")
            replacement = f'class={class_match.group(1)}{" ".join(classes)}{class_match.group(1)}'
            attrs = f"{attrs[:class_match.start()]}{replacement}{attrs[class_match.end():]}"
        else:
            attrs = f'{attrs} class="mahan-ui-v2"'
        return f"<body{attrs}>"

    return re.sub(r"<body([^>]*)>", replace, text, count=1, flags=re.IGNORECASE)


def _modernize_html(text: str) -> str:
    # Keep first paint independent from the remote Vazirmatn font. Bootstrap and
    # Font Awesome remain untouched because the legacy dashboard still uses them.
    text = re.sub(
        r'<link[^>]+rastikerdar[^>]+Vazirmatn[^>]*>\s*',
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Remove only the known legacy inline dashboard theme. Other inline styles
    # may contain layout rules required by the original page.
    text = re.sub(
        r'<style>\s*/\*\s*استایل[^<]*داشبورد\s*\*/.*?</style>\s*',
        "",
        text,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = _add_body_class(text)
    text = _inject_before_closing(text, "</head>", STYLE_TAG)
    text = _inject_before_closing(text, "</head>", MODEL_STYLE_TAG)
    text = _inject_before_closing(text, "</body>", SCRIPT_TAG)
    text = _inject_before_closing(text, "</body>", RUNTIME_FIXES_SCRIPT_TAG)
    text = _inject_before_closing(text, "</body>", ACTION_GUARD_SCRIPT_TAG)
    text = _inject_before_closing(text, "</body>", MODEL_SCRIPT_TAG)
    return text


def install_ui_v2(app: FastAPI) -> None:
    if getattr(app.state, "mahan_ui_v2_installed", False):
        return
    app.state.mahan_ui_v2_installed = True

    @app.middleware("http")
    async def inject_modern_dashboard(request: Request, call_next: Callable):
        response = await call_next(request)
        if request.method != "GET" or request.url.path != "/":
            return response
        if response.status_code != 200:
            return response
        if "text/html" not in str(response.headers.get("content-type") or "").lower():
            return response

        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(bytes(chunk))
        text = _modernize_html(b"".join(chunks).decode("utf-8", errors="replace"))

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("content-type", None)
        headers["cache-control"] = "no-store, max-age=0"
        headers["x-content-type-options"] = "nosniff"
        headers["referrer-policy"] = "no-referrer"
        return HTMLResponse(content=text, status_code=response.status_code, headers=headers)
