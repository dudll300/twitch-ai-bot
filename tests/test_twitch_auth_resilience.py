"""Real OAuth parsing and retry classification, with urlopen as the boundary."""

import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import urllib.error
from urllib.parse import parse_qs

import bot
import rewards
import twitch_auth as auth
from test_autonomous_privacy_regression import CFG


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def failure(code, payload=None):
    return urllib.error.HTTPError('https://id.twitch.tv/oauth2/validate', code, 'synthetic', {},
                                  response(payload or {'message': 'SYNTHETIC_SECRET_ERROR'}))


def identity(**changes):
    return dict(client_id='client', login='helper', user_id='123', scopes=sorted(auth.SCOPES),
                expires_in=3600, **changes)


class AuthResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tokens.json'
        self.original = json.dumps(dict(access_token='cached-token', refresh_token='cached-refresh'))
        self.path.write_text(self.original)

    def test_invalid_validation_body_never_authorizes_refreshes_or_rewrites_cache(self):
        payloads = [b'{broken', b'null', b'[]', b'{}',
            json.dumps({**identity(), 'scopes': 'chat:read'}).encode(),
            json.dumps({**identity(), 'expires_in': True}).encode()]
        for payload in payloads:
            with self.subTest(payload=payload), patch('urllib.request.urlopen', return_value=io.BytesIO(payload)) as http, \
                    patch.object(auth, 'authorize_device') as device:
                with self.assertRaises(auth.TemporaryTwitchError):
                    auth.get_access_token('client', 'helper', self.path)
                self.assertEqual(http.call_count, 1)
                device.assert_not_called()
                self.assertEqual(self.path.read_text(), self.original)

    def test_http_and_network_errors_are_temporary_without_exposing_response(self):
        for error in [failure(code) for code in (408, 429, 500, 502, 503, 504)] + [
                urllib.error.URLError('SYNTHETIC_SECRET_ERROR'), ConnectionResetError('SYNTHETIC_SECRET_ERROR')]:
            with self.subTest(error=type(error).__name__), patch('urllib.request.urlopen', side_effect=error), \
                    patch.object(auth, 'authorize_device') as device:
                with self.assertRaises(auth.TemporaryTwitchError) as caught:
                    auth.get_access_token('client', 'helper', self.path)
                device.assert_not_called()
                self.assertNotIn('SYNTHETIC_SECRET_ERROR', str(caught.exception))
                self.assertEqual(self.path.read_text(), self.original)
        self.assertIs(rewards.TemporaryTwitchError, auth.TemporaryTwitchError)

    def test_confirmed_401_is_distinct_from_terminal_403(self):
        with patch('urllib.request.urlopen', side_effect=failure(401)):
            self.assertIsNone(auth.validate('cached-token'))
        with patch('urllib.request.urlopen', side_effect=failure(403)), patch.object(auth, 'authorize_device') as device:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                auth.get_access_token('client', 'helper', self.path)
            device.assert_not_called()

    def test_invalid_refresh_body_preserves_original_cache_and_does_not_open_oauth(self):
        for body in (b'{broken', b'{}', b'{"access_token":123}', b'{"access_token":"new","refresh_token":7}'):
            with self.subTest(body=body), patch('urllib.request.urlopen', side_effect=[failure(401), io.BytesIO(body)]) as http, \
                    patch.object(auth, 'authorize_device') as device:
                with self.assertRaises(auth.TemporaryTwitchError):
                    auth.get_access_token('client', 'helper', self.path)
                self.assertEqual(http.call_count, 2)
                self.assertEqual(self.path.read_text(), self.original)
                device.assert_not_called()

    def test_valid_rotated_refresh_is_retained_when_later_validation_is_temporarily_broken(self):
        fresh = dict(access_token='new-token', refresh_token='rotated-refresh')
        with patch('urllib.request.urlopen', side_effect=[failure(401), response(fresh), io.BytesIO(b'{broken')]), \
                patch.object(auth, 'authorize_device') as device:
            with self.assertRaises(auth.TemporaryTwitchError):
                auth.get_access_token('client', 'helper', self.path)
            device.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), fresh)

    def test_successful_refresh_without_rotation_retains_existing_refresh_token(self):
        with patch('urllib.request.urlopen', side_effect=[failure(401), response(dict(access_token='new-token')), response(identity())]):
            self.assertEqual(auth.get_access_token('client', 'helper', self.path), 'new-token')
        self.assertEqual(json.loads(self.path.read_text())['refresh_token'], 'cached-refresh')

    def test_new_grant_survives_broken_validation_without_a_second_device_flow(self):
        self.path.unlink()
        fresh = dict(access_token='new-token', refresh_token='new-refresh')
        with patch.object(auth, 'authorize_device', return_value=fresh) as device, \
                patch('urllib.request.urlopen', side_effect=[io.BytesIO(b'{broken'), response(identity())]):
            with self.assertRaises(auth.TemporaryTwitchError):
                auth.get_access_token('client', 'helper', self.path)
            self.assertEqual(auth.get_access_token('client', 'helper', self.path), 'new-token')
            device.assert_called_once()

    def test_device_poll_retries_bad_json_on_same_grant_without_opening_another_flow(self):
        device = dict(verification_uri='https://www.twitch.tv/activate', user_code='synthetic-code',
                      device_code='synthetic-device', interval=5, expires_in=60)
        pending = failure(400, dict(message='authorization_pending'))
        with patch('urllib.request.urlopen', side_effect=[response(device), io.BytesIO(b'{broken'), pending,
                response(dict(access_token='new-token', refresh_token='new-refresh'))]) as http, \
                patch.object(auth.webbrowser, 'open') as browser, patch.object(auth.time, 'sleep') as sleep, \
                patch.object(auth.time, 'monotonic', side_effect=[0, 1, 2, 3]):
            tokens = auth.authorize_device('client', auth.SCOPES, 'helper')
        self.assertEqual(tokens['access_token'], 'new-token')
        self.assertEqual(http.call_count, 4)
        browser.assert_called_once()
        self.assertEqual(sleep.call_count, 3)
        for call in http.call_args_list[1:]:
            self.assertEqual(parse_qs(call.args[0].data.decode())['device_code'], ['synthetic-device'])

    def test_device_poll_retains_grant_after_network_timeout_reset_and_temporary_http(self):
        device = dict(verification_uri='https://www.twitch.tv/activate', user_code='synthetic-code',
                      device_code='synthetic-device', interval=5, expires_in=60)
        for error in (urllib.error.URLError('SYNTHETIC_SECRET_ERROR'), TimeoutError('SYNTHETIC_SECRET_ERROR'),
                      ConnectionResetError('SYNTHETIC_SECRET_ERROR'), failure(429), failure(503)):
            with self.subTest(error=type(error).__name__), \
                    patch('urllib.request.urlopen', side_effect=[response(device), error,
                          response(dict(access_token='new-token'))]) as http, \
                    patch.object(auth.webbrowser, 'open') as browser, patch.object(auth.time, 'sleep'), \
                    patch.object(auth.time, 'monotonic', side_effect=[0, 1, 2]):
                self.assertEqual(auth.authorize_device('client', auth.SCOPES, 'helper')['access_token'], 'new-token')
                self.assertEqual(http.call_count, 3)
                browser.assert_called_once()
                for call in http.call_args_list[1:]:
                    self.assertEqual(parse_qs(call.args[0].data.decode())['device_code'], ['synthetic-device'])


class AuthReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_reuses_existing_reconnect_loop_after_corrupt_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / '.twitch_token.json'
            original = json.dumps(dict(access_token='cached-token', refresh_token='cached-refresh'))
            path.write_text(original)
            with patch.object(bot, 'ROOT', root):
                instance = bot.Bot(dict(CFG, TWITCH_BOT_NAME='helper', TWITCH_CLIENT_ID='client',
                                        TWITCH_REWARD_TITLE='Synthetic reward'))
                listener = AsyncMock()
                with patch('urllib.request.urlopen', side_effect=[io.BytesIO(b'{broken'), response(identity())]) as http, \
                        patch.object(auth, 'authorize_device') as device, \
                        patch.object(bot, 'RewardListener', return_value=listener), \
                        patch.object(bot.asyncio, 'open_connection', side_effect=asyncio.CancelledError) as irc, \
                        patch.object(bot.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
                    with self.assertRaises(asyncio.CancelledError):
                        await instance.run()
                self.assertEqual(http.call_count, 2)
                sleep.assert_awaited_once_with(5)
                listener.prepare.assert_awaited_once()
                irc.assert_awaited_once()
                device.assert_not_called()


if __name__ == '__main__':
    unittest.main()
