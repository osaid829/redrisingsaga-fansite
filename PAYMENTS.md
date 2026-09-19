# Payment setup

The local server supports Razorpay orders, captured-payment verification, durable
SQLite entitlements, signed webhook processing, and expiring `HttpOnly` browser
sessions. It never grants access from a browser request alone.

For local test-mode checkout, set the Razorpay key ID and secret in the ignored
`.env` file:

```sh
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
```

Start the server with `python3 server.py`. Configure Razorpay test webhooks at
`/api/webhooks/razorpay` for `payment.captured` and `order.paid` before public
launch. Set its generated secret as `RAZORPAY_WEBHOOK_SECRET`; webhook processing
is disabled until that value is present.

Run `python3 -B -m unittest -v test_server test_checkout` to test the real HTTP
routes with an isolated SQLite database and mocked Razorpay responses. Tests cover
checkout without webhooks, captured-payment verification, tampered prices,
invalid signatures, wrong currency/order, expired sessions, repeat purchases,
webhook retries, denied downloads, and byte-identical local ebook delivery.

The browser retries pending capture and retains the callback proof in sessionStorage
until verification succeeds, allowing a same-tab reload to retry without paying again.
Purchased products display a Download purchase button for the current browser's
30-day session. Orders are bound to that HttpOnly session before checkout. Once
a verified webhook records a captured payment, reloading the original browser
restores access even when the checkout callback was lost. An unpaid order alone
never grants download access. Clearing cookies or switching devices requires manual purchase
recovery; customer accounts/email recovery are not implemented. Configure webhooks
before public launch so payments can be recorded even if the browser closes.

Vercel deliberately disables this commerce backend: its filesystem cannot safely
persist SQLite orders or sessions, and the media library is too large for static
hosting. Production needs a managed database plus private object storage that issues
short-lived download URLs.

## Development checks

Use `npm ci` followed by `npm run check` for strict JavaScript type checking and
the isolated Python integration suite. `npm audit` checks known dependency
advisories. `python3 test_customer_flow.py` runs only the isolated checkout suite;
it never creates live Razorpay orders. The optional actual-EPUB test is skipped
in CI when the paid file is absent.

## Production dependencies still required

1. Managed database for durable orders, entitlements, and sessions.
2. Private object storage for ebooks, audiobooks, and prebuilt combo archives,
   with short-lived signed download URLs after entitlement checks.
3. Razorpay test-mode end-to-end verification, then a configured public HTTPS
   webhook and live credentials.
4. Customer recovery across devices (authenticated accounts or emailed links)
   and refund/dispute entitlement handling.

Do not upload `.env`, SQLite files, or paid media into GitHub or a public static
deployment. No Vercel production payment deployment is configured yet.
