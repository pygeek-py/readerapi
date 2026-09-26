"""
One-time helper: authorise Reader to send email from your Gmail account and
print the refresh token to put in Render's environment.

Run it locally (not on Render):

    python scripts/gmail_authorize.py CLIENT_ID CLIENT_SECRET

Uses only the standard library. The OAuth client must be of type "Desktop app"
(Google allows any loopback port for those).
"""
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPE = 'https://www.googleapis.com/auth/gmail.send'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
PORT = 8765


class Handler(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        Handler.result = {k: v[0] for k, v in query.items()}
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<h2>All set.</h2><p>You can close this tab and go back to the terminal.</p>')

    def log_message(self, *args):
        pass


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_id, client_secret = sys.argv[1], sys.argv[2]
    redirect_uri = f'http://127.0.0.1:{PORT}'
    state = secrets.token_urlsafe(16)

    url = AUTH_URL + '?' + urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',  # forces Google to issue a refresh token
        'state': state,
    })
    print('Opening your browser. If it does not open, paste this URL into it:\n')
    print(url + '\n')
    webbrowser.open(url)

    with http.server.HTTPServer(('127.0.0.1', PORT), Handler) as server:
        server.handle_request()

    result = Handler.result
    if result.get('state') != state or 'code' not in result:
        sys.exit(f"Authorisation failed: {result.get('error', 'no code returned')}")

    body = urllib.parse.urlencode({
        'code': result['code'],
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as response:
        tokens = json.loads(response.read())

    refresh = tokens.get('refresh_token')
    if not refresh:
        sys.exit('Google did not return a refresh token. Remove the app at '
                 'https://myaccount.google.com/permissions and run this again.')

    print('\nSuccess. Set these on Render (Environment tab) for the readerapi service:\n')
    print('EMAIL_BACKEND=api.gmail_backend.GmailAPIEmailBackend')
    print(f'GMAIL_CLIENT_ID={client_id}')
    print(f'GMAIL_CLIENT_SECRET={client_secret}')
    print(f'GMAIL_REFRESH_TOKEN={refresh}')
    print('DEFAULT_FROM_EMAIL=Reader <your-gmail-address@gmail.com>')
    print('\nTreat the refresh token like a password: anyone holding it can send mail as you.')


if __name__ == '__main__':
    main()
