with open('bot_select.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    # Remove unused bot_select_bank import
    if line.strip().startswith('from bot_select_bank import'):
        skip = True
        continue
    if skip and line.strip().endswith(')'):
        skip = False
        continue
    if skip:
        continue
    new_lines.append(line)

content = "".join(new_lines)

# Remove unused imports from bot_select_otp
content = content.replace("from bot_select_otp import handle_otp_dialog, notify_favorite_banks, resend_enabled\n", "")

dead_funcs = [
'''    def fetch_otp_fast(self, stop_event, timeout=120):
        return self._fetch_otp_fast(stop_event, timeout=timeout)
''',
'''    def _ensure_nid_after_reload(self, page):
        selectors = ["#ctl00_ContentPlaceHolder1_tbIDNo", "input[name$='tbIDNo']"]
        for selector in selectors:
            try:
                page.wait_for_selector(selector, timeout=3000, state="attached")
                page.fill(selector, self.nid)
                return True
            except Exception:
                continue
        return False
''',
'''    def _wait_for_state_any_scope(self, page, target_state, timeout=12000, interval=0.5):
        return wait_for_state_any_scope(self, page, target_state, timeout=timeout, interval=interval)
''',
'''    def _has_firewall_signal(self, page) -> bool:
        return has_firewall_signal(page)
''',
'''    def _process_bank_selection_v2(self, page, applicant_data, stop_event, session_controller=None):
        return process_bank_selection_v2(self, page, applicant_data, stop_event, session_controller=session_controller)
''',
'''    def _return_to_bank_field(self, page):
        return return_to_bank_field(self, page)
''',
'''    def _select_first_available_branch(self, page, session_controller=None):
        return select_first_available_branch(self, page, session_controller=session_controller)
''',
'''    def _process_branch_selection(self, page, applicant_data, stop_event, session_controller=None):
        return process_branch_selection(self, page, applicant_data, stop_event, session_controller=session_controller)
''',
'''    def _wait_for_branch_fully_loaded(self, page, timeout=15000):
        return wait_for_branch_fully_loaded(self, page, timeout=timeout)
''',
'''    def _load_runtime_data(self) -> dict:
        return load_runtime_data(self)
''',
'''    def _handle_otp_dialog(self, page):
        return handle_otp_dialog(self, page)
''',
'''    def _notify_favorite_banks(self, page):
        return notify_favorite_banks(self, page)
''',
'''    def _resend_enabled(self, page) -> bool:
        return resend_enabled(self, page)
'''
]

for df in dead_funcs:
    if df in content:
        content = content.replace(df, "")
    else:
        # try normalize newlines
        df_norm = df.replace("\r\n", "\n")
        content_norm = content.replace("\r\n", "\n")
        if df_norm in content_norm:
            content = content_norm.replace(df_norm, "")

with open('bot_select.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("CLEANED DEAD FUNCTIONS IN bot_select.py")
