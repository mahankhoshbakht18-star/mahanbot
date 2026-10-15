"""One authorized navigation; no submissions, cookies or headers collected."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_launcher import launch_browser, close_browser, safe_goto
from network_diagnostics import NavigationEvidence, SiteRejectedError, rejection_info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--output", default="diagnostics/navigation.json")
    args = parser.parse_args()
    handles = launch_browser({"headless": False})
    page = handles[-1]
    evidence = NavigationEvidence(page)
    result = {"mode": "manual" if args.manual else "automatic", "profile": "fresh", "proxy": False}
    try:
        if args.manual:
            input("Open https://ve.cbi.ir/SelectBnkShb.aspx manually, then press Enter: ")
        else:
            try:
                safe_goto(page, "https://ve.cbi.ir/SelectBnkShb.aspx", wait_until="domcontentloaded", timeout=30000)
            except SiteRejectedError as exc:
                result.update(outcome="site_rejected", support_id=exc.support_id)
        if result.get("outcome") != "site_rejected":
            try:
                page.wait_for_selector("input[name$='tbIDNo'], #ans, #jar", timeout=10000)
            except Exception:
                pass
        body = page.inner_text("body", timeout=3000)
        result["rejection"] = rejection_info(body)
        if result["rejection"] is not None:
            result["outcome"] = "site_rejected"
            result["support_id"] = result["rejection"]["support_id"]
        result["challenge_input"] = page.locator("#ans, #jar").count() > 0
        result["challenge_markers"] = [marker for marker in ("human visitor", "در حال بررسی مرورگر شما", "cloudflare") if marker in body.lower()]
        result["entry_form"] = page.locator("input[name$='tbIDNo']").count() > 0
        result.setdefault("outcome", "form_loaded" if result["entry_form"] else "manual_challenge" if result["challenge_input"] or result["challenge_markers"] else "no_entry_form")
    except Exception as exc:
        result.update(outcome="navigation_error", error_type=type(exc).__name__)
    finally:
        result.update(evidence.snapshot(page))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        close_browser(*handles)


if __name__ == "__main__":
    main()
