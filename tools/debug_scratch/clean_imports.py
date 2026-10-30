filepath = 'bot_select.py'
with open(filepath, 'r', encoding='utf-8', newline='') as f:
    content = f.read()

nl = '\r\n' if '\r\n' in content else '\n'

target = """from bot_select_otp import (
    get_otp_from_db_fresh,
    fast_otp_sync_and_inject,
    handle_otp_dialog,
    notify_favorite_banks,
    detect_otp_failure,
    otp_input_visible,
    resend_enabled,
    can_navigate_from_otp,
)
from bot_select_bank import (
    process_bank_selection_v2,
    return_to_bank_field,
    select_first_available_branch,
    process_branch_selection,
    wait_for_branch_fully_loaded,
    load_runtime_data,
)"""

replacement = """from bot_select_otp import (
    get_otp_from_db_fresh,
    fast_otp_sync_and_inject,
    detect_otp_failure,
    otp_input_visible,
    can_navigate_from_otp,
)"""

t_conv = target.replace('\r\n', '\n').replace('\n', nl)
r_conv = replacement.replace('\r\n', '\n').replace('\n', nl)

assert t_conv in content, 'Target not found in bot_select.py'
content = content.replace(t_conv, r_conv, 1)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content)
print("Cleaned unused imports from bot_select.py")
