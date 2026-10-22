with open('static/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

target = 'id="manualOtpInput"'
idx = text.find(target)
if idx != -1:
    end_tag = text.find('>', idx)
    if 'id="otpInput"' not in text:
        text = text[:end_tag+1] + '\n              <input type="hidden" id="otpInput">' + text[end_tag+1:]
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(text)
        print('SUCCESS: Added hidden otpInput to index.html')
    else:
        print('Already present')
else:
    print('manualOtpInput not found')
