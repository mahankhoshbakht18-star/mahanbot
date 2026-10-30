filepath = 'bot_core.py'
with open(filepath, 'r', encoding='utf-8', newline='') as f:
    content = f.read()

nl = '\r\n' if '\r\n' in content else '\n'

t1 = """    def log_exception(self, exc, page=None, meta=None):
        \"\"\"Logs an exception with full traceback and context.\"\"\"
        details = {
            \"error\": sanitize_log_text(str(exc)),
            \"traceback\": sanitize_log_text(traceback.format_exc()),
            \"url\": page.url if page else \"unknown\"
        }
        if meta:
            details.update(meta)
        self.log(f\"Exception occurred: {sanitize_log_text(str(exc))}\", \"error\", page, meta=details)

"""

t2 = """    def wait_for_manual_captcha(self, page, input_selector, button_selector, stop_event, source="general"):
        \"\"\"Compatibility alias for code migrated from the former manual flow.\"\"\"
        return self.solve_captcha_with_model(
            page,
            input_selector,
            button_selector,
            stop_event=stop_event,
            source=source,
        )

"""

for idx, t in enumerate([t1, t2], 1):
    t_conv = t.replace('\r\n', '\n').replace('\n', nl)
    if t_conv in content:
        content = content.replace(t_conv, '')
        print(f"Target {idx} removed successfully.")
    else:
        print(f"Target {idx} not found directly, trying normalized...")
        t_conv2 = t.replace('\r\n', '\n')
        content_norm = content.replace('\r\n', '\n')
        if t_conv2 in content_norm:
            content_norm = content_norm.replace(t_conv2, '')
            content = content_norm.replace('\n', nl)
            print(f"Target {idx} removed successfully with normalization.")
        else:
            print(f"Target {idx} NOT FOUND!")

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content)
print("Finished cleaning bot_core.py")
