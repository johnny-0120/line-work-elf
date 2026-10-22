"""Bounded local retrieval. Notes are references, never execution authority."""
import os
import re
import time
from pathlib import Path

PRIVATE_PATH = re.compile(r'private|personal|私事|私人|隱私|日記|醫療|感情', re.I)
STOP = {'你覺','覺得','得呢','怎麼','麼樣','幫我','請問','可以','什麼','一下','這個','那個','訊息','群組','最近','是否','我要','參考','筆記'}


def terms(text):
    parts = re.findall(r'[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}', text.lower())
    result = set()
    for part in parts:
        if re.fullmatch(r'[\u4e00-\u9fff]+', part):
            result.update(part[i:i+2] for i in range(len(part)-1) if part[i:i+2] not in STOP)
        else:
            result.add(part)
    return sorted(result)[:100]


def search_notes(query, limit=4):
    configured = os.getenv('OBSIDIAN_VAULT', '')
    if not configured:
        return {'available': False, 'reason': '尚未設定 Obsidian 筆記庫。', 'notes': []}
    root = Path(configured).resolve()
    if not root.is_dir():
        return {'available': False, 'reason': 'Obsidian 磁碟目前無法讀取；請確認 Google Drive 已掛載。', 'notes': []}
    words = terms(query)
    if not words:
        return {'available': True, 'notes': []}
    hits, count, deadline = [], 0, time.monotonic()+3
    try:
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not PRIVATE_PATH.search(d) and d not in ('90 封存','99 模板'))
            for name in sorted(files):
                if time.monotonic() > deadline or count >= 500:
                    break
                if not name.lower().endswith('.md') or PRIVATE_PATH.search(name):
                    continue
                count += 1
                path = Path(folder)/name
                try:
                    # Do not follow shortcuts/symlinks outside the authorized vault.
                    resolved = path.resolve()
                    relative = resolved.relative_to(root).as_posix()
                    if path.is_symlink() or resolved.stat().st_size > 512000:
                        continue
                    text = resolved.read_text(encoding='utf-8-sig')
                except (OSError, ValueError, UnicodeError):
                    continue
                front = text.split('---',2)[1] if text.startswith('---') and len(text.split('---',2))==3 else ''
                if re.search(r'(?:private|personal|sensitive)\s*:\s*(?:true|yes)|(?:visibility|access)\s*:\s*private|tags\s*:[\s\S]*(?:private|personal|私人|私事)', front, re.I) or re.search(r'(?:^|\s)#(?:private\b|私事|私人)',text,re.I):
                    continue
                low = text.lower()
                matched = [w for w in words if w in low or w in relative.lower()]
                if not matched:
                    continue
                score = len(matched)+sum((12 if w.isascii() else 3) for w in matched if w in relative.lower())
                at = min((low.find(w) for w in matched if w in low), default=0)
                excerpt = text[max(0,at-200):max(0,at-200)+900]
                # Never send common credential formats from a note to the model.
                excerpt = re.sub(r'(?i)(?:sk-(?:or-v1-|proj-)?[a-z0-9_-]{16,}|AIza[a-z0-9_-]{20,}|-----BEGIN[^\n]*PRIVATE KEY-----[\s\S]*)', '[已隱藏憑證]', excerpt)
                excerpt = re.sub(r'(?im)^.*(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password|密碼|金鑰)\s*[:=].*$', '[已隱藏憑證欄位]', excerpt)
                hits.append((score, relative, excerpt))
            if time.monotonic() > deadline or count >= 500:
                break
    except OSError:
        return {'available': False, 'reason': '筆記庫讀取暫時失敗。', 'notes': []}
    hits.sort(key=lambda hit: (-hit[0],hit[1]))
    return {'available': True, 'notes': [{'path':p,'excerpt':e} for _,p,e in hits[:limit]], 'scope': '排除私密、日記、封存與模板；最多掃描 500 篇 Markdown'}


def recent_messages(elf, sources=None, before=None, limit=16):
    with elf.lock:
        sql = ('SELECT m.id,m.text,m.timestamp,m.source,s.name FROM messages m JOIN sources s ON s.id=m.source '
               'WHERE s.enabled=1 AND m.timestamp>=? AND NOT EXISTS(SELECT 1 FROM blocked_sources b WHERE b.id=m.source)')
        args = [int((time.time()-7*86400)*1000)]
        if sources:
            sql += ' AND m.source IN ('+','.join('?' for _ in sources)+')'
            args.extend(sources)
        if before is not None:
            sql += ' AND m.timestamp<=?'
            args.append(before)
        rows = elf.db.execute(sql+' ORDER BY m.timestamp DESC,m.id DESC LIMIT ?',args+[limit]).fetchall()
    return [dict(row) | {'text':row['text'][:900]} for row in reversed(rows)]
