from database import DBHandler


def firewall_gate(bot, page, stop_event) -> bool:
    if stop_event.is_set():
        return True
    if not bot.is_firewall_challenge(page):
        return False
    DBHandler.update_status(bot.nid, "WAITING_USER_INPUT", "Manual firewall CAPTCHA entry required")
    resumed = bot.handle_firewall_challenge(page, stop_event)
    if stop_event.is_set():
        return True
    if not resumed:
        return True
    DBHandler.update_status(bot.nid, "Ready", "Firewall challenge cleared")
    return False


def firewall_input_visible(page) -> bool:
    try:
        ans = page.locator("#ans")
        return ans.count() > 0 and ans.first.is_visible()
    except Exception:
        return False
