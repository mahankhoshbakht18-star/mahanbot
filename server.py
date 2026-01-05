import threading
import os
import json
import sqlite3
import time
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from database import DBHandler, resource_path

# ایمپورت ربات‌ها
from bot_register import RegistrationBot
from bot_select import BankSelectionBot
from bot_status import StatusBot

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DBHandler.init_db()
ACTIVE_BOTS = {}

@app.on_event("startup")
def startup_event():
    """پاکسازی وضعیت‌های گیر کرده هنگام شروع برنامه"""
    try:
        conn = sqlite3.connect(resource_path('cbi_ultimate.db'))
        c = conn.cursor()
        c.execute("UPDATE applicants SET status='Stopped' WHERE status IN ('Running', 'Selecting', 'Registering', 'Waiting SMS')")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error checking DB on startup: {e}")

# --- WebSocket ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try: await connection.send_text(message)
            except: pass
manager = ConnectionManager()

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

# --- Helper ---
def log_callback(nid, message, level="info"):
    DBHandler.update_status(nid, level.title(), message)
    payload = json.dumps({"type": "log", "nid": nid, "message": message, "level": level, "timestamp": time.strftime("%H:%M:%S")})
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(manager.broadcast(payload))
        loop.close()
    except: pass

def force_stop_bot(nid):
    """اگر رباتی با این کد ملی فعال است، آن را متوقف کن تا جدید اجرا شود"""
    if nid in ACTIVE_BOTS:
        try:
            # ارسال سیگنال توقف
            ACTIVE_BOTS[nid].set()
            # کمی صبر برای اینکه ترد قبلی بسته شود
            time.sleep(0.5)
            # اگر هنوز در لیست بود، دستی پاکش کن
            if nid in ACTIVE_BOTS:
                ACTIVE_BOTS.pop(nid, None)
        except:
            pass

# --- Threads ---
def run_register_thread(nid, settings, stop_event):
    try:
        bot = RegistrationBot(nid, settings, log_callback=log_callback)
        bot.run(stop_event)
    except Exception as e: log_callback(nid, f"خطا: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None) # استفاده از pop برای جلوگیری از خطا
        log_callback(nid, "ربات متوقف شد", "stopped")

def run_select_thread(nid, settings, loan_type, stop_event):
    try:
        bot = BankSelectionBot(nid, settings, log_callback=log_callback)
        bot.run(stop_event, loan_type)
    except Exception as e: log_callback(nid, f"خطا: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None)
        log_callback(nid, "ربات متوقف شد", "stopped")

def run_status_thread(nid, settings, stop_event):
    try:
        bot = StatusBot(nid, settings, log_callback=log_callback)
        bot.run(stop_event)
    except Exception as e:
        log_callback(nid, f"خطای وضعیت: {e}", "error")
    finally:
        ACTIVE_BOTS.pop(nid, None)
        log_callback(nid, "عملیات پایان یافت", "stopped")

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
def save_applicant(req: ApplicantModel):
    try:
        conn = sqlite3.connect(resource_path('cbi_ultimate.db'))
        c = conn.cursor()
        d_str = json.dumps(req.data)
        if req.id:
            c.execute("UPDATE applicants SET full_name=?, national_id=?, data=? WHERE id=?", (req.full_name, req.national_id, d_str, req.id))
        else:
            c.execute("INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)", (req.full_name, req.national_id, d_str))
        conn.commit()
        conn.close()
        return {"status": "ok"}
    except Exception as e: return {"status": "error", "msg": str(e)}

@app.delete("/applicants/{nid}")
def delete_applicant(nid: str):
    force_stop_bot(nid)
    conn = sqlite3.connect(resource_path('cbi_ultimate.db'))
    c = conn.cursor()
    c.execute("DELETE FROM applicants WHERE national_id=?", (nid,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.post("/receive_sms")
def rec_sms(req: SMSRequest):
    if DBHandler.save_otp(req.nid, req.code):
        log_callback(req.nid, f"پیامک: {req.code}", "success")
        return {"status": "ok"}
    return {"status": "error"}

@app.post("/bot/start-register/{nid}")
def start_reg(nid: str):
    # توقف اجباری نسخه قبلی اگر وجود دارد
    force_stop_bot(nid)
    
    settings = DBHandler.get_config()
    stop_event = threading.Event()
    ACTIVE_BOTS[nid] = stop_event
    threading.Thread(target=run_register_thread, args=(nid, settings, stop_event), daemon=True).start()
    return {"status": "started"}

@app.post("/bot/start-select")
def start_sel(req: BankSelectRequest):
    nid = req.nid
    force_stop_bot(nid)
    
    settings = DBHandler.get_config()
    stop_event = threading.Event()
    ACTIVE_BOTS[nid] = stop_event
    threading.Thread(target=run_select_thread, args=(nid, settings, req.loan_type, stop_event), daemon=True).start()
    return {"status": "started"}

@app.post("/bot/stop/{nid}")
def stop_bot(nid: str):
    if nid in ACTIVE_BOTS:
        ACTIVE_BOTS[nid].set()
        log_callback(nid, "توقف...", "stopping")
    else:
        DBHandler.update_status(nid, "Stopped", "Force Stop")
    return {"status": "stopped"}

@app.post("/bot/action/view-status/{nid}")
def action_view_status(nid: str):
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
    ACTIVE_BOTS[nid] = stop_event
    
    t = threading.Thread(target=run_status_thread, args=(nid, settings, stop_event), daemon=True)
    t.start()
    
    return {"status": "started", "message": "استعلام وضعیت آغاز شد"}

@app.post("/bot/action/delete-request/{nid}")
def action_delete_request(nid: str):
    log_callback(nid, "حذف درخواست (هنوز پیاده‌سازی نشده)", "warning")
    return {"status": "ok"}

@app.post("/bot/action/recover-code/{nid}")
def action_recover_code(nid: str):
    log_callback(nid, "بازیابی کد (هنوز پیاده‌سازی نشده)", "info")
    return {"status": "ok"}

# --- WebSocket Endpoint (اصلاح شده) ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # اجرا روی پورت 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)