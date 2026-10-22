with open('tests/test_final_submission_policy.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace self.page.wait_for_function('() => true') in those two tests
text_new = text.replace("held[0].fulfill(content_type='text/html', body=f'<span id=\"ctl00_ContentPlaceHolder1_labErrMsg\">{FULL}</span>')\n        self.page.wait_for_function('() => true')",
                        "held[0].fulfill(content_type='text/html', body=f'<span id=\"ctl00_ContentPlaceHolder1_labErrMsg\">{FULL}</span>')\n        self.page.wait_for_timeout(100)")

text_new = text_new.replace("held[1].fulfill(content_type='text/html', body='<span id=\"ctl00_ContentPlaceHolder1_lblMessage\">درخواست رزرو شما با موفقیت ثبت شد</span>')\n        self.page.wait_for_function('() => true')",
                            "held[1].fulfill(content_type='text/html', body='<span id=\"ctl00_ContentPlaceHolder1_lblMessage\">درخواست رزرو شما با موفقیت ثبت شد</span>')\n        self.page.wait_for_timeout(100)")

text_new = text_new.replace("held[0].fulfill(content_type='text/html', body='<span id=\"ctl00_ContentPlaceHolder1_lblTrackingCode\">123456789012</span>')\n        self.page.wait_for_function('() => true')",
                            "held[0].fulfill(content_type='text/html', body='<span id=\"ctl00_ContentPlaceHolder1_lblTrackingCode\">123456789012</span>')\n        self.page.wait_for_timeout(100)")

with open('tests/test_final_submission_policy.py', 'w', encoding='utf-8') as f:
    f.write(text_new)

print('SUCCESS: Updated test_final_submission_policy.py timeouts.')
