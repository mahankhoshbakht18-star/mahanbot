with open('bank_session_controller.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = '''    def _on_response(self, response):
        if response.request.is_navigation_request() or response.request.method == "POST":
            if response.status == 401:
                self.bot._set_recovery_flag("session_expired")
            elif response.status in {403, 429}:
                self._blocked = True

    def _on_request_finished(self, request):
        if self._is_final_request(request):
            data = {}
            response = request.response()
            if response is not None and response.status == 200:
                try:
                    html = response.text()
                    data = analyze_bank_page_html(html)
                    data['reserve_confirmed'] = extract_confirmed_reservation(html=html)
                except Exception:
                    pass  # Missing response content is an unknown outcome, never success.
            self._requests.discard(id(request))
            self._response_serial += 1
            self._last_final_response = {'serial': self._response_serial, 'status': response.status if response else 0, 'data': data}'''

replacement = '''    def _on_response(self, response):
        try:
            req = response.request
            if req and (req.is_navigation_request() or req.method == "POST"):
                if response.status == 401:
                    self.bot._set_recovery_flag("session_expired")
                elif response.status in {403, 429}:
                    self._blocked = True
        except Exception:
            pass

    def _on_request_finished(self, request):
        if self._is_final_request(request):
            data = {}
            response = None
            try:
                response = request.response()
            except Exception:
                response = None
            if response is not None and getattr(response, 'status', 0) == 200:
                try:
                    html = response.text()
                    data = analyze_bank_page_html(html)
                    data['reserve_confirmed'] = extract_confirmed_reservation(html=html)
                except Exception:
                    pass  # Missing response content is an unknown outcome, never success.
            self._requests.discard(id(request))
            self._response_serial += 1
            status_val = getattr(response, 'status', 0) if response else 0
            self._last_final_response = {'serial': self._response_serial, 'status': status_val, 'data': data}'''

# Normalize newlines
text_norm = text.replace('\r\n', '\n')
target_norm = target.replace('\r\n', '\n')
repl_norm = replacement.replace('\r\n', '\n')

if target_norm in text_norm:
    text_norm = text_norm.replace(target_norm, repl_norm, 1)
    with open('bank_session_controller.py', 'w', encoding='utf-8') as f:
        f.write(text_norm)
    print("SUCCESS: Updated bank_session_controller.py safely.")
else:
    print("WARNING: target not found in bank_session_controller.py")
