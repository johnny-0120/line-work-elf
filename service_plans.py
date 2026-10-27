"""Current free service policy. No checkout, paid grants or payment webhooks are enabled."""
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class ServicePlan:
    code: str='free'
    label: str='免費版'
    price_minor: int=0
    payments_enabled: bool=False
    max_groups: int=5
    conversation_chars: int=12000
    conversation_parts: int=30

FREE=ServicePlan()

def current_plan(tenant=None):
    # The client/model cannot choose a plan. A future verified billing adapter
    # must be implemented before any paid entitlement can be granted.
    return FREE

def card(elf):
    import onboarding as ui
    plan=current_plan(getattr(elf,'member_key',None))
    lines=['目前免費使用，不需要付款或填信用卡。',
           '功能：群組整理、雙人對話整理、個人記錄、待辦、選用的智慧私訊與 Google Calendar。',
           '每人最多 '+str(plan.max_groups)+' 個群組；每份手動對話最多 '+str(plan.conversation_parts)+' 段、合計 '+str(plan.conversation_chars)+' 字。']
    if hasattr(elf,'member_key'):
        used=elf.broker.used(elf.member_key)
        lines.append('今日 AI 處理用量：'+str(used)+' / '+str(elf.broker.user_limit)+' 次。包含整理與智慧私訊，台北時間每天08:00重置；服務總用量達上限時也會暫停新的 AI 處理。')
    lines.append('目前沒有啟用收費。若未來推出付費方案，會另行說明並由你主動訂購。')
    return ui.card('我的方案與用量',plan.label,lines,[ui.button('開始使用','/開始使用',primary=True),ui.button('使用設定','/使用設定')],'目前免費使用，查看可用功能與用量')
