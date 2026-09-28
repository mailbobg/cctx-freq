import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('panel_runtime',Path(__file__).with_name('panel.py'))
panel=importlib.util.module_from_spec(spec);spec.loader.exec_module(panel)


class RuntimeTests(unittest.TestCase):
    def test_panel_keeps_portfolio_counts_when_frequi_counts_legs(self):
        bot={'name':'funding-spread','strategy':'CrossVenueFundingV1','bot_name':'funding-spread',
             'port':8086,'description':'paper','runtime':'cross_venue_paper'}
        response={'show_config':{'state':'running','dry_run':True,'max_open_trades':2,'poll_seconds':30},
                  'portfolio':{'open_trades':1,'closed_trade_count':3,'profit_all_coin':12.,'profit_closed_coin':10.,
                               'winrate':2/3,'positions':[],'health':{'note':'holding'}},
                  'daily?timescale=14&scope=portfolio':{'data':[]}}
        with patch.object(panel,'find_process',return_value=None),patch.object(panel,'load_state',return_value={}), \
             patch.object(panel,'bot_api',side_effect=lambda b,path:response[path]):
            result=panel.status(bot)
            self.assertEqual(result['api'],'up')
            self.assertEqual(result['open_trades'],1);self.assertEqual(result['max_trades'],1)
            self.assertEqual(result['closed_trades'],3);self.assertEqual(result['profit_all'],12.)
    def test_explicit_start_and_restart_resume_paper_entries(self):
        bot={'runtime':'cross_venue_paper'}
        for action in ['start','restart']:
            with patch.object(panel,'bots',return_value={'paper':bot}), \
                 patch.object(panel,'start_bot'),patch.object(panel,'stop_bot'), \
                 patch.object(panel,'wait_api',return_value=True),patch.object(panel,'_bot_post') as post:
                panel.act('paper',action)
                post.assert_called_once_with(bot,'start')
    def test_paper_service_is_registered_in_frequi_login(self):
        bot={'name':'funding-spread','bot_name':'funding-spread','port':8086,'runtime':'cross_venue_paper'}
        with patch.object(panel,'bots',return_value={'paper':bot}), \
             patch.object(panel,'credentials',return_value=('test','secret')), \
             patch.object(panel.requests,'post') as request:
            request.return_value.json.return_value={'access_token':'scoped-access','refresh_token':'scoped-refresh'}
            result=panel._frequi_logins()
            self.assertEqual(result['ccxt.funding-spread']['apiUrl'],'http://127.0.0.1:8086')
            self.assertEqual(result['ccxt.funding-spread']['accessToken'],'scoped-access')
            request.assert_called_once()

    def test_native_command_is_preserved(self):
        bot={'name':'unlock-short','config':panel.ROOT/'user_data/bots/unlock-short.json','runtime':'freqtrade'}
        cmd=panel.command_for_bot(bot)
        self.assertEqual(cmd[:4],[str(panel.FREQTRADE),'trade','-c','user_data/bots/unlock-short.json'])

    def test_cross_venue_command_is_not_a_single_exchange_bot(self):
        bot={'name':'funding-spread','config':panel.ROOT/'user_data/bots/funding-spread.json','runtime':'cross_venue_paper'}
        cmd=panel.command_for_bot(bot)
        self.assertTrue(cmd[1].endswith('cross_venue/service.py'))
        self.assertNotIn('trade',cmd)
        self.assertIn('user_data/bots/funding-spread.json',cmd)

    def test_unknown_runtime_rejects_launch(self):
        with self.assertRaises(ValueError):panel.command_for_bot({'runtime':'other'})


if __name__=='__main__':unittest.main()
