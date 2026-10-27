"""Read the currently configured public URL after a tunnel replacement."""
import os,re
from pathlib import Path
from urllib.parse import urlsplit

def public_base():
    value=os.getenv('WEBHOOK_URL','').rstrip('/')
    path=Path(os.getenv('ELF_CONFIG_FILE',str(Path(__file__).resolve().parent/'.env')))
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('WEBHOOK_URL='):value=line.split('=',1)[1].strip().strip('"\'').rstrip('/')
    except OSError:pass
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return ''
    return value
