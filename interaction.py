"""Native button workflows; all selectors are resolved inside the current user's DB."""
import copy
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta
import onboarding as ui

PREFIX='elf:v1:'
FIXED={'/我的方案','/私訊助理','/啟用私訊助理','/暫停私訊助理','/完成對話輸入','/新手教學','/整理雙人對話','/記一則','/我的記錄','/開始使用','/同意並開始','/綁定群組','/綁定此群','/邀請教學','/群組','/待回覆','/整理','/立即整理','/使用設定','/資料管理','/刪除我的資料','/狀態','/說明','/行事曆','/連接行事曆','/行程 今天','/行程 明天','/行程 本週','/新增行程','/取消操作','/行程開始','/行程結束','/確認新增','/每日整理 開啟','/每日整理 關閉','/解除日曆確認','/解除日曆'}

def action(label,command):
    return {'type':'postback','label':label[:20],'data':PREFIX+command,'displayText':label[:300]}

def decode(event):
    """Called only AFTER LINE signature/destination validation; reject foreign actions."""
    if event.get('type')!='postback': return event
    data=event.get('postback',{}).get('data','')
    command=data[len(PREFIX):] if isinstance(data,str) and data.startswith(PREFIX) else ''
    valid=command in FIXED or bool(re.fullmatch(r'/教學 [1-6]',command)) or bool(re.fullmatch(r'/(?:對話保存|對話待辦|刪除記錄|重試對話) [0-9a-f]{16}',command)) or bool(re.fullmatch(r'/(?:來源|來源暫停|來源恢復|來源移除確認|來源移除|來源改名) [0-9a-f]{16}',command)) or bool(re.fullmatch(r'/已處理 [A-Za-z0-9-]{1,80}',command))
    if not valid:return event
    if command in ('/行程開始','/行程結束'):
        value=event.get('postback',{}).get('params',{}).get('datetime','')
        if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d',value):return event
        command+=' '+value
    result=copy.deepcopy(event)
    result['_native_type']='postback'
    result['type']='message'
    result['message']={'type':'text','id':'button:'+str(event.get('webhookEventId','')),'text':command}
    return result

def key(sid):return hashlib.sha256(sid.encode()).hexdigest()[:16]

def groups(elf):
    return [dict(r) for r in elf.db.execute('SELECT s.id,s.name,s.enabled FROM sources s JOIN group_bindings b ON b.id=s.id ORDER BY s.name LIMIT 50')]

def source(elf,selector):
    matches=[r for r in groups(elf) if key(r['id'])==selector]
    return matches[0] if len(matches)==1 else None

def wizard(elf):
    row=elf.db.execute('SELECT data,expires FROM ui_sessions WHERE id=1').fetchone()
    return json.loads(row[0]) if row and row[1]>time.time() else None

def save_wizard(elf,data):
    elf.db.execute('INSERT OR REPLACE INTO ui_sessions VALUES(1,?,?)',(json.dumps(data),time.time()+600))

def normalize_text(elf,event):
    if event.get('_capture_normalized'):return event
    if event.get('source',{}).get('type')!='user' or event.get('source',{}).get('userId')!=elf.owner_id:return event
    state=wizard(elf)
    text=event.get('message',{}).get('text','')
    if state and state.get('capture') and text and not event.get('_native_type') and text not in ('/取消操作','/完成對話輸入'):
        result=copy.deepcopy(event);result['_capture_normalized']=True;result['message']['text']=('/貼上對話 ' if state['capture']=='conversation' else '/記住內容 ')+text[:12001];return result
    if state and state.get('rename') and text and not text.startswith('/'):
        result=copy.deepcopy(event);result['message']['text']='/來源新名稱 '+text[:60];return result
    if state and state.get('step')=='title' and text and not text.startswith('/'):
        result=copy.deepcopy(event);result['message']['text']='/行程主題 '+text[:100];return result
    return event

def handle(elf,text,sid,kind):
    if kind in ('group','room') and text=='/綁定此群':
        if source(elf,key(sid)): return ('你已綁定這個群組，可回私訊管理或整理。',False)
        if len(groups(elf))>=5:return ('最多綁定五個群組，請先到「我的群組」移除來源。',False)
        if elf.db.execute('SELECT 1 FROM blocked_sources WHERE id=?',(sid,)).fetchone():return ('此來源已隔離，沒有啟用。',False)
        # Signed click supplies both the actor and the real group source. No guessed IDs.
        name='工作群組 '+str(len(groups(elf))+1)
        elf.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,1)',(sid,name))
        elf.db.execute('INSERT OR REPLACE INTO group_bindings VALUES(?,?,?)',(sid,'signed_button',time.time()))
        elf.db.execute('UPDATE known_groups SET owner_seen=1,present=1 WHERE id=?',(sid,))
        return ('已為你綁定這個群組，從現在開始收集新文字。請先取得成員同意；私事或 #private 開頭會排除。摘要只私訊你，其他人需各自開通。',False)
    if kind!='user':return None
    if text=='/新手教學' or re.fullmatch(r'/教學 [1-6]',text):return ('請依教學卡片一步一步操作。',False)
    if text in ('/整理雙人對話','/記一則'):
        save_wizard(elf,{'capture':'conversation' if text=='/整理雙人對話' else 'note','parts':[]})
        return ('請把要整理的對話文字貼上來。我會透過 外部 AI 服務分析，只整理你提供的內容；不會自動讀取其他聊天室。十分鐘內有效。' if text=='/整理雙人對話' else '請直接回覆要記住的內容，會保存在你自己的記錄，保留三十天。十分鐘內有效。',False)
    if text.startswith(('/貼上對話 ','/記住內容 ')):
        state=wizard(elf);conversation=text.startswith('/貼上對話 ')
        if not state or state.get('capture')!=('conversation' if conversation else 'note'):return ('這次輸入已過期，請重新按「整理雙人對話」或「記一則」。',False)
        value=text.split(' ',1)[1]
        if value.strip().startswith(('私事','#private')):return ('這段標記為私事，沒有保存或送 AI。其他對話仍可繼續傳。',False)
        from dm_companion import remember,enqueue
        if conversation:
            parts=state.get('parts',[])
            if len(parts)>=30 or sum(len(p['text']) for p in parts)+len(parts)+len(value)>12000:return ('已達這次收集上限，請先按「開始整理這些對話」，再開新的一次。',False)
            mid=getattr(elf,'_command_mid',None) or str(uuid.uuid4())
            if not any(p['id']==mid for p in parts):parts.append({'id':mid,'text':value})
            state['parts']=parts;save_wizard(elf,state)
            return ('已收到 '+str(len(parts))+' 段文字。你可以繼續轉傳／貼上，全部送完後按「開始整理這些對話」。尚未送 AI。',False)
        elf.db.execute('DELETE FROM ui_sessions')
        return (remember(elf,'記住 '+value,getattr(elf,'_command_mid',None) or 'note:'+str(uuid.uuid4())) or '沒有收到可保存的文字。',False)
    if text=='/完成對話輸入':
        state=wizard(elf);parts=state.get('parts',[]) if state else []
        if not state or state.get('capture')!='conversation' or not parts:return ('目前還沒有收到對話文字。請先按「整理雙人對話」，再轉傳或貼上內容。',False)
        from dm_companion import enqueue
        reply=enqueue(elf,'\n'.join(p['text'] for p in parts),'batch:'+str(uuid.uuid4()),[p['id'] for p in parts]);elf.db.execute('DELETE FROM ui_sessions');return (reply,False)
    if text=='/我的方案':return ('目前免費使用，不需要付款。功能與用量以方案卡片為準。',False)
    if text in ('/私訊助理','/啟用私訊助理','/暫停私訊助理'):
        if not hasattr(elf,'member_key'):return ('你的管理者私訊助理已啟用；私人聊天不自動納入公事摘要。',False)
        if text=='/啟用私訊助理':elf.db.execute("INSERT OR REPLACE INTO controls VALUES('member_assistant_enabled',1)")
        elif text=='/暫停私訊助理':
            elf.db.execute("INSERT OR REPLACE INTO controls VALUES('member_assistant_enabled',0)");elf.db.execute("UPDATE assistant_jobs SET state='cancelled',text='' WHERE state='pending'");elf.db.execute('DELETE FROM assistant_context')
        return ('已開啟智慧私訊，你接下來傳給我的文字會交由 AI 理解；標記私事的文字除外。' if text=='/啟用私訊助理' else '已暫停智慧私訊，仍可使用功能按鈕。' if text=='/暫停私訊助理' else '請確認下方智慧私訊的資料用途。',False)
    if text=='/我的記錄':return ('以下是你自己明確要求保存的內容。',False)
    if text.startswith('/刪除記錄 '):
        cur=elf.db.execute('DELETE FROM personal_notes WHERE id=?',(text.split(' ',1)[1],));return ('已移除這則記錄，不影響其他使用者。' if cur.rowcount else '這則記錄已移除，或不屬於你的帳號。',False)
    if text.startswith('/重試對話 '):
        cid=text.split(' ',1)[1]
        row=elf.db.execute("SELECT 1 FROM conversation_imports WHERE id=? AND state='failed' AND created>?",(cid,time.time()-86400)).fetchone()
        if not row:return ('找不到可重試的整理，可能已完成或原文已到期。',False)
        elf.db.execute("UPDATE conversation_imports SET state='pending' WHERE id=?",(cid,));elf.db.execute('DELETE FROM conversation_leases WHERE id=?',(cid,));return ('已重新排入整理，不必重貼。完成後會私訊結果。',False)
    if text.startswith(('/對話保存 ','/對話待辦 ')):
        cid=text.split(' ',1)[1];row=elf.db.execute("SELECT summary FROM conversation_imports WHERE id=? AND state='done' AND created>?",(cid,time.time()-86400)).fetchone()
        if not row:return ('這份整理已過期，或不屬於你的帳號。',False)
        data=json.loads(row[0])
        if text.startswith('/對話保存 '):
            elf.db.execute('INSERT OR IGNORE INTO personal_notes VALUES(?,?,?)',(cid,data['summary']+'\n'+'\n'.join(a['suggestion'] for a in data['actions']),time.time()));return ('已保存到你自己的記錄；不會自動寫入公事摘要或日曆。',False)
        elf.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,0)',(elf.owner_id,'手動對話建議'))
        for i,a in enumerate(data['actions']):
            tid=hashlib.sha256((cid+str(i)).encode()).hexdigest()[:8]
            elf.db.execute('INSERT OR IGNORE INTO tasks(id,message_id,source,summary,created) VALUES(?,?,?,?,?)',(tid,'conversation:'+cid+':'+str(i),elf.owner_id,a['suggestion'],time.time()))
        return ('已把這份整理的 '+str(len(data['actions']))+' 項建議加入你的待辦；尚未新增日曆或發送訊息給他人。',False)
    if text=='/解除日曆':
        elf.db.execute('DELETE FROM calendar_tokens');elf.db.execute("DELETE FROM calendar_jobs WHERE state='pending'");return ('已清除你的本機日曆授權並停止尚未執行的要求。可至 Google 帳號的第三方連線頁撤銷雲端授權。',False)
    if text in ('/開始使用','/使用設定','/資料管理','/邀請教學','/行事曆','/連接行事曆','/解除日曆確認'):
        return ('請點選卡片中的功能繼續。',False)
    if text.startswith('/來源新名稱 '):
        state=wizard(elf);row=source(elf,state.get('rename','')) if state else None
        if not row:return ('改名操作已過期，請重新選擇群組。',False)
        name=text[len('/來源新名稱 '):].strip()[:60]
        if not name:return ('名稱不能空白，請重新輸入。',False)
        elf.db.execute('UPDATE sources SET name=? WHERE id=?',(name,row['id']));elf.db.execute('DELETE FROM ui_sessions');return ('已更新你看到的群組名稱，不影響其他使用者。',False)
    if text.startswith(('/來源改名 ','/來源 ','/來源暫停 ','/來源恢復 ','/來源移除確認 ','/來源移除 ')):
        command,selector=text.split(maxsplit=1);row=source(elf,selector)
        if not row:return ('這個來源已移除，或不屬於你的帳號。請回「我的群組」重新選擇。',False)
        if command=='/來源改名':
            save_wizard(elf,{'rename':selector});return ('這個群組要叫什麼名稱？直接回覆名稱即可，或按取消。',False)
        if command=='/來源暫停':
            elf.db.execute('UPDATE sources SET enabled=0 WHERE id=?',(row['id'],));elf.erase_snapshots(source_id=row['id'])
            return ('已暫停這個群組的新訊息收集，既有資料仍依保留期限處理。',False)
        if command=='/來源恢復':
            from public_groups import member_exists
            origin=elf.db.execute('SELECT source_type FROM group_origins WHERE id=?',(row['id'],)).fetchone()
            if not origin or not member_exists(origin[0],row['id'],elf.owner_id):return ('無法確認你仍在這個群組，沒有恢復收集。',False)
            elf.db.execute('UPDATE sources SET enabled=1 WHERE id=?',(row['id'],))
            return ('已恢復收集接下來的新訊息，不會補讀暫停期間的對話。',False)
        if command=='/來源移除':
            state=wizard(elf)
            if not state or state.get('remove')!=selector:return ('移除確認已過期，請重新選擇群組。',False)
            elf.purge_source(row['id']);elf.db.execute('DELETE FROM group_bindings WHERE id=?',(row['id'],));elf.db.execute('DELETE FROM ui_sessions')
            return ('已移除你自己的群組綁定與已存訊息、待辦，不影響其他使用者。',False)
        if command=='/來源移除確認':save_wizard(elf,{'remove':selector})
        return ('請選擇此群組的管理方式。',False)
    if text=='/新增行程':
        save_wizard(elf,{'step':'title'})
        return ('這次行程叫什麼名稱？直接回覆主題即可，例如「專案討論」。十分鐘內有效。',False)
    if text=='/取消操作':
        elf.db.execute('DELETE FROM ui_sessions');return ('已取消這次操作，沒有新增行程。',False)
    if text.startswith('/行程主題 '):
        state=wizard(elf)
        if not state or state.get('step')!='title':return ('操作已過期，請重新按「新增行程」。',False)
        save_wizard(elf,{'step':'start','title':text[len('/行程主題 '):].strip()[:100]})
        return ('請選擇開始日期與時間（台北時間）。',False)
    if text.startswith(('/行程開始 ','/行程結束 ')):
        state=wizard(elf);start=text.startswith('/行程開始 ')
        if not state or state.get('step')!=('start' if start else 'end'):return ('操作已過期或步驟不符，請重新按「新增行程」。',False)
        from elf import TAIPEI
        try:value=datetime.fromisoformat(text.split(' ',1)[1]).replace(tzinfo=TAIPEI)
        except ValueError:return ('時間格式不正確，請重新選擇。',False)
        if value<=datetime.now(TAIPEI):return ('請選擇未來時間。',False)
        if not start and (value<=datetime.fromisoformat(state['start']) or value.date()!=datetime.fromisoformat(state['start']).date()):return ('結束時間需晚於開始時間，目前支援同一天的行程。',False)
        state['start' if start else 'end']=value.isoformat();state['step']='end' if start else 'confirm';save_wizard(elf,state)
        return ('再選擇結束時間。' if start else '請確認行程內容，按「確認新增」才會寫入日曆。',False)
    if text=='/確認新增':
        state=wizard(elf)
        if not state or state.get('step')!='confirm':return ('這筆行程已送出或已過期，不會重複建立。',False)
        command='/新增行程 '+state['start'][:10]+' '+state['start'][11:16]+'到'+state['end'][11:16]+' '+state['title']
        elf.db.execute('INSERT INTO calendar_jobs VALUES(?,?,NULL,?,?)',(str(uuid.uuid4()),command,'pending',time.time()));elf.db.execute('DELETE FROM ui_sessions')
        return ('已送出新增要求，完成後會私訊實際結果；時間衝突時不會直接建立。',False)
    return None

def render(elf,event,text):
    if event.get('source',{}).get('type')!='user':return None
    cmd=event.get('message',{}).get('text','').strip()
    member=hasattr(elf,'member_key')
    if cmd=='/我的方案':
        from service_plans import card
        return [card(elf)]
    if cmd in ('/私訊助理','/啟用私訊助理','/暫停私訊助理'):
        from member_assistant import consent_card
        return [consent_card(elf,text)]
    if cmd=='/新手教學' or re.fullmatch(r'/教學 [1-6]',cmd):return [ui.tutorial(int(cmd.split(' ')[1]) if cmd.startswith('/教學 ') else 1)]
    if cmd.startswith('/貼上對話 '):return [ui.card('對話收集中','還有內容可以繼續傳',[text,'確認都送完後再按整理，這樣會把多則轉傳訊息一起理解。'],[ui.button('開始整理這些對話','/完成對話輸入',primary=True),ui.button('取消這次輸入','/取消操作')],'對話已收到，可繼續轉傳或開始整理')]
    if cmd in ('/整理雙人對話','/記一則'):return [ui.card('直接回覆文字即可','貼上對話內容' if cmd=='/整理雙人對話' else '記一則自己的筆記',[text],[ui.button('取消這次輸入','/取消操作'),ui.button('看逐步教學','/教學 5' if cmd=='/整理雙人對話' else '/教學 6')],'請回覆要整理或保存的文字')]
    if cmd=='/我的記錄':
        rows=elf.db.execute('SELECT id,text FROM personal_notes ORDER BY created DESC LIMIT 10').fetchall()
        if not rows:return [ui.card('我的記錄','還沒有保存的內容',['按「記一則」，或直接對小精靈說「幫我記住……」。'],[ui.button('記一則','/記一則',primary=True)],'目前沒有保存的記錄')]
        bubbles=[ui.card('我的記錄',str(i+1)+' · 已保存',[r['text']],[ui.button('刪除這則記錄','/刪除記錄 '+r['id']),ui.button('再記一則','/記一則')],'已保存的個人記錄')['contents'] for i,r in enumerate(rows)]
        return [{'type':'flex','altText':'你自己的已保存記錄','contents':{'type':'carousel','contents':bubbles}}]
    if cmd in ('/開始使用','/開始','/說明'):
        buttons=[ui.button('第一次使用：逐步教學','/新手教學',primary=True),ui.button('整理雙人對話','/整理雙人對話'),ui.button('綁定與管理群組','/群組'),ui.button('記一則／我的記錄','/我的記錄'),ui.button('日曆與設定','/使用設定'),ui.button('開啟智慧私訊','/私訊助理')]
        return [ui.card('你的工作入口','今天，想先整理什麼？',['群組摘要、待回覆、下一步都可以直接點選。','每位使用者的來源、待辦與日曆授權分開保存。'],buttons,'點選卡片開始使用')]
    if cmd=='/綁定群組':return [ui.pairing(text)]
    if cmd=='/邀請教學':return [ui.invitation()]
    if cmd=='/資料管理':
        if member:return [ui.data_controls()]
        return [ui.card('管理者資料設定','管理自己的收集來源',['可在「我的群組」逐一暫停或解除來源。日曆仍使用你原有的個人授權。'],[ui.button('我的群組','/群組',primary=True),ui.button('Google Calendar','/行事曆'),ui.button('返回設定','/使用設定')],'管理個人資料與來源')]
    if cmd=='/群組':
        rows=groups(elf)
        return [ui.card('只顯示你的來源','我的工作群',['選擇群組，即可暫停、恢復或解除綁定。'] if rows else ['尚未綁定群組，按下方開始。'],[ui.button(r['name'][:20],'/來源 '+key(r['id'])) for r in rows[:5]]+[ui.button('綁定工作群','/綁定群組',primary=True)],'選擇自己的群組管理')]
    if cmd.startswith('/來源改名 '):return [ui.card('群組名稱','直接回覆新的名稱',[text],[ui.button('取消','/取消操作')],'請回覆群組的新名稱')]
    if cmd.startswith(('/來源 ','/來源暫停 ','/來源恢復 ','/來源移除確認 ')):
        selector=cmd.split(' ',1)[1];row=source(elf,selector)
        if not row:return [ui.text_message(text)]
        if cmd.startswith('/來源移除確認 '):return [ui.card('移除前確認',row['name'],['將停止你的收集，並清除這個群組在你空間內的訊息、摘要快照與待辦；不影響其他人的訂閱。'],[ui.button('保留，返回','/來源 '+selector,primary=True),ui.button('確認解除並清除','/來源移除 '+selector)],'確認解除自己的群組')]
        return [ui.card('群組管理',row['name'],[text,'目前狀態：'+('收集中' if row['enabled'] else '已暫停')],[ui.button('暫停收集' if row['enabled'] else '恢復收集',('/來源暫停 ' if row['enabled'] else '/來源恢復 ')+selector,primary=True),ui.button('修改顯示名稱','/來源改名 '+selector),ui.button('解除這個群組','/來源移除確認 '+selector),ui.button('返回我的群組','/群組')],'管理自己的群組收集')]
    if cmd=='/待回覆':
        rows=elf.db.execute('SELECT id,summary FROM tasks WHERE done=0 LIMIT 10').fetchall()
        if not rows:return [ui.card('待回覆','目前沒有待回覆事項',['先整理群組的新對話，之後可在這裡逐項標記完成。'],[ui.button('立即整理','/整理',primary=True),ui.button('我的群組','/群組')],'目前沒有待回覆事項')]
        bubbles=[ui.card('待回覆',str(i+1)+' · 待處理',[r['summary']],[ui.button('已處理這一項','/已處理 '+r['id'],primary=True),ui.button('返回待回覆','/待回覆')],'待處理：'+r['summary'])['contents'] for i,r in enumerate(rows)]
        return [{'type':'flex','altText':'你的待回覆事項，點選即可標記完成','contents':{'type':'carousel','contents':bubbles}}]
    if cmd in ('/使用設定','/每日整理 開啟','/每日整理 關閉'):
        buttons=[ui.button('Google Calendar','/行事曆',primary=True),ui.button('智慧私訊助理','/私訊助理'),ui.button('我的方案與用量','/我的方案')]
        if member:
            automatic=elf.broker.parent.db.execute('SELECT automatic FROM public_members WHERE uid=?',(elf.owner_id,)).fetchone()[0]
            buttons.append(ui.button('關閉每日摘要' if automatic else '開啟每日摘要','/每日整理 '+('關閉' if automatic else '開啟')))
        buttons += [ui.button('管理我的群組','/群組'),ui.button('資料管理','/資料管理'),ui.button('逐步使用教學','/新手教學')]
        return [ui.card('個人設定','把整理方式調成適合你。',[text],buttons,'管理日曆、群組與每日摘要')]
    if cmd=='/解除日曆確認':
        return [ui.card('解除日曆連線','要停止小精靈存取你的日曆嗎？',['只移除你自己的本機授權與尚未執行的要求，不刪除已建立的日曆活動。'],[ui.button('保留連線','/行事曆',primary=True),ui.button('確認解除我的連線','/解除日曆')],'確認解除個人日曆連線')]
    if cmd in ('/行事曆','/連接行事曆'):
        from member_calendar import calendar_card
        return [calendar_card(elf)]
    state=wizard(elf)
    if cmd=='/新增行程' or cmd.startswith(('/行程主題 ','/行程開始 ','/行程結束 ')):
        buttons=[ui.button('取消這次操作','/取消操作')]
        if state and state.get('step') in ('start','end'):
            start=state['step']=='start'
            picker={'type':'datetimepicker','label':'選擇開始時間' if start else '選擇結束時間','data':PREFIX+('/行程開始' if start else '/行程結束'),'mode':'datetime'}
            buttons.insert(0,{'type':'button','style':'primary','color':'#516D98','action':picker})
        if state and state.get('step')=='confirm':buttons.insert(0,ui.button('確認新增','/確認新增',primary=True))
        lines=[text]
        if state and state.get('title'):lines.insert(0,'主題：'+state['title'])
        if state and state.get('start'):lines.append('開始：'+state['start'][:16].replace('T',' '))
        if state and state.get('end'):lines.append('結束：'+state['end'][:16].replace('T',' '))
        return [ui.card('新增行程 · 台北時間','一步一步完成安排',lines,buttons,'新增行程：'+text)]
    return None
