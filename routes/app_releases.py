"""Admin-only facade; relay credentials are never exposed to browser JavaScript."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from core.deps import _admin_user

router = APIRouter()


@router.get("/admin/app-releases")
def releases_page(_=Depends(_admin_user)):
    return FileResponse(Path(__file__).resolve().parent.parent / "static" / "app-releases.html")


def allowed_path(path: str, method: str):
    import re
    return ((method == "GET" and path == "overview") or
            (method == "POST" and (path in {"upload", "releases"} or
             re.fullmatch(r"releases/[1-9][0-9]*/(?:approve|notify|stop)", path))))


@router.api_route("/api/app-admin/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request, _=Depends(_admin_user)):
    if not allowed_path(path, request.method):
        raise HTTPException(404)
    if request.method == "POST":
        if request.headers.get("X-MAHAN-ADMIN") != "1":
            raise HTTPException(403, "درخواست مدیریت معتبر نیست")
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "مبدأ درخواست مدیریت معتبر نیست")
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HTTPException(403)
    from relay_transport import config
    configured = config()
    if not configured:
        raise HTTPException(503, "اتصال مدیریتی به رله پیکربندی نشده است")
    base, headers = configured
    release_key = os.getenv("RELAY_RELEASE_ADMIN_KEY", "").strip()
    if not release_key:
        raise HTTPException(503, "کلید مستقل مدیریت نسخه در سرور تنظیم نشده است")
    headers = {**headers, "X-RELEASE-ADMIN-KEY": release_key}
    headers = {**headers, "Content-Type": request.headers.get("Content-Type", "application/json")}
    limit = 100 * 1024 * 1024 if path == "upload" else 32768
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > limit:
            raise HTTPException(413, "حجم درخواست بیش از حد مجاز است")
        content.extend(chunk)
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
            remote = await client.request(request.method, base + "/api/v1/bot/app/" + path, headers=headers, content=bytes(content))
            if remote.status_code >= 500:
                raise HTTPException(503, "سرویس مدیریت نسخه یا تأیید امضای APK آماده نیست")
            return Response(remote.content, status_code=remote.status_code, media_type="application/json",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except httpx.RequestError:
        raise HTTPException(503, "ارتباط با سرویس مدیریت نسخه برقرار نشد") from None
