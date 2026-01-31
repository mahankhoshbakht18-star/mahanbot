import threading
import os
import time
import json
import asyncio
import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, Optional
from database import DBHandler, resource_path
from event_logger import (
    EVENT_BROADCASTER,
    build_log_callback,
    create_job_id,
    job_status_event,
    log_event,
    set_main_loop,
)

# ایمپورت ربات‌ها
from bot_register import RegistrationBot
from bot_select import BankSelectionBot
from bot_status import StatusBot
from captcha_service import CaptchaService, load_captcha_resources

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict origins here when you know the allowed domains.
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DBHandler.init_db()
ACTIVE_BOTS: Dict[str, Dict[str, Any]] = {}
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
CAPTCHA_SERVICE: Optional[CaptchaService] = None

def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-KEY")):
    expected_key = os.getenv("MAHANBOT_API_KEY")
    if not expected_key:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="API key not configured")
    if x_api_key != expected_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return True

@app.on_event("startup")
async def startup_event():
    """پاکسازی وضعیت‌های گیر کرده هنگام شروع برنامه"""
    global MAIN_LOOP, CAPTCHA_SERVICE
    MAIN_LOOP = asyncio.get_running_loop()
    set_main_loop(MAIN_LOOP)
    model, ocr_firewall = load_captcha_resources()
    CAPTCHA_SERVICE = CaptchaService(model=model, ocr_firewall=ocr_firewall)
    try:
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            await conn.execute(
                "UPDATE applicants SET status='Stopped' WHERE status IN ('Running', 'Selecting', 'Registering', 'Waiting SMS')"
            )
            await conn.commit()
    except Exception as e:
        print(f"Error checking DB on startup: {e}")

# --- Models ---
class ApplicantModel(BaseModel):
    id: Optional[int] = None
    full_name: str
    national_id: str
    data: Dict[str, Any]

class BankSelectRequest(BaseModel):
    nid: str
    loan_type: str 

class SMSRequest(BaseModel):
    nid: str
    code: str

def force_stop_bot(nid):
    """اگر رباتی با این کد ملی فعال است، آن را متوقف کن تا جدید اجرا شود"""
    if nid in ACTIVE_BOTS:
        try:
            # ارسال سیگنال توقف
            ACTIVE_BOTS[nid]["stop_event"].set()
            # کمی صبر برای اینکه ترد قبلی بسته شود
            time.sleep(0.5)
            # اگر هنوز در لیست بود، دستی پاکش کن
            bot_info = ACTIVE_BOTS.pop(nid, None)
            if bot_info:
                job_status_event(nid, bot_info.get("job_id"), "stopped", "force_stop")
        except:
            pass

def get_captcha_service():
    global CAPTCHA_SERVICE
    if CAPTCHA_SERVICE is None:
        CAPTCHA_SERVICE = CaptchaService()
    return CAPTCHA_SERVICE

# --- Threads ---
def run_register_thread(nid, settings, stop_event, captcha_service, job_id):
    try:
        log_callback = build_log_callback(job_id)
        bot = RegistrationBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        job_status_event(nid, job_id, "running")
        bot.run(stop_event)
    except Exception as e:
        log_event(nid, job_id, f"خطا: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None) # استفاده از pop برای جلوگیری از خطا
        log_event(nid, job_id, "ربات متوقف شد", "stopped")
        job_status_event(nid, job_id, "stopped")

def run_select_thread(nid, settings, loan_type, stop_event, captcha_service, job_id):
    try:
        log_callback = build_log_callback(job_id)
        bot = BankSelectionBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        job_status_event(nid, job_id, "running")
        bot.run(stop_event, loan_type)
    except Exception as e:
        log_event(nid, job_id, f"خطا: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None)
        log_event(nid, job_id, "ربات متوقف شد", "stopped")
        job_status_event(nid, job_id, "stopped")

def run_status_thread(nid, settings, stop_event, captcha_service, job_id):
    try:
        log_callback = build_log_callback(job_id)
        bot = StatusBot(nid, settings, log_callback=log_callback, captcha_service=captcha_service)
        job_status_event(nid, job_id, "running")
        bot.run(stop_event)
    except Exception as e:
        log_event(nid, job_id, f"خطای وضعیت: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None)
        log_event(nid, job_id, "عملیات پایان یافت", "stopped")
        job_status_event(nid, job_id, "stopped")

# --- Routes ---
@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    return HTMLResponse("Error: index.html not found in static folder")

@app.get("/applicants")
def get_applicants():
    apps = DBHandler.get_all_applicants()
    for app_data in apps:
        app_data['is_active'] = (app_data['national_id'] in ACTIVE_BOTS)
    return apps

@app.post("/applicants")
async def save_applicant(req: ApplicantModel):
    try:
        d_str = json.dumps(req.data)
        async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
            if req.id:
                await conn.execute(
                    "UPDATE applicants SET full_name=?, national_id=?, data=? WHERE id=?",
                    (req.full_name, req.national_id, d_str, req.id),
                )
            else:
                await conn.execute(
                    "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
                    (req.full_name, req.national_id, d_str),
                )
            await conn.commit()
        return {"status": "ok"}
    except Exception as e: return {"status": "error", "msg": str(e)}

@app.delete("/applicants/{nid}")
async def delete_applicant(nid: str, _: bool = Depends(require_api_key)):
    force_stop_bot(nid)
    async with aiosqlite.connect(resource_path('cbi_ultimate.db')) as conn:
        await conn.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
        await conn.commit()
    return {"status": "deleted"}

@app.post("/receive_sms")
def rec_sms(req: SMSRequest):
    if DBHandler.save_otp(req.nid, req.code):
        log_event(req.nid, None, f"پیامک: {req.code}", "success")
        return {"status": "ok"}
    return {"status": "error"}

@app.post("/bot/start-register/{nid}")
def start_reg(nid: str, _: bool = Depends(require_api_key)):
    # توقف اجباری نسخه قبلی اگر وجود دارد
    force_stop_bot(nid)
    
    settings = DBHandler.get_config()
    stop_event = threading.Event()
    job_id = create_job_id()
    ACTIVE_BOTS[nid] = {"stop_event": stop_event, "job_id": job_id}
    captcha_service = get_captcha_service()
    job_status_event(nid, job_id, "started")
    threading.Thread(
        target=run_register_thread,
        args=(nid, settings, stop_event, captcha_service, job_id),
        daemon=True,
    ).start()
    return {"status": "started"}

@app.post("/bot/start-select")
def start_sel(req: BankSelectRequest, _: bool = Depends(require_api_key)):
    nid = req.nid
    force_stop_bot(nid)
    
    settings = DBHandler.get_config()
    stop_event = threading.Event()
    job_id = create_job_id()
    ACTIVE_BOTS[nid] = {"stop_event": stop_event, "job_id": job_id}
    captcha_service = get_captcha_service()
    job_status_event(nid, job_id, "started")
    threading.Thread(
        target=run_select_thread,
        args=(nid, settings, req.loan_type, stop_event, captcha_service, job_id),
        daemon=True,
    ).start()
    return {"status": "started"}

@app.post("/bot/stop/{nid}")
def stop_bot(nid: str, _: bool = Depends(require_api_key)):
    if nid in ACTIVE_BOTS:
        bot_info = ACTIVE_BOTS[nid]
        bot_info["stop_event"].set()
        log_event(nid, bot_info.get("job_id"), "توقف...", "stopping")
        job_status_event(nid, bot_info.get("job_id"), "stopping")
    else:
        DBHandler.update_status(nid, "Stopped", "Force Stop")
    return {"status": "stopped"}

@app.post("/bot/action/view-status/{nid}")
def action_view_status(nid: str, _: bool = Depends(require_api_key)):
    # این بخش تغییر کرد: به جای خطا دادن، قبلی را متوقف و جدید را شروع می‌کند
    force_stop_bot(nid)
    
    user = DBHandler.get_applicant(nid)
    if not user: return {"status": "error", "message": "کاربر یافت نشد"}
    
    try:
        d = json.loads(user['data'])
        if not d.get('tracking_code'):
            return {"status": "error", "message": "کد رهگیری ندارد"}
    except:
        return {"status": "error", "message": "داده نامعتبر"}

    settings = DBHandler.get_config()
    stop_event = threading.Event()
    job_id = create_job_id()
    ACTIVE_BOTS[nid] = {"stop_event": stop_event, "job_id": job_id}
    
    captcha_service = get_captcha_service()
    job_status_event(nid, job_id, "started")
    t = threading.Thread(
        target=run_status_thread, args=(nid, settings, stop_event, captcha_service, job_id), daemon=True
    )
    t.start()
    
    return {"status": "started", "message": "استعلام وضعیت آغاز شد"}

@app.post("/bot/action/delete-request/{nid}")
def action_delete_request(nid: str, _: bool = Depends(require_api_key)):
    log_event(nid, None, "حذف درخواست (هنوز پیاده‌سازی نشده)", "warning")
    return {"status": "ok"}

@app.post("/bot/action/recover-code/{nid}")
def action_recover_code(nid: str):
    log_event(nid, None, "بازیابی کد (هنوز پیاده‌سازی نشده)", "info")
    return {"status": "ok"}

# --- WebSocket Endpoint (اصلاح شده) ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await EVENT_BROADCASTER.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        EVENT_BROADCASTER.disconnect(websocket)
    except Exception:
        EVENT_BROADCASTER.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # اجرا روی پورت 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
