import unittest

from ui_v2 import (
    ACTION_GUARD_SCRIPT_TAG,
    MODEL_SCRIPT_TAG,
    MODEL_STYLE_TAG,
    RUNTIME_FIXES_SCRIPT_TAG,
    SCRIPT_TAG,
    STYLE_TAG,
    _modernize_html,
)


class UiV2Tests(unittest.TestCase):
    def test_injects_all_local_assets_and_body_class(self):
        source = '<html><head></head><body><main>ok</main></body></html>'
        result = _modernize_html(source)
        self.assertIn(STYLE_TAG, result)
        self.assertIn(MODEL_STYLE_TAG, result)
        self.assertIn(SCRIPT_TAG, result)
        self.assertIn(RUNTIME_FIXES_SCRIPT_TAG, result)
        self.assertIn(ACTION_GUARD_SCRIPT_TAG, result)
        self.assertIn(MODEL_SCRIPT_TAG, result)
        self.assertIn('<body class="mahan-ui-v2">', result)
        self.assertLess(result.index(RUNTIME_FIXES_SCRIPT_TAG), result.index(ACTION_GUARD_SCRIPT_TAG))

    def test_preserves_existing_body_classes(self):
        source = '<html><head></head><body class="legacy compact"><main>ok</main></body></html>'
        result = _modernize_html(source)
        self.assertIn('class="legacy compact mahan-ui-v2"', result)

    def test_handles_uppercase_closing_tags(self):
        source = '<HTML><HEAD></HEAD><BODY></BODY></HTML>'
        result = _modernize_html(source)
        self.assertIn(STYLE_TAG, result)
        self.assertIn(RUNTIME_FIXES_SCRIPT_TAG, result)
        self.assertIn(ACTION_GUARD_SCRIPT_TAG, result)

    def test_removes_remote_vazirmatn_and_legacy_inline_theme(self):
        source = '''
        <html><head>
          <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33/Vazirmatn.css" rel="stylesheet">
          <style>/* استایل‌های اختصاصی داشبورد */ .stat-card { color:red; }</style>
        </head><body></body></html>
        '''
        result = _modernize_html(source)
        self.assertNotIn('rastikerdar', result)
        self.assertNotIn('color:red', result)

    def test_is_idempotent(self):
        source = '<html><head></head><body></body></html>'
        once = _modernize_html(source)
        twice = _modernize_html(once)
        for asset in (
            STYLE_TAG,
            MODEL_STYLE_TAG,
            SCRIPT_TAG,
            RUNTIME_FIXES_SCRIPT_TAG,
            ACTION_GUARD_SCRIPT_TAG,
            MODEL_SCRIPT_TAG,
        ):
            self.assertEqual(twice.count(asset), 1)
        self.assertEqual(twice.count('mahan-ui-v2'), 1)


if __name__ == '__main__':
    unittest.main()
