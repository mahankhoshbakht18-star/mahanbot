import sqlite3
import json
import os
import sys

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

DB_PATH = resource_path('cbi_ultimate.db')

class DBHandler:
    @staticmethod
    def init_db():
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS applicants 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                          full_name TEXT, 
                          national_id TEXT UNIQUE, 
                          status TEXT DEFAULT 'Ready', 
                          last_log TEXT DEFAULT '-', 
                          data TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS settings 
                         (key TEXT PRIMARY KEY, value TEXT)''')
            
            default_config = json.dumps({
                'captcha_delay': 0.1,
                'retry_count': 1000,
                'headless': False,
                'clear_cookies': True,
                'save_only_mode': False,
                'sms_auto_resend': True,
                'captcha_mode': 'human',
                'final_submit': False
            })
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('config', ?)", (default_config,))
            conn.commit()
            conn.close()
        except Exception as e: print(f"DB Init Error: {e}")

    @staticmethod
    def get_config():
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='config'")
            row = c.fetchone()
            conn.close()
            return json.loads(row[0]) if row else {}
        except: return {}

    @staticmethod
    def update_config(new_conf):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE settings SET value=? WHERE key='config'", (json.dumps(new_conf),))
            conn.commit()
            conn.close()
        except: pass

    @staticmethod
    def get_applicant(nid):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM applicants WHERE national_id=?", (nid,))
            res = c.fetchone()
            conn.close()
            return res
        except: return None

    @staticmethod
    def get_all_applicants():
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM applicants ORDER BY id DESC")
            rows = c.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                try: r['data'] = json.loads(r['data']) if r['data'] else {}
                except: r['data'] = {}
                results.append(r)
            conn.close()
            return results
        except: return []

    @staticmethod
    def update_status(nid, status, log):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE applicants SET status=?, last_log=? WHERE national_id=?", (status, log, nid))
            conn.commit()
            conn.close()
        except: pass

    @staticmethod
    def save_otp(nid, code):
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                d['otp_code'] = str(code).strip()
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d), nid))
                conn.commit()
                conn.close()
                return True
            except: pass
        return False

    @staticmethod
    def clear_otp(nid):
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                if 'otp_code' in d:
                    del d['otp_code']
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE applicants SET data=? WHERE national_id=?", (json.dumps(d), nid))
                    conn.commit()
                    conn.close()
            except: pass

    # --- تابع جدید برای ذخیره موفقیت ---
    @staticmethod
    def save_success_data(nid, tracking_code):
        """ذخیره کد رهگیری و تغییر وضعیت به موفق"""
        user = DBHandler.get_applicant(nid)
        if user:
            try:
                d = json.loads(user['data'])
                # ذخیره کد رهگیری
                d['tracking_code'] = str(tracking_code).strip()
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                # آپدیت دیتا و وضعیت همزمان
                c.execute("UPDATE applicants SET data=?, status='Success', last_log='ثبت نام موفق' WHERE national_id=?", (json.dumps(d), nid))
                conn.commit()
                conn.close()
                return True
            except Exception as e: print(f"Save Success Error: {e}")
        return False