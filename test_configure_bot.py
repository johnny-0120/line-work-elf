import unittest
from unittest.mock import patch
import configure_bot


class ConfigureTests(unittest.TestCase):
    @patch.object(configure_bot,'load_env')
    @patch.object(configure_bot,'save_settings')
    @patch.object(configure_bot,'api',return_value={'userId':'U'+'a'*32})
    def test_configures_bot_without_disclosing_identifier(self,api,save,load):
        result=configure_bot.configure()
        self.assertEqual(result,{'destination_validation_configured':True})
        api.assert_called_once_with('GET','/v2/bot/info')
        save.assert_called_once_with({'LINE_BOT_USER_ID':'U'+'a'*32})

    @patch.object(configure_bot,'load_env')
    @patch.object(configure_bot,'save_settings')
    @patch.object(configure_bot,'api',return_value={'userId':'not-a-bot'})
    def test_invalid_response_does_not_change_configuration(self,api,save,load):
        with self.assertRaises(ValueError): configure_bot.configure()
        save.assert_not_called()
