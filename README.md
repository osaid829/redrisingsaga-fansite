# Red Rising Saga fan site

An unofficial, non-commercial personal fan project inspired by Pierce Brown's
*Red Rising* saga. The Vercel deployment is a static reader's guide that links
visitors to the [official saga page](https://www.piercebrown.com/redrisingsaga).
It does not host, sell, or distribute ebooks or audiobooks.

## Local development

```sh
npm ci
npm run check
python3 server.py
```

The local Python server supports an isolated Razorpay integration test harness
for development only. Do not use it to distribute media unless you own the
necessary rights and have configured secure production storage, a managed
database, and verified payment webhooks.

## Deployment

Vercel deploys the static fan edition from `index.html` and `vercel.json`.
No payment credentials, local database, ebook, or audiobook files are included.
