"""
Django email backend that sends through the Gmail API over HTTPS.

Render's free web services block outbound SMTP (ports 25/465/587), so Django's
normal SMTP backend can't deliver there. The Gmail API is plain HTTPS on port
443, which is allowed. Mail goes out from the Google account that authorised
the app, using an OAuth refresh token.

Settings (all read from the environment in settings.py):
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN

Run scripts/gmail_authorize.py once, locally, to obtain the refresh token.
"""
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'

# Access tokens last about an hour; keep one per process and refresh a little
# early rather than exchanging the refresh token on every email.
_token_lock = threading.Lock()
_token_cache = {'value': None, 'expires_at': 0.0}


class GmailSendError(Exception):
    pass


def _post(url, data, headers, timeout):
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'{}')
        except ValueError:
            body = {}
        return exc.code, body


def _access_token(timeout, force_refresh=False):
    with _token_lock:
        if not force_refresh and _token_cache['value'] and time.time() < _token_cache['expires_at'] - 60:
            return _token_cache['value']

        missing = [
            name for name in ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REFRESH_TOKEN')
            if not getattr(settings, name, '')
        ]
        if missing:
            raise GmailSendError(f"Gmail API email backend is missing settings: {', '.join(missing)}")

        body = urllib.parse.urlencode({
            'client_id': settings.GMAIL_CLIENT_ID,
            'client_secret': settings.GMAIL_CLIENT_SECRET,
            'refresh_token': settings.GMAIL_REFRESH_TOKEN,
            'grant_type': 'refresh_token',
        }).encode()
        status, payload = _post(
            TOKEN_URL, body, {'Content-Type': 'application/x-www-form-urlencoded'}, timeout,
        )
        if status != 200 or 'access_token' not in payload:
            hint = ' (the refresh token was revoked or expired; run gmail_authorize.py again)' \
                if payload.get('error') == 'invalid_grant' else ''
            raise GmailSendError(
                f"Google rejected the refresh token exchange: {status} {payload.get('error', '')}{hint}"
            )

        _token_cache['value'] = payload['access_token']
        _token_cache['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
        return _token_cache['value']


class GmailAPIEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        timeout = getattr(settings, 'EMAIL_TIMEOUT', 10)
        for message in email_messages:
            try:
                self._send_one(message, timeout)
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception('Gmail API send failed')
            else:
                sent += 1
        return sent

    def _send_one(self, message, timeout):
        raw = base64.urlsafe_b64encode(message.message().as_bytes(linesep='\r\n')).decode()
        payload = json.dumps({'raw': raw}).encode()

        for attempt in (1, 2):
            token = _access_token(timeout, force_refresh=(attempt == 2))
            status, body = _post(
                SEND_URL,
                payload,
                {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                timeout,
            )
            if status == 200:
                return
            # A 401 means the cached access token went stale; refresh once and retry.
            if status == 401 and attempt == 1:
                continue
            error = body.get('error', {})
            detail = error.get('message', '') if isinstance(error, dict) else str(error)
            raise GmailSendError(f'Gmail API send failed: {status} {detail}')
