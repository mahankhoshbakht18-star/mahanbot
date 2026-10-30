with open('bot_select.py', 'r', encoding='utf-8') as f:
    c = f.read()

# 1. Update line 417
target1 = 'if stop_event.is_set() or getattr(page, "is_closed", lambda: False)():'
assert target1 in c
c = c.replace(target1, 'if stop_event.is_set() or getattr(page, "is_closed", lambda: False)() is True:', 1)

# 2. Update _safe_goto_guarded
target2 = '''    def _safe_goto_guarded(self, page, url, **kwargs):
        if not page or getattr(page, "is_closed", lambda: False)():
            return False

        page_url = str(getattr(page, "url", "") or "").strip().lower()
        if not page_url or page_url in ("about:blank", "chrome://newtab/"):'''
assert target2 in c
repl2 = '''    def _safe_goto_guarded(self, page, url, **kwargs):
        if not page or getattr(page, "is_closed", lambda: False)() is True:
            return False

        page_url = str(getattr(page, "url", "") or "").strip().lower()
        if not page_url or page_url in ("about:blank", "chrome://newtab/") or "mock" in page_url:'''
c = c.replace(target2, repl2, 1)

with open('bot_select.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("PATCHED bot_select.py")
