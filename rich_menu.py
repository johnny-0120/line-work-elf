"""Build owner controls and a public onboarding menu without sharing data."""
import hashlib
import json
import os
from pathlib import Path
from urllib import request, error

from elf import ROOT, load_env
from line_api import api, ensure_token, LineAuthError, save_settings

BUTTONS = [('立即整理', '彙整工作重點', '/整理', 'paper'),
           ('待回覆', '查看待處理事項', '/待回覆', 'check'),
           ('今日行程', '查看今天的安排', '/行程 今天', 'today'),
           ('明日行程', '提前安排明天', '/行程 明天', 'tomorrow'),
           ('新增行程', '指定時間與主題', '/新增行程', 'plus'),
           ('使用說明', '功能與操作方式', '/說明', 'help')]
PUBLIC_BUTTONS = [('開始使用','第一次來，從這裡開始','/開始使用','start'),
                  ('綁定工作群','點選完成群組綁定','/綁定群組','link'),
                  ('立即整理','整理自己的新討論','/整理','paper'),
                  ('我的群組','查看自己的收集來源','/群組','groups'),
                  ('待回覆','查看自己的待辦','/待回覆','check'),
                  ('使用設定','日曆連線與整理偏好','/使用設定','settings')]


def definition(public=False):
    areas = []
    for index, (label, _, command, _) in enumerate(PUBLIC_BUTTONS if public else BUTTONS):
        col, row = index % 3, index // 3
        left, right = col * 2500 // 3, (col+1)*2500//3
        areas.append({'bounds': {'x': left, 'y': row*843, 'width': right-left, 'height': 843},
                      'action': __import__('interaction').action(label,command)})
    return {'size': {'width': 2500, 'height': 1686}, 'selected': True,
            'name': '公事小精靈｜'+('多人群組引導 v2' if public else '個人工具 v2'),
            'chatBarText': '開始使用・群組整理' if public else '開啟功能選單', 'areas': areas}


def image_file(public=False, dark=False):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (2500, 1686), '#101A2C' if dark else '#A8C0DF')
    draw = ImageDraw.Draw(img)
    bold = ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc', 92)
    regular = ImageFont.truetype('C:/Windows/Fonts/msjh.ttc', 43)
    small = ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc', 82)
    for i, (title, subtitle, _, icon) in enumerate(PUBLIC_BUTTONS if public else BUTTONS):
        col, row = i%3, i//3
        x0, x1 = col*2500//3, (col+1)*2500//3
        top = row*843
        active = i == 0
        bg, fg, muted = (('#294360', '#EDF4FF', '#B6CAE4') if active else ('#1B2C45', '#DCE9FA', '#A8C0DF')) if dark else (('#A8C0DF', '#17243A', '#344D70') if active else ('#DCE8F6', '#17243A', '#405C80'))
        draw.rounded_rectangle((x0+20, top+20, x1-20, top+823), radius=42, fill=bg)
        cx, cy = (x0+x1)//2, top+268
        draw.ellipse((cx-133, cy-133, cx+133, cy+133), fill=('#314969' if dark else '#C6D9EF'))
        def line(points, width=12):
            draw.line(points, fill=fg, width=width, joint='curve')
        if icon == 'start':
            draw.polygon([(cx-35,cy-73),(cx-35,cy+73),(cx+80,cy)],fill=('#A8C0DF' if dark else '#516D98'))
        elif icon == 'link':
            draw.rounded_rectangle((cx-91,cy-45,cx+16,cy+37),radius=25,outline=fg,width=11)
            draw.rounded_rectangle((cx-16,cy-15,cx+91,cy+67),radius=25,outline=fg,width=11)
        elif icon == 'groups':
            for dx,dy in ((-68,0),(68,0),(0,-32)):
                draw.ellipse((cx+dx-26,cy+dy-55,cx+dx+26,cy+dy-3),outline=fg,width=8)
                draw.arc((cx+dx-45,cy+dy+8,cx+dx+45,cy+dy+81),180,360,fill=fg,width=8)
        elif icon == 'settings':
            for dy,dx in ((-55,-35),(0,45),(55,-10)):
                line([(cx-85,cy+dy),(cx+85,cy+dy)],8)
                draw.ellipse((cx+dx-17,cy+dy-17,cx+dx+17,cy+dy+17),fill=('#A8C0DF' if dark else '#516D98'))
        elif icon == 'paper':
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
        avatar=ROOT/'assets/avatar.png'
        if active and avatar.exists():
            portrait=Image.open(avatar).convert('RGB').resize((266,266))
            mask=Image.new('L',(266,266));ImageDraw.Draw(mask).ellipse((0,0,265,265),fill=255)
            img.paste(portrait,(cx-133,cy-133),mask)
        draw.text((cx,top+480),title,font=bold,fill=fg,anchor='mm')
        draw.text((cx,top+595),subtitle,font=regular,fill=muted,anchor='mm')
    suffix = '-dark' if dark else ''
    path = ROOT / (('rich-menu-public' if public else 'rich-menu')+suffix+'.png')
    img.save(path, optimize=True)
    preview = ROOT / (('rich-menu-public' if public else 'rich-menu')+suffix+'-preview.png')
    img.resize((1000,674)).save(preview)
    return path


def install(public=False):
    load_env()
    owner = os.getenv('LINE_OWNER_USER_ID')
    if not owner:
        raise LineAuthError('Owner must be configured first.')
    if public:
        basic=api('GET','/v2/bot/info').get('basicId')
        if not basic:
            raise LineAuthError('Cannot resolve the official account basic ID.')
        save_settings({'PUBLIC_BOT_BASIC_ID':basic})
    path = image_file(public)
    body = definition(public)
    api('POST', '/v2/bot/richmenu/validate', body)
    state_root=Path(os.environ.get('ELF_STATE_DIR', str(Path(os.environ.get('LOCALAPPDATA',str(ROOT)))/'LineWorkElf/state')))
    state = state_root / ('rich-menu-public.json' if public else 'rich-menu.json')
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
    # A per-user owner menu takes precedence over the shared default menu.
    endpoint='/v2/bot/user/all/richmenu' if public else '/v2/bot/user/'+owner+'/richmenu'
    api('POST', endpoint+'/'+menu_id)
    verified = api('GET', endpoint)
    result={'installed':verified.get('richMenuId')==menu_id,'buttons':len(body['areas']),'scope':'default' if public else 'owner'}
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    import sys
    if '--preview' in sys.argv:
        image_file('--public' in sys.argv)
        print('Preview generated.')
    else:
        install('--public' in sys.argv)
