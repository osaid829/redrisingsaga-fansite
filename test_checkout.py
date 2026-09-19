"""HTTP integration tests: isolated DB/media, mocked gateway, no real charges."""
import hashlib
import hmac
import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import server
from commerce import CommerceStore


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        server.RATE_WINDOWS.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = CommerceStore(root / 'test.db')
        self.ebook = root / 'book.epub'
        self.ebook.write_bytes(b'PK-ebook-fixture-' * 500)
        self.audio = root / 'audio'
        self.audio.mkdir()
        (self.audio / 'chapter.mp3').write_bytes(b'audio-fixture-' * 500)
        self.ebooks = {key: [self.ebook] for key in server.CATALOG if key.endswith('-ebook')}
        self.audios = {key: {'path': self.audio} for key in server.CATALOG if key.endswith('-audio')}
        self.payment_changes = {}
        self.orders = {}
        self.calls = 0
        overrides = dict(commerce_store=self.store, PAYMENTS_ENABLED=True, WEBHOOKS_ENABLED=False,
                         RAZORPAY_KEY_SECRET='test-only-secret', EBOOK_PRODUCT_FILES=self.ebooks,
                         AUDIO_PRODUCT_FOLDERS=self.audios)
        mocks = patch.multiple(server, **overrides)
        mocks.start()
        self.addCleanup(mocks.stop)
        self.create_mock = patch.object(server, 'razorpay_request', side_effect=self.gateway_create).start()
        self.get_mock = patch.object(server, 'razorpay_get', side_effect=self.gateway_get).start()
        self.addCleanup(patch.stopall)
        self.httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.RedRisingServerHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.cookie = ''

    def gateway_create(self, endpoint, payload):
        self.calls += 1
        order = dict(payload, id=f'order_{self.calls}')
        self.orders[order['id']] = order
        return order

    def gateway_get(self, endpoint):
        if endpoint.startswith('orders/'):
            return dict(self.orders[endpoint.split('/')[1]], status='paid')
        payment_id = endpoint.split('/')[1]
        order_id = 'order_' + payment_id.split('_')[1]
        order = self.orders[order_id]
        return dict(id=payment_id, order_id=order_id, status='captured',
                    amount=order['amount'], currency=order['currency'], **{}) | self.payment_changes

    def request(self, method, path, data=None, cookie=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=10)
        request_headers = {'Content-Type': 'application/json', 'Cookie': self.cookie if cookie is None else cookie}
        request_headers.update(headers or {})
        body = json.dumps(data) if data is not None else None
        connection.request(method, path, body, request_headers)
        response = connection.getresponse()
        status, response_headers, raw = response.status, dict(response.getheaders()), response.read()
        connection.close()
        if 'Set-Cookie' in response_headers:
            self.cookie = response_headers['Set-Cookie'].split(';')[0]
        return status, response_headers, raw

    def create(self, product='red-rising-ebook', **extra):
        status, _, raw = self.request('POST', '/api/create-order', dict(product_id=product, currency='INR', **extra))
        self.assertEqual(status, 200, raw)
        return json.loads(raw)

    def proof(self, order):
        payment_id = order['order_id'].replace('order_', 'pay_')
        signature = hmac.new(b'test-only-secret', f"{order['order_id']}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return dict(razorpay_order_id=order['order_id'], razorpay_payment_id=payment_id, razorpay_signature=signature)

    def verify(self, order):
        return self.request('POST', '/api/verify-payment', self.proof(order))

    def test_checkout_without_webhook_and_exact_file_delivery(self):
        order = self.create(amount=100)
        self.assertEqual(order['amount'], 9900)
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 402)
        status, headers, raw = self.verify(order)
        self.assertEqual(status, 200, raw)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertNotIn('access_token', json.loads(raw))
        status, headers, downloaded = self.request('GET', '/api/download/red-rising-ebook')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(hashlib.sha256(downloaded).digest(), hashlib.sha256(self.ebook.read_bytes()).digest())
        self.assertEqual(self.request('GET', '/api/download/golden-son-ebook')[0], 402)

    def test_fake_signature_rejected_before_gateway_lookup(self):
        proof = self.proof(self.create())
        proof['razorpay_signature'] = 'fake'
        self.assertEqual(self.request('POST', '/api/verify-payment', proof)[0], 400)
        self.get_mock.assert_not_called()
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 402)

    def test_uncaptured_mismatched_and_refunded_payments_blocked(self):
        order = self.create()
        for change in [{'status': 'authorized'}, {'status': 'refunded'}, {'amount': 1}, {'currency': 'USD'}, {'order_id': 'other'}, {'id': 'other'}]:
            with self.subTest(change=change):
                self.payment_changes = change
                self.assertIn(self.verify(order)[0], (400, 409))
                self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 402)

    def test_pending_capture_can_be_retried_without_repaying(self):
        order = self.create()
        self.payment_changes = {'status': 'authorized'}
        self.assertEqual(self.verify(order)[0], 409)
        self.payment_changes = {}
        self.assertEqual(self.verify(order)[0], 200)
        self.assertEqual(self.calls, 1)

    def test_two_purchases_replay_and_restart_preserve_access(self):
        first = self.create()
        self.assertEqual(self.verify(first)[0], 200)
        second = self.create('golden-son-ebook')
        self.assertEqual(self.verify(second)[0], 200)
        self.assertEqual(self.verify(first)[0], 200)
        with patch.object(server, 'commerce_store', CommerceStore(self.store.database_path)):
            for product in ['red-rising-ebook', 'golden-son-ebook']:
                self.assertEqual(self.request('GET', '/api/download/' + product)[0], 200)

    def test_combo_delivers_valid_archive_with_all_products(self):
        self.assertEqual(self.verify(self.create('saga-combo'))[0], 200)
        status, _, raw = self.request('GET', '/api/download/saga-combo')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 12)
            self.assertEqual(archive.read('ebooks/red-rising-ebook/book.epub'), self.ebook.read_bytes())
        self.assertEqual(self.request('GET', '/api/audio/red-rising-audio/chapter.mp3', headers={'Range': 'bytes=0-99'})[2], (self.audio / 'chapter.mp3').read_bytes()[:100])

    def test_expired_forged_query_tokens_and_private_files_rejected(self):
        self.verify(self.create())
        with self.store.connection() as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 402)
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook?token=anything', cookie='rr_session=fake')[0], 402)
        for path in ['/.env', '/commerce.db', '/commerce.db-journal', '/server.py', '/test-pass.txt', '/CHAPTER%2001.mp3']:
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_missing_files_invalid_input_and_cross_origin_prevent_orders(self):
        self.ebook.unlink()
        self.assertEqual(self.request('POST', '/api/create-order', {'product_id': 'red-rising-ebook'})[0], 503)
        self.assertEqual(self.request('POST', '/api/create-order', {'product_id': []})[0], 400)
        self.assertEqual(self.request('POST', '/api/create-order', {'product_id': 'red-rising-audio'}, headers={'Origin': 'https://attacker.invalid'})[0], 403)
        self.create_mock.assert_not_called()

    def test_webhook_rejects_forgery_and_deduplicates_fulfilment(self):
        order = self.create()
        payment = self.gateway_get('payments/pay_1')
        event = {'event': 'payment.captured', 'payload': {'payment': {'entity': payment}}}
        raw = json.dumps(event).encode()
        signature = hmac.new(b'webhook-test', raw, hashlib.sha256).hexdigest()
        with patch.multiple(server, WEBHOOKS_ENABLED=True, RAZORPAY_WEBHOOK_SECRET='webhook-test'):
            self.assertEqual(self.request('POST', '/api/webhooks/razorpay', event,
                                         headers={'X-Razorpay-Signature': 'bad'})[0], 400)
            headers = {'X-Razorpay-Signature': signature, 'X-Razorpay-Event-Id': 'event_test'}
            self.assertEqual(self.request('POST', '/api/webhooks/razorpay', event, headers=headers)[0], 200)
            self.assertEqual(json.loads(self.request('POST', '/api/webhooks/razorpay', event, headers=headers)[2])['status'], 'duplicate')
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM entitlements').fetchone()[0], 1)
        # A captured webhook restores this browser even if the checkout callback
        # was lost. Another browser cannot claim it by knowing the order ID.
        self.assertEqual(json.loads(self.request('GET', '/api/purchases')[2])['products'], ['red-rising-ebook'])
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook', cookie='')[0], 402)
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 200)
        self.assertEqual(self.verify(order)[0], 200)
        self.assertEqual(self.request('GET', '/api/download/red-rising-ebook')[0], 200)

    def test_actual_project_ebook_bytes_reach_customer(self):
        actual = server.BASE_DIR / '1_Red_Rising_-_Pierce_Brown.epub'
        if not actual.is_file():
            self.skipTest('Local ebook is not available in this checkout')
        with patch.dict(server.EBOOK_PRODUCT_FILES, {'red-rising-ebook': [actual]}):
            self.assertEqual(self.verify(self.create())[0], 200)
            status, headers, raw = self.request('GET', '/api/download/red-rising-ebook')
        self.assertEqual(status, 200)
        self.assertEqual(int(headers['Content-Length']), actual.stat().st_size)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(actual.read_bytes()).hexdigest())
        with zipfile.ZipFile(io.BytesIO(raw)) as ebook:
            self.assertIsNone(ebook.testzip())


if __name__ == '__main__':
    unittest.main()
