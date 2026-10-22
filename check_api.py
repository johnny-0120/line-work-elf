"""Safely test credentials without printing secrets or account details."""
import json
import os
import re
import sys
from urllib import request, error
from elf import load_env, Elf, Problem

load_env()
key = os.getenv('OPENROUTER_API_KEY', '')
if not key:
    print(json.dumps({'ok': False, 'reason': 'OPENROUTER_API_KEY missing'}))
    sys.exit(1)
req = request.Request('https://openrouter.ai/api/v1/key', headers={'Authorization': 'Bearer ' + key})
try:
    with request.urlopen(req, timeout=25) as response:
        json.load(response)
    print(json.dumps({'authenticated': True}))
    if '--auth-only' not in sys.argv:
        result = Elf(':memory:').proxy({'model': 'openrouter/free', 'messages': [{'role': 'user', 'content': 'Reply only OK.'}]})
        print(json.dumps({'ok': True, 'model': result.get('model'), 'cost': result.get('usage', {}).get('cost'),
                          'has_reply': bool(result.get('choices', [{}])[0].get('message', {}).get('content'))}))
except error.HTTPError as exc:
    try:
        reason = str(json.loads(exc.read()).get('error', {}).get('message', 'Authentication failed'))
    except (ValueError, AttributeError):
        reason = 'Authentication failed'
    reason = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', reason.replace(key, '[REDACTED]'))
    print(json.dumps({'ok': False, 'status': exc.code, 'reason': reason[:220]}))
    sys.exit(1)
except Problem as exc:
    print(json.dumps({'ok': False, 'status': exc.status, 'reason': exc.message}))
    sys.exit(1)
