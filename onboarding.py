"""LINE-native welcome cards and actions. Pure builders: no user-data access."""
import os
import re
from urllib.parse import quote

SITE='https://johnny-0120.github.io/line-work-elf/'
BLUE='#6F9BFF'
AMBER='#FFBD59'
INK='#111722'


def action(label,text):
    return {'type':'message','label':label,'text':text}


def chat_url():
    basic=os.getenv('PUBLIC_BOT_BASIC_ID','')
    return 'https://line.me/R/oaMessage/'+quote(basic,safe='')+'/?'+quote('/開始使用',safe='') if basic else SITE


def quick(items=None):
    items=items or [('綁定工作群','/綁定群組'),('立即整理','/整理'),('我的群組','/群組'),('使用設定','/使用設定')]
    return {'items':[{'type':'action','action':action(label,command)} for label,command in items]}


def text_message(text,items=None):
    return {'type':'text','text':text[:4800],'quickReply':quick(items)}


def button(label,command=None,uri=None,clipboard=None,primary=False):
    act=({'type':'uri','label':label,'uri':uri} if uri else
         {'type':'clipboard','label':label,'clipboardText':clipboard} if clipboard else action(label,command))
    return {'type':'button','style':'primary' if primary else 'secondary','height':'sm',
            'color':'#3659B8' if primary else '#DCE6FA','action':act}


def card(label,title,paragraphs,buttons,alt):
    contents=[{'type':'text','text':label,'color':AMBER,'size':'xs','weight':'bold'},
              {'type':'text','text':title,'color':'#FFFFFF','size':'xl','weight':'bold','wrap':True,'margin':'md'}]
    for paragraph in paragraphs:
        contents.append({'type':'text','text':paragraph,'color':'#CAD4E3','size':'sm','wrap':True,'margin':'lg'})
    return {'type':'flex','altText':alt[:400],
            'contents':{'type':'bubble','size':'mega',
                'body':{'type':'box','layout':'vertical','backgroundColor':INK,'paddingAll':'22px','contents':contents},
                'footer':{'type':'box','layout':'vertical','backgroundColor':INK,'spacing':'sm','paddingAll':'18px','contents':buttons}}}


def welcome():
    return card('LINE WORK ELF · 歡迎','群組聊完了，\n我幫你接住重點。',
        ['整理討論內容、待回覆與下一步，結果只私訊你。',
         '① 建立你的整理空間\n② 邀我入群，貼上綁定指令\n③ 回私訊按「立即整理」',
         '每人獨立資料庫。只讀你已綁定群組的新文字，不會把大家的內容混在一起。'],
        [button('了解並開始','/開始使用',primary=True),button('先看看怎麼用','/邀請教學')],
        '歡迎使用公事小精靈！按「了解並開始」，建立自己的群組整理空間。')


def consent():
    return card('開始之前','你的資料，怎麼整理？',
        ['群組文字會經 OpenRouter 與免費模型供應者分析。請先告知並取得群組成員同意；不要加入敏感對話。',
         '每人分開儲存訊息、摘要與待辦，摘要只傳本人。一般私訊不交給 AI，也不會讀取管理者的日曆或筆記。',
         '只收集綁定後的新文字。「私事」或 #private 開頭會排除，其餘分類仍可能有誤。訊息約保留 7 天，待辦約 30 天。',
         '服務管理者仍能管理本機檔案，這不是端對端加密；可隨時停止收集或刪除自己的資料。'],
        [button('同意並建立我的空間','/同意並開始',primary=True),button('閱讀完整資料說明',uri=SITE+'privacy.html')],
        '開始前請確認資料用途：按「同意並建立我的空間」才會開通，群組仍需你本人綁定。')


def pairing(reply):
    match=re.search(r'^/綁定群組 ([0-9A-F]{10})$',reply,re.M)
    if not match:return text_message(reply)
    command='/綁定群組 '+match[1]
    return card('下一步 · 綁定工作群','把這行貼到你的群組',
        ['① 先邀請「公事小精靈」加入目標工作群。\n② 按下方「複製綁定指令」。\n③ 由你本人貼到該群組並送出。',
         command,'代碼 10 分鐘內有效，只能由你本人使用一次。每人最多綁定 5 群；不會讀取綁定前的歷史訊息。'],
        [button('複製綁定指令',clipboard=command,primary=True),button('如何邀請我入群','/邀請教學'),button('完成了，查看我的群組','/群組')],
        '請複製這行並由你本人貼到目標工作群：'+command+'（10 分鐘有效）。')


def invitation(group=False):
    buttons=([button('開啟私訊，建立自己的空間',uri=chat_url(),primary=True)] if group else
             [button('我已邀請入群，取得綁定指令','/綁定群組',primary=True)])
    return card('每位成員，各自開通','同一隻小精靈，\n各自整理自己的工作。' if group else '怎麼把我加入群組？',
        ['① 先加我為好友。\n② 開啟目標群組的成員邀請，從好友選擇「公事小精靈」。\n③ 回到我的私訊，按「綁定工作群」，再把指令貼回群組。',
         '先向群組成員說明會使用外部 AI。每個想收摘要的人，都要用自己的 LINE 私訊開通並綁定；別人代貼代碼無效。',
         '只收集綁定後的新文字，摘要私訊本人，不會貼在群裡。手機可按下方開啟私訊；電腦版可直接點我的頭像進入聊天。'],
        buttons,'小精靈已加入群組！每位想整理的成員請各自開啟我的私訊，按「開始使用」；尚未自動收集任何人的訊息。')


def dashboard(groups):
    enabled=sum(bool(x['enabled']) for x in groups)
    return card('你的整理空間','下一步，從這裡開始。',
        ([f'你已綁定 {len(groups)} 個群組，其中 {enabled} 個收集中。',
          '群裡有新討論後，按「立即整理」。摘要、待回覆與下一步只會私訊你。'] if groups else
         ['你的空間已準備好，目前還沒有綁定群組。','先按「綁定工作群」，把取得的指令由你本人貼到已加入我的工作群。']),
        [button('綁定工作群','/綁定群組',primary=True),button('立即整理','/整理'),button('我的群組','/群組'),button('使用設定','/使用設定')],
        '你的獨立整理空間已準備好。'+('按「立即整理」查看已綁定群組的新討論。' if groups else '按「綁定工作群」完成下一步。'))


def group_list(groups):
    lines=[f"{'收集中' if x['enabled'] else '已暫停'}｜{x['name']}" for x in groups]
    return card('只有你自己的來源','我的工作群',
        ['\n'.join(lines) if lines else '目前還沒有綁定群組。',
         '各人各自綁定，互不影響。同群也只會讀取你綁定後的新訊息。',
         '暫停請到該群傳 /暫停收集；移除可私訊 /解除群組 完整名稱。'],
        [button('綁定另一個群組','/綁定群組',primary=True),button('整理我的新訊息','/整理'),button('查看使用狀態','/狀態')],
        '你的工作群：'+('；'.join(lines) if lines else '尚未綁定，按「綁定另一個群組」開始。'))


def settings(automatic):
    return card('只變更你自己的設定','想什麼時候收到整理？',
        ['每日摘要：'+('已開啟' if automatic else '目前關閉'),
         '開啟後，每天台北時間 21:00 後，有新訊息且額度足夠才會私訊你。也可隨時手動整理。'],
        [button('關閉每日摘要' if automatic else '開啟每日摘要','/每日整理 '+('關閉' if automatic else '開啟'),primary=True),
         button('查看我的狀態與額度','/狀態'),button('資料管理','/資料管理'),button('如何邀請我入群','/邀請教學')],
        '個人設定：每日摘要目前'+('開啟' if automatic else '關閉')+'，只變更你自己的設定。')


def data_controls():
    return card('資料管理','要暫停，還是停止使用？',
        ['單一群組：在該群傳 /暫停收集，或私訊 /解除群組 完整名稱。',
         '按「清除我的資料並停止使用」會刪除你本機空間內的群組綁定、訊息、摘要與待辦，不影響其他使用者。',
         '已送出的 LINE 訊息、第三方紀錄與既有短期備份不會因此撤回；防濫用計數仍短期保留。'],
        [button('返回使用設定','/使用設定',primary=True),button('清除我的資料並停止使用','/刪除我的資料')],
        '資料管理：你可移除單一來源，或明確按「清除我的資料並停止使用」刪除自己的本機空間。')
