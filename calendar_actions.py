"""Explicit owner calendar commands. Ambiguous times never cause writes."""
import re
from datetime import datetime, timedelta, timezone
from calendar_client import CalendarError

TZ = timezone(timedelta(hours=8))


def number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    if value == '兩':
        return 2
    if '十' in value:
        a, b = value.split('十')
        return digits.get(a, 1) * 10 + digits.get(b, 0)
    return digits.get(value, -1)


def parse_event(text, now=None):
    now = now or datetime.now(TZ)
    # Date must be explicit, including a year for absolute dates.
    match = re.match(r'^(今天|明天|後天|\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*', text)
    if not match:
        raise CalendarError('請明確寫日期、開始與結束時間，例如：/新增行程 明天下午三點到四點 活動會議，或 /新增行程 2026-10-20 15:00到16:00 活動會議。')
    date_text = match[1]
    try:
        day = (now + timedelta(days={'今天': 0, '明天': 1, '後天': 2}[date_text])).date() if date_text in ('今天', '明天', '後天') else datetime.strptime(date_text.replace('/', '-'), '%Y-%m-%d').date()
    except ValueError:
        raise CalendarError('日期無效，請重新指定。') from None
    rest = text[match.end():]
    pattern = r'\s*(上午|下午|晚上|中午)?\s*([0-9零一二三四五六七八九十兩]{1,3})(?:(:)(\d{2})|[點時](半|[0-9零一二三四五六七八九十兩]{1,3}分?)?)'
    start = re.match(pattern, rest)
    if not start:
        raise CalendarError('請補上開始時間與結束時間，例如「下午三點到四點」。')
    remaining = rest[start.end():]
    sep = re.match(r'\s*(?:到|至|[-~～])', remaining)
    end = re.match(pattern, remaining[sep.end():]) if sep else None
    if not end:
        raise CalendarError('請補上結束時間，例如「下午三點到四點」；我不會猜測會議長度。')
    title = remaining[sep.end()+end.end():].strip()
    if not title or len(title) > 160:
        raise CalendarError('請在時間後面補上 1–160 字的行程名稱。')
    def clock(m, inherited=None):
        period, hour, colon, minute, chinese = m.groups()
        period = period or inherited
        h = number(hour)
        minutes = int(minute) if colon else (30 if chinese == '半' else number(chinese.rstrip('分')) if chinese else 0)
        if period:
            if not 1 <= h <= 12:
                raise CalendarError('上午或下午請使用 1–12 點。')
            h = h % 12 + (12 if period in ('下午', '晚上', '中午') else 0)
        elif not colon and h <= 12:
            raise CalendarError('請寫上午或下午，或改用 24 小時制，例如 15:00。')
        if not 0 <= h < 24 or not 0 <= minutes < 60:
            raise CalendarError('時間無效，請重新指定。')
        return datetime.combine(day, datetime.min.time(), TZ).replace(hour=h, minute=minutes)
    a, b = clock(start), clock(end, start[1])
    if b <= a:
        raise CalendarError('結束時間需晚於開始時間；目前請使用同一天的行程。')
    if a <= now:
        raise CalendarError('開始時間已過，請指定未來時間。')
    return {'title': title, 'start': a.isoformat(), 'end': b.isoformat()}


def execute(client, command, job_id, now=None, metadata=None):
    now = now or datetime.now(TZ)
    if command.startswith('/新增行程 '):
        import hashlib
        eid = hashlib.sha256(job_id.encode()).hexdigest()
        try:
            event = parse_event(command[len('/新增行程 '):].strip(), now)
        except CalendarError:
            # A request may have succeeded before a network failure. Retrying
            # after its start time must return the real receipt, not a new event.
            existing = client.get_event(eid)
            if (isinstance(existing,dict) and
                    existing.get('extendedProperties',{}).get('private',{}).get('elf_job')==job_id):
                return '✅ 原行程已存在，沒有重複新增\n' + existing.get('summary','未命名行程') + '\n' + existing.get('start',{}).get('dateTime','')[:16].replace('T',' ') + '\n' + existing.get('htmlLink','')
            raise
        extras = {k:metadata[k] for k in ('description','location') if isinstance(metadata.get(k),str)} if metadata else {}
        result = client.create_event(eid, job_id, **event, **extras)
        return '✅ 已新增 Google Calendar 行程\n' + event['title'] + '\n' + event['start'][:16].replace('T', ' ') + ' ～ ' + event['end'][11:16] + '（台北時間）\n' + result.get('htmlLink', '')
    query = command[len('/行程'):].strip()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if query in ('', '今天'):
        a, b = midnight, midnight + timedelta(days=1)
    elif query == '明天':
        a, b = midnight + timedelta(days=1), midnight + timedelta(days=2)
    elif query == '本週':
        a = midnight - timedelta(days=midnight.weekday())
        b = a + timedelta(days=7)
    else:
        raise CalendarError('支援 /行程 今天、/行程 明天、/行程 本週。')
    events = client.list_events(a.isoformat(), b.isoformat())
    lines = ['📅 Google Calendar｜' + (query or '今天') + '（台北時間）']
    for e in events[:20]:
        raw = e['start'].get('dateTime')
        when = datetime.fromisoformat(raw.replace('Z', '+00:00')).astimezone(TZ).strftime('%m/%d %H:%M') if raw else e['start'].get('date', '') + ' 全天'
        lines.append(when + ' ' + e['title'])
    if not events:
        lines.append('沒有行程。')
    if len(events) > 20:
        lines.append('僅顯示前 20 筆，請到 Google Calendar 查看其餘行程。')
    return '\n'.join(lines)
