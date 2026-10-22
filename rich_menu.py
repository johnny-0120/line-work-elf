"""Build and install the owner's LINE six-button rich menu."""
import hashlib
import json
import os
from pathlib import Path
from urllib import request, error

from elf import ROOT, load_env
from line_api import api, ensure_token, LineAuthError

BUTTONS = [('立即整理', '彙整工作重點', '/整理', 'paper'),
           ('待回覆', '查看待處理事項', '/待回覆', 'check'),
           ('今日行程', '查看今天的安排', '/行程 今天', 'today'),
           ('明日行程', '提前安排明天', '/行程 明天', 'tomorrow'),
           ('新增行程', '指定時間與主題', '/新增行程', 'plus'),
           ('使用說明', '功能與操作方式', '/說明', 'help')]


def definition():
    areas = []
    for index, (label, _, command, _) in enumerate(BUTTONS):
        col, row = index % 3, index // 3
        left, right = col * 2500 // 3, (col+1)*2500//3
        areas.append({'bounds': {'x': left, 'y': row*843, 'width': right-left, 'height': 843},
                      'action': {'type': 'message', 'label': label, 'text': command}})
    return {'size': {'width': 2500, 'height': 1686}, 'selected': True,
            'name': '公事小精靈功能選單 v1', 'chatBarText': '開啟功能選單', 'areas': areas}


def image_file():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (2500, 1686), '#f0f4f2')
    draw = ImageDraw.Draw(img)
    bold = ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc', 92)
    regular = ImageFont.truetype('C:/Windows/Fonts/msjh.ttc', 43)
    small = ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc', 82)
    for i, (title, subtitle, _, icon) in enumerate(BUTTONS):
        col, row = i%3, i//3
        x0, x1 = col*2500//3, (col+1)*2500//3
        top = row*843
        active = i == 0
        bg, fg, muted = ('#087b69', '#ffffff', '#c0e7dd') if active else ('#f7faf8', '#185449', '#618078')
        draw.rounded_rectangle((x0+20, top+20, x1-20, top+823), radius=42, fill=bg)
        cx, cy = (x0+x1)//2, top+268
        draw.ellipse((cx-133, cy-133, cx+133, cy+133), fill='#258f7e' if active else '#e3eeea')
        def line(points, width=12):
            draw.line(points, fill=fg, width=width, joint='curve')
        if icon == 'paper':
            draw.rounded_rectangle((cx-65,cy-85,cx+65,cy+85),radius=12,outline=fg,width=10)
            for dy in (-35,0,35):
                line([(cx-36,cy+dy),(cx+36,cy+dy)],8)
        elif icon == 'check':
            for dy in (-55,5,65):
                line([(cx-80,cy+dy),(cx-65,cy+dy+16),(cx-40,cy+dy-14)],9)
                line([(cx-10,cy+dy),(cx+75,cy+dy)],9)
        elif icon in ('today','tomorrow'):
            draw.rounded_rectangle((cx-82,cy-72,cx+82,cy+85),radius=13,outline=fg,width=10)
            line([(cx-82,cy-28),(cx+82,cy-28)],9)
            for dx in (-43,43):
                line([(cx+dx,cy-96),(cx+dx,cy-50)],10)
            draw.text((cx,cy+23),'今' if icon=='today' else '明',font=small,fill=fg,anchor='mm')
        elif icon == 'plus':
            line([(cx-76,cy),(cx+76,cy)],14)
            line([(cx,cy-76),(cx,cy+76)],14)
        else:
            draw.ellipse((cx-78,cy-78,cx+78,cy+78),outline=fg,width=10)
            draw.text((cx,cy-3),'?',font=ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc',125),fill=fg,anchor='mm')
        draw.text((cx,top+480),title,font=bold,fill=fg,anchor='mm')
        draw.text((cx,top+595),subtitle,font=regular,fill=muted,anchor='mm')
    path = ROOT / 'rich-menu.png'
    img.save(path, optimize=True)
    preview = ROOT / 'rich-menu-preview.png'
    img.resize((1000,674)).save(preview)
    return path


def install():
    load_env()
    owner = os.getenv('LINE_OWNER_USER_ID')
    if not owner:
        raise LineAuthError('Owner must be configured first.')
    path = image_file()
    body = definition()
    api('POST', '/v2/bot/richmenu/validate', body)
    state = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'LineWorkElf/state/rich-menu.json'
    fingerprint = hashlib.sha256(path.read_bytes()+json.dumps(body).encode()).hexdigest()
    previous = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
    menu_id = previous.get('id') if previous.get('fingerprint') == fingerprint else None
    if menu_id:
        api('GET', '/v2/bot/richmenu/' + menu_id)
    else:
        menu_id = api('POST', '/v2/bot/richmenu', body)['richMenuId']
        req = request.Request('https://api-data.line.me/v2/bot/richmenu/' + menu_id + '/content',
            path.read_bytes(), {'Authorization': 'Bearer ' + ensure_token(), 'Content-Type': 'image/png'}, method='POST')
        try:
            with request.urlopen(req, timeout=40) as response:
                response.read()
        except error.HTTPError as exc:
            raise LineAuthError(f'Rich menu upload HTTP {exc.code}.') from None
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({'id':menu_id,'fingerprint':fingerprint}),encoding='utf-8')
    # Only the owner needs these controls. No other user's menu is changed.
    api('POST', '/v2/bot/user/' + owner + '/richmenu/' + menu_id)
    verified = api('GET', '/v2/bot/user/' + owner + '/richmenu')
    print(json.dumps({'installed': verified.get('richMenuId') == menu_id, 'buttons': len(body['areas']), 'scope':'owner'}))


if __name__ == '__main__':
    import sys
    if '--preview' in sys.argv:
        image_file()
        print('Preview generated.')
    else:
        install()
