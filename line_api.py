"""Manage official short-lived LINE tokens without displaying credentials."""
import json
import os
import sys
from pathlib import Path
import time
from urllib import request, parse, error

ROOT = Path(__file__).resolve().parent


class LineAuthError(Exception):
    pass


def save_settings(values):
    path = ROOT / '.env'
    if not path.exists():
        # Container env-file sources are not mounted as writable files. Keep
        # the renewed token in memory; a later restart can renew again.
        os.environ.update({key: str(value) for key, value in values.items()})
        return
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    found = set()
    for index, line in enumerate(lines):
        key = line.split('=', 1)[0].strip()
        if key in values:
            lines[index] = key + '=' + str(values[key])
            found.add(key)
    lines.extend(key + '=' + str(value) for key, value in values.items() if key not in found)
    temporary = path.with_name('.env.pending')
    temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    temporary.replace(path)
    os.environ.update({key: str(value) for key, value in values.items()})


def ensure_token(force=False):
    token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
    expiry = float(os.getenv('LINE_TOKEN_EXPIRES_AT', '0') or 0)
    if not force and token and (not expiry or expiry > time.time() + 120):
        return token
    channel_id = os.getenv('LINE_CHANNEL_ID', '')
    secret = os.getenv('LINE_CHANNEL_SECRET', '')
    if not channel_id or not secret:
        raise LineAuthError('Configure LINE_CHANNEL_ID and LINE_CHANNEL_SECRET.')
    form = parse.urlencode({'grant_type': 'client_credentials', 'client_id': channel_id,
                            'client_secret': secret}).encode()
    req = request.Request('https://api.line.me/v2/oauth/accessToken', form,
                          {'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with request.urlopen(req, timeout=25) as response:
            data = json.load(response)
        token = data['access_token']
        expiry = time.time() + int(data['expires_in'])
    except error.HTTPError as exc:
        raise LineAuthError(f'LINE token API returned HTTP {exc.code}.') from None
    except (error.URLError, TimeoutError, KeyError, ValueError):
        raise LineAuthError('LINE token renewal failed; no credential was logged.') from None
    save_settings({'LINE_CHANNEL_ACCESS_TOKEN': token, 'LINE_TOKEN_EXPIRES_AT': int(expiry)})
    return token


def api(method, path, body=None):
    token = ensure_token()
    encoded = json.dumps(body).encode() if body is not None else None
    req = request.Request('https://api.line.me' + path, encoded,
        {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method=method)
    try:
        with request.urlopen(req, timeout=35) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raise LineAuthError(f'LINE API returned HTTP {exc.code}.') from None


if __name__ == '__main__':
    from elf import load_env
    load_env()
    try:
        if '--configure-webhook' in sys.argv:
            url = os.environ['WEBHOOK_URL'].rstrip('/') + '/webhook/line-work-elf'
            api('PUT', '/v2/bot/channel/webhook/endpoint', {'endpoint': url})
            print(json.dumps({'webhook_saved': True}))
        elif '--test-webhook' in sys.argv:
            info = api('POST', '/v2/bot/channel/webhook/test', {})
            print(json.dumps({'webhook_success': info.get('success'), 'status_code': info.get('statusCode'),
                              'reason': info.get('reason'), 'detail': info.get('detail')}, ensure_ascii=True))
        else:
            info = api('GET', '/v2/bot/info')
            print(json.dumps({'ok': True, 'basic_id': info.get('basicId'), 'display_name': info.get('displayName')}, ensure_ascii=True))
    except (LineAuthError, error.URLError, TimeoutError) as exc:
        status = exc.code if isinstance(exc, error.HTTPError) else None
        print(json.dumps({'ok': False, 'status': status, 'reason': str(exc) if isinstance(exc, LineAuthError) else 'LINE API request failed'}))
        raise SystemExit(1)
