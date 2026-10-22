"""Create local settings without displaying any credential values."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent
target = root / '.env'
if not target.exists():
    content = (root / '.env.example').read_text(encoding='utf-8')
    content = content.replace('INTERNAL_TOKEN=\n', 'INTERNAL_TOKEN=' + secrets.token_urlsafe(32) + '\n')
    content = content.replace('N8N_ENCRYPTION_KEY=\n', 'N8N_ENCRYPTION_KEY=' + secrets.token_hex(32) + '\n')
    target.write_text(content, encoding='utf-8')
    print('Created .env. Fill in OPENROUTER_API_KEY and LINE settings locally.')
else:
    print('.env already exists; preserved.')
