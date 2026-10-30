with open('core/state_machine.py', 'r', encoding='utf-8') as f:
    c = f.read()
target = '"current_step": norm_new_state,'
assert target in c, "target not found"
replacement = '"current_step": (DBHandler.get_runtime(clean_nid) or {}).get("current_step") if target_state in {BotState.FAILED, BotState.PAUSED} and (DBHandler.get_runtime(clean_nid) or {}).get("current_step") else norm_new_state,'
c = c.replace(target, replacement, 1)
with open('core/state_machine.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("PATCHED SUCCESS")
