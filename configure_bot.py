"""Bind incoming destination validation to your own LINE bot, without printing IDs."""
import json
from elf import load_env
from line_api import api, save_settings


def configure():
    load_env()
    info=api('GET','/v2/bot/info')
    bot_id=info.get('userId','')
    if not isinstance(bot_id,str) or not bot_id.startswith('U') or len(bot_id)!=33:
        raise ValueError('LINE did not return a valid bot user ID.')
    save_settings({'LINE_BOT_USER_ID':bot_id})
    return {'destination_validation_configured':True}


if __name__=='__main__':
    print(json.dumps(configure()))
