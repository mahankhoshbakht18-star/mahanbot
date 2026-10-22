import os
import unittest


class SchemeAThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html_content = f.read()

        css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            cls.css_content = f.read()

        js_path = os.path.join(os.path.dirname(__file__), "..", "static", "script.js")
        with open(js_path, "r", encoding="utf-8") as f:
            cls.js_content = f.read()

    def test_scheme_a_tokens_defined(self):
        # Palette: Primary Turquoise #00BFB3, Deep Turquoise #009F98, Rich Gold #E5A900
        self.assertIn("--primary: #00BFB3;", self.css_content)
        self.assertIn("--primary-dark: #009F98;", self.css_content)
        self.assertIn("--gold: #E5A900;", self.css_content)
        self.assertIn("--card:", self.css_content)

    def test_persian_tooltips_styling_defined(self):
        self.assertIn("[data-tooltip]", self.css_content)
        self.assertIn("content: attr(data-tooltip);", self.css_content)

    def test_nine_sidebar_navigation_items_exist(self):
        self.assertIn("switchView('dashboard'", self.html_content)
        self.assertIn("switchView('list'", self.html_content)
        self.assertIn("switchView('add'", self.html_content)
        self.assertIn("switchView('bank-select'", self.html_content)
        self.assertIn("switchView('saved-pages'", self.html_content)
        self.assertIn("switchView('delete-req'", self.html_content)
        self.assertIn("switchView('status'", self.html_content)
        self.assertIn("switchView('settings'", self.html_content)
        self.assertIn("switchView('support'", self.html_content)

    def test_support_view_has_official_telegram_channels(self):
        self.assertIn("id=\"view-support\"", self.html_content)
        self.assertIn("https://t.me/mahanbot_ezdavaj", self.html_content)
        self.assertIn("https://t.me/mahan402", self.html_content)
        self.assertIn("https://t.me/dr_mahan22", self.html_content)
        self.assertIn("دانلود اپلیکیشن اندروید ماهان‌بات", self.html_content)

    def test_terminal_console_is_light_luxury(self):
        self.assertIn(".terminal-box {", self.css_content)
        self.assertIn("background: #f8fafc;", self.css_content)
        self.assertIn("color: #1e293b;", self.css_content)

    def test_all_six_dashboard_stats_exist(self):
        stat_ids = [
            "stat-total",
            "stat-active",
            "stat-success",
            "stat-codes",
            "stat-otp",
            "stat-bank",
        ]
        for sid in stat_ids:
            self.assertIn(f'id="{sid}"', self.html_content)

    def test_stage3_live_bank_status_card_exists(self):
        self.assertIn('id="stage3BankStatusCard"', self.html_content)
        self.assertIn('id="stage3BankStatusBody"', self.html_content)


if __name__ == "__main__":
    unittest.main()
