with open('static/script.js', 'r', encoding='utf-8') as f:
    text = f.read()

target = """    if (previous !== manualOtpTargetNid) {
        document.getElementById('manualOtpInput').value = '';
        document.getElementById('otpInput').value = '';
        document.getElementById('manualOtpStatus').textContent = '';
    }
    const send = document.getElementById('manualOtpSend');
    if (send) send.disabled = !manualOtpTargetNid || manualOtpSending;
    select.disabled = manualOtpSending;
    document.getElementById('manualOtpInput').disabled = manualOtpSending;
    document.getElementById('otpInput').disabled = manualOtpSending;
}

function selectOtpRecipient(nid) {
    manualOtpTargetNid = nid || null;
    document.getElementById('manualOtpInput').value = '';
    document.getElementById('otpInput').value = '';"""

replacement = """    if (previous !== manualOtpTargetNid) {
        const mInput = document.getElementById('manualOtpInput');
        if (mInput) mInput.value = '';
        const oInput = document.getElementById('otpInput');
        if (oInput) oInput.value = '';
        const mStatus = document.getElementById('manualOtpStatus');
        if (mStatus) mStatus.textContent = '';
    }
    const send = document.getElementById('manualOtpSend');
    if (send) send.disabled = !manualOtpTargetNid || manualOtpSending;
    select.disabled = manualOtpSending;
    const mInput = document.getElementById('manualOtpInput');
    if (mInput) mInput.disabled = manualOtpSending;
    const oInput = document.getElementById('otpInput');
    if (oInput) oInput.disabled = manualOtpSending;
}

function selectOtpRecipient(nid) {
    manualOtpTargetNid = nid || null;
    const mInput = document.getElementById('manualOtpInput');
    if (mInput) mInput.value = '';
    const oInput = document.getElementById('otpInput');
    if (oInput) oInput.value = '';"""

if target in text:
    text = text.replace(target, replacement, 1)
    with open('static/script.js', 'w', encoding='utf-8') as f:
        f.write(text)
    print("SUCCESS: Guarded otpInput in static/script.js")
else:
    print("WARNING: Target not found in static/script.js")
