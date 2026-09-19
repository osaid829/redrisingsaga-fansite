# Razorpay launch checklist

The site is intentionally in safe simulation mode until both `RAZORPAY_KEY_ID` and
`RAZORPAY_KEY_SECRET` are supplied as environment variables. Do not put either
secret in HTML or JavaScript.

```sh
export RAZORPAY_KEY_ID='rzp_test_...'
export RAZORPAY_KEY_SECRET='...'
export RAZORPAY_WEBHOOK_SECRET='...'
python3 server.py
```

The storefront offers INR for domestic orders and USD for international orders. Both
amounts are fixed in the server catalog; the browser cannot set an amount or an
arbitrary currency. International cards/currencies must be enabled for your Razorpay
account before using the USD option.

Use Razorpay test keys and test payment methods first. Configure a Razorpay webhook
at `/api/webhooks/razorpay` and verify its secret. The endpoint validates webhook
signatures, but it deliberately does not fulfil a purchase: connect it to a durable
database/fulfilment service before production so events survive server restarts and
are processed idempotently.

Before accepting payments, keep the app behind HTTPS, use live keys only in the
deployment environment, and confirm you have distribution rights for every EPUB,
audiobook, image, and product being sold. Static files remain public by design;
paid files need authenticated, expiring download delivery.
