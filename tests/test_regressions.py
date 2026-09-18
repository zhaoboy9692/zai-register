import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from contextlib import ExitStack

from zai.results import ResultStore, PersistenceError
from zai.register import register_one, register_batch
from zai.captcha import CaptchaSolver


class ProgressTests(unittest.TestCase):
    def test_cli_uses_incremental_store(self):
        import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cli.json'
            def fake_batch(**kwargs):
                row = dict(attempt_id='cli-test', email='cli@example.com', status='complete', token='dummy')
                kwargs['on_progress'](row)
                self.assertTrue(path.exists())
                return [row]
            with patch('sys.argv', ['main.py', '--temp-mail', '--count', '1', '--output', str(path)]), patch('main.register_batch', side_effect=fake_batch):
                main.main()
            self.assertEqual(len(json.loads(path.read_text())), 1)

    def test_batch_saved_before_next_account_and_survives_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.json'
            path.write_text('[{"email":"existing@example.com","status":"complete"}]')
            store = ResultStore(path)
            calls = []

            def fake_one(**kwargs):
                address = kwargs['email_addr']
                if calls:
                    saved = json.loads(path.read_text())
                    self.assertEqual(saved[-1]['status'], 'complete')
                    self.assertIn('a@example.com', path.with_suffix('.txt').read_text())
                calls.append(address)
                data = dict(email=address, password='test-only', status='pending')
                kwargs['on_progress'](data)
                if len(calls) == 2:
                    raise KeyboardInterrupt
                return dict(data, status='complete', token='test-token')

            with patch('zai.register._register_one', side_effect=fake_one), patch('zai.register.time.sleep'):
                with self.assertRaises(KeyboardInterrupt):
                    register_batch(emails=['a@example.com', 'b@example.com'], on_progress=store.save)
            saved = json.loads(path.read_text())
            self.assertEqual([r['status'] for r in saved], ['complete', 'complete', 'interrupted'])
            self.assertEqual(saved[1]['token'], 'test-token')
            self.assertEqual(len(ResultStore(path).records), 3)

    def test_exception_preserves_current_address(self):
        events = []
        def fail(**kwargs):
            kwargs['on_progress'](dict(email='x@example.com', password='test', status='signup_done'))
            raise RuntimeError('Browser closed')
        with patch('zai.register._register_one', side_effect=fail):
            result = register_one(on_progress=events.append)
        self.assertEqual(result['email'], 'x@example.com')
        self.assertEqual(events[-1]['status'], 'failed')

    def test_save_failure_stops_work(self):
        def fail(**kwargs):
            kwargs['on_progress']({'email': 'x@example.com'})
        def broken_save(data):
            raise PersistenceError('disk full')
        with patch('zai.register._register_one', side_effect=fail):
            with self.assertRaises(PersistenceError):
                register_one(on_progress=broken_save)

    def test_atomic_failure_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'results.json'
            path.write_text('[]')
            store = ResultStore(path)
            with patch('zai.results.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(PersistenceError):
                    store.save({'status': 'complete'})
            self.assertEqual(path.read_text(), '[]')


class BrowserLoopTests(unittest.TestCase):
    def run_loop(self, *, closed=False, redirected=False, passed=False, visible=False,
                 response=False, error_response=False):
        clock = [0.0]
        page = MagicMock()
        page.url = 'https://chat.z.ai/' if redirected else 'https://chat.z.ai/auth?action=signup'
        page.is_closed.return_value = closed
        handlers = {}
        page.on.side_effect = lambda event, handler: handlers.update({event: handler})
        sent = [False]
        def wait(ms):
            clock[0] += ms / 1000
            if (response or error_response) and clock[0] >= 8 and not sent[0]:
                sent[0] = True
                resp = MagicMock()
                resp.url = 'https://chat.z.ai/api/v1/auths/signup'
                resp.request.method = 'POST'
                resp.request.post_data = '{"captcha_verify_param":"dummy"}'
                resp.status = 400 if error_response else 200
                resp.json.return_value = {'success': not error_response}
                handlers['response'](resp)
        page.wait_for_timeout.side_effect = wait
        browser = MagicMock()
        browser.is_connected.return_value = True
        browser.new_context.return_value.new_page.return_value = page
        runtime = MagicMock()
        runtime.__enter__.return_value.chromium.launch.return_value = browser
        solver = CaptchaSolver()
        with ExitStack() as stack:
            stack.enter_context(patch('playwright.sync_api.sync_playwright', return_value=runtime))
            stack.enter_context(patch('zai.captcha.time.monotonic', side_effect=lambda: clock[0]))
            stack.enter_context(patch('zai.captcha._bring_window_to_front', return_value=True))
            stack.enter_context(patch.object(solver, '_fill_form'))
            stack.enter_context(patch.object(solver, '_check_captcha_passed', return_value=passed))
            stack.enter_context(patch.object(solver, '_captcha_visible', return_value=visible))
            trigger = stack.enter_context(patch.object(solver, '_trigger_captcha'))
            stack.enter_context(patch.object(solver, '_click_submit', side_effect=lambda _: not passed))
            try:
                result = solver.solve('dummy@example.com', 'test-only', 'dummy', timeout=90)
                self.assertEqual(result[1], {'success': True})
            finally:
                self.assertLessEqual(trigger.call_count, 4)  # initial attempt + 3 retries
                browser.close.assert_called_once()

    def test_missing_popup_is_bounded(self):
        with self.assertRaisesRegex(RuntimeError, '弹窗仍未出现'):
            self.run_loop()

    def test_closed_window_stops_and_closes_browser(self):
        with self.assertRaisesRegex(RuntimeError, '窗口已关闭'):
            self.run_loop(closed=True)

    def test_redirect_stops(self):
        with self.assertRaisesRegex(RuntimeError, '离开'):
            self.run_loop(redirected=True)

    def test_missing_submit_stops(self):
        with self.assertRaisesRegex(RuntimeError, '找不到提交按钮'):
            self.run_loop(passed=True)

    def test_visible_challenge_waits_for_human_until_timeout(self):
        with self.assertRaises(TimeoutError):
            self.run_loop(visible=True)

    def test_response_processed_during_wait(self):
        self.run_loop(visible=True, response=True)

    def test_rejected_response_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, '拒绝请求'):
            self.run_loop(visible=True, error_response=True)


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            channel=os.environ.get('TEST_BROWSER_CHANNEL') or None, headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_empty_hidden_and_success_states(self):
        page = self.browser.new_page()
        solver = CaptchaSolver()
        try:
            for html in ['<div id="captcha-element"></div>',
                         '<div id="captcha-element">点击开始验证</div>',
                         '<div id="captcha-element" style="display:none">验证通过</div>',
                         '<p>验证成功</p>']:
                page.set_content(html)
                self.assertFalse(solver._check_captcha_passed(page), html)
            for label in ['滑动成功!', '验证通过', 'Verification successful']:
                page.set_content(f'<div id="captcha-element">{label}</div>')
                self.assertTrue(solver._check_captcha_passed(page))
        finally:
            page.close()

    def test_trigger_opens_local_dialog_and_submit_result(self):
        page = self.browser.new_page()
        try:
            page.set_content('''<button id="captcha-element" onclick="document.querySelector('#aliyunCaptcha-window-float').style.display='block'">开始验证</button>
                <div id="aliyunCaptcha-window-float" style="display:none">请拖动滑块</div>''')
            solver = CaptchaSolver()
            self.assertFalse(solver._captcha_visible(page))
            self.assertTrue(solver._trigger_captcha(page))
            self.assertTrue(solver._captcha_visible(page))
            self.assertFalse(solver._check_captcha_passed(page))
            self.assertFalse(solver._click_submit(page))
            page.set_content('<button type="submit">创建账号</button>')
            self.assertTrue(solver._click_submit(page))
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
