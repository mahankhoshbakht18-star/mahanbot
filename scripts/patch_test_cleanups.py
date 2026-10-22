# 1. Update tests/test_critical_repair.py
with open('tests/test_critical_repair.py', 'r', encoding='utf-8') as f:
    text = f.read()

target1 = '''        self.stop = threading.Event()
        self.owner = LIVE_SESSIONS.acquire(self.nid, self.stop)
        self.bot.live_owner = self.owner

    def tearDown(self):
        if LIVE_SESSIONS.get(self.nid) is self.owner:
            LIVE_SESSIONS.release(self.owner)'''

replacement1 = '''        LIVE_SESSIONS.reset()
        self.stop = threading.Event()
        self.owner = LIVE_SESSIONS.acquire(self.nid, self.stop)
        self.bot.live_owner = self.owner

    def tearDown(self):
        try:
            if LIVE_SESSIONS.get(self.nid) is self.owner:
                LIVE_SESSIONS.release(self.owner)
        except Exception:
            pass
        LIVE_SESSIONS.reset()'''

text_norm = text.replace('\r\n', '\n')
t1_norm = target1.replace('\r\n', '\n')
r1_norm = replacement1.replace('\r\n', '\n')

if t1_norm in text_norm:
    text_norm = text_norm.replace(t1_norm, r1_norm, 1)
    with open('tests/test_critical_repair.py', 'w', encoding='utf-8') as f:
        f.write(text_norm)
    print("SUCCESS: Updated tests/test_critical_repair.py")
else:
    print("WARNING: target1 not found in tests/test_critical_repair.py")

# 2. Update tests/test_regressions.py MandatoryV180Tests
with open('tests/test_regressions.py', 'r', encoding='utf-8') as f:
    reg_text = f.read()

target2 = '''    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test_v180.db")
        DBHandler._settings_cache.clear()
        DBHandler.init_db()
        EVENT_BROADCASTER.event_history.clear()
        EVENT_BROADCASTER._last_log_by_nid.clear()'''

replacement2 = '''    def setUp(self):
        from live_sessions import LIVE_SESSIONS
        LIVE_SESSIONS.reset()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = os.path.join(self.temp_dir.name, "test_v180.db")
        DBHandler._settings_cache.clear()
        DBHandler.init_db()
        EVENT_BROADCASTER.event_history.clear()
        EVENT_BROADCASTER._last_log_by_nid.clear()'''

target3 = '''    def tearDown(self):
        database.DB_PATH = self.old_db_path
        DBHandler._settings_cache.clear()
        self.temp_dir.cleanup()'''

replacement3 = '''    def tearDown(self):
        from live_sessions import LIVE_SESSIONS
        LIVE_SESSIONS.reset()
        database.DB_PATH = self.old_db_path
        DBHandler._settings_cache.clear()
        self.temp_dir.cleanup()'''

reg_norm = reg_text.replace('\r\n', '\n')
t2_norm = target2.replace('\r\n', '\n')
r2_norm = replacement2.replace('\r\n', '\n')
t3_norm = target3.replace('\r\n', '\n')
r3_norm = replacement3.replace('\r\n', '\n')

if t2_norm in reg_norm and t3_norm in reg_norm:
    reg_norm = reg_norm.replace(t2_norm, r2_norm, 1)
    reg_norm = reg_norm.replace(t3_norm, r3_norm, 1)
    with open('tests/test_regressions.py', 'w', encoding='utf-8') as f:
        f.write(reg_norm)
    print("SUCCESS: Updated tests/test_regressions.py")
else:
    print("WARNING: target2 or target3 not found in tests/test_regressions.py")
