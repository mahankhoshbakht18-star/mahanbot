import unittest

from ui_v2 import SCRIPT_TAG, STYLE_TAG, _modernize_html


class UiV2Tests(unittest.TestCase):
    def test_injects_local_assets_and_body_class(self):
        source = '<html><head></head><body><main>ok</main></body></html>'
        result = _modernize_html(source)
        self.assertIn(STYLE_TAG, result)
        self.assertIn(SCRIPT_TAG, result)
        self.assertIn('<body class="mahan-ui-v2">', result)

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
        self.assertEqual(twice.count(STYLE_TAG), 1)
        self.assertEqual(twice.count(SCRIPT_TAG), 1)


if __name__ == '__main__':
    unittest.main()
