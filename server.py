#!/usr/bin/env python3
"""Local storefront server with durable Razorpay order fulfilment.

Commerce is intentionally disabled on Vercel because its filesystem is not a
durable database and the protected media does not fit Vercel static hosting.
"""

import base64
import http.cookies
import hashlib
import hmac
import http.server
import io
import json
import os
import secrets
import shutil
import tempfile
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from commerce import CommerceStore, SESSION_TTL_SECONDS

PORT = int(os.environ.get("PORT", "8000"))
BASE_DIR = Path(__file__).parent.resolve()


def load_dotenv(dotenv_path=None):
    """Load environment variables from .env file if present."""
    if dotenv_path is None:
        dotenv_path = BASE_DIR / ".env"
    if not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


load_dotenv()

MAX_BODY_BYTES = 16_384
ORDER_TTL_SECONDS = 15 * 60
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
COMMERCE_DB_PATH = Path(os.environ.get("COMMERCE_DB_PATH", BASE_DIR / "commerce.db"))
# SQLite gives the local server durable state. A serverless deployment must use a
# managed database adapter instead, so it fails closed rather than lose purchases.
PAYMENTS_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET and not os.environ.get("VERCEL"))
WEBHOOKS_ENABLED = bool(PAYMENTS_ENABLED and RAZORPAY_WEBHOOK_SECRET)
commerce_store = CommerceStore(COMMERCE_DB_PATH) if PAYMENTS_ENABLED else None

# Prices live on the server. Never trust a title or amount received from a browser.
CATALOG = {
    "red-rising-audio": ("Red Rising Audiobook", {"INR": 19900, "USD": 199}),
    "golden-son-audio": ("Golden Son Audiobook", {"INR": 19900, "USD": 199}),
    "morning-star-audio": ("Morning Star Audiobook", {"INR": 19900, "USD": 199}),
    "iron-gold-audio": ("Iron Gold Audiobook", {"INR": 24900, "USD": 249}),
    "dark-age-audio": ("Dark Age Audiobook", {"INR": 24900, "USD": 249}),
    "light-bringer-audio": ("Light Bringer Audiobook", {"INR": 24900, "USD": 249}),
    "red-rising-ebook": ("Red Rising Ebook", {"INR": 9900, "USD": 99}),
    "golden-son-ebook": ("Golden Son Ebook", {"INR": 9900, "USD": 99}),
    "morning-star-ebook": ("Morning Star Ebook", {"INR": 9900, "USD": 99}),
    "iron-gold-ebook": ("Iron Gold Ebook", {"INR": 9900, "USD": 99}),
    "dark-age-ebook": ("Dark Age Ebook", {"INR": 9900, "USD": 99}),
    "light-bringer-ebook": ("Light Bringer Ebook", {"INR": 9900, "USD": 99}),
    "saga-combo": ("Complete Ebooks & Audiobooks Mega Combo", {"INR": 89900, "USD": 899}),
}
EBOOK_PRODUCT_FILES = {
    "red-rising-ebook": [BASE_DIR / "1_Red_Rising_-_Pierce_Brown.epub"],
    "golden-son-ebook": [BASE_DIR / "Golden Son.epub"],
    "morning-star-ebook": [BASE_DIR / "3_Morning_Star_-_Pierce_Brown (1).epub"],
    "iron-gold-ebook": [BASE_DIR / "Iron Gold.epub"],
    "dark-age-ebook": [BASE_DIR / "Dark_Age_Red_Rising_Saga_5_-_Pierce_Brown.epub"],
    "light-bringer-ebook": [BASE_DIR / "Light Bringer A Red Rising Novel.epub"],
}
AUDIO_PRODUCT_FOLDERS = {
    "red-rising-audio": {"title": "Red Rising Audiobook", "path": BASE_DIR / "red rising audiobook1"},
    "golden-son-audio": {"title": "Golden Son Audiobook", "path": BASE_DIR / "GOLDEN SON AUDIOBOOK"},
    "morning-star-audio": {"title": "Morning Star Audiobook", "path": BASE_DIR / "MORNING STAR AUDIOBOOK"},
    "iron-gold-audio": {"title": "Iron Gold Audiobook", "path": BASE_DIR / "IRON GOLD AUDIOBOOK"},
    "dark-age-audio": {"title": "Dark Age Audiobook", "path": BASE_DIR / "DARK AGE AUDIOBOOK"},
    "light-bringer-audio": {"title": "Light Bringer Audiobook", "path": BASE_DIR / "Light Bringer Audiobook.m4b"},
}
PREVIEW_SECONDS = 600
PREVIEW_BYTES_PER_SECOND = 128_000 / 8
RATE_LOCK = threading.Lock()
RATE_WINDOWS = {}


def checkout_allowed(address):
    now = time.monotonic()
    with RATE_LOCK:
        for key in list(RATE_WINDOWS):
            if now - RATE_WINDOWS[key][0] >= 60:
                del RATE_WINDOWS[key]
        if address not in RATE_WINDOWS:
            if len(RATE_WINDOWS) >= 5000:
                return False
            RATE_WINDOWS[address] = [now, 0]
        RATE_WINDOWS[address][1] += 1
        return RATE_WINDOWS[address][1] <= 60

MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".avif": "image/avif", ".mp3": "audio/mpeg",
    ".m4b": "audio/mp4", ".epub": "application/epub+zip", ".zip": "application/zip",
    ".xml": "application/xml; charset=utf-8", ".txt": "text/plain; charset=utf-8",
}

# These are deployment artifacts or implementation files, never public website assets.
PRIVATE_PATH_PARTS = {"__MACOSX", "__pycache__", ".git", ".env", ".DS_Store"}
PRIVATE_SUFFIXES = {".py", ".pyc", ".pem", ".key", ".sqlite", ".db"}


def valid_signature(secret, message, signature):
    """Constant-time HMAC comparison, shared by checkout and webhook validation."""
    if (not secret or not message or not isinstance(signature, str)
            or len(signature) != 64
            or any(char not in "0123456789abcdef" for char in signature)):
        return False
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def resolve_audio_path(product_id, filename):
    """Resolve a requested audiobook file safely within the configured product folder or file."""
    spec = AUDIO_PRODUCT_FOLDERS.get(product_id)
    if not spec:
        return None
    media_path = spec["path"]
    if not media_path.exists():
        return None
    if media_path.is_dir():
        try:
            candidate = (media_path / urllib.parse.unquote(filename)).resolve()
        except (RuntimeError, ValueError):
            return None
        try:
            candidate.relative_to(media_path.resolve())
        except ValueError:
            return None
        if not candidate.is_file() or candidate.suffix.lower() not in {".mp3", ".m4b"}:
            return None
        return candidate
    if media_path.is_file():
        if filename and urllib.parse.unquote(filename) not in {media_path.name, media_path.name.replace(" ", "%20")}:
            return None
        return media_path
    return None


def resolve_ebook_path(product_id):
    """Resolve a requested ebook file or packaged directory safely within the project root."""
    for candidate in EBOOK_PRODUCT_FILES.get(product_id, []):
        if candidate.exists():
            return candidate
    return None


def iter_archive_files(root_path, prefix=""):
    """Yield files under a directory tree with stable internal paths for ZIP creation."""
    if not root_path or not root_path.exists():
        return []
    files = []
    for path in sorted(root_path.rglob("*")):
        if path.is_file():
            rel_path = path.relative_to(root_path)
            files.append((path, f"{prefix}{rel_path.as_posix()}" if prefix else rel_path.as_posix()))
    return files


def build_download_payload(product_id):
    """Prepare a downloadable payload for a purchased product."""
    if product_id == "saga-combo":
        files = []
        for ebook_id in ("red-rising-ebook", "golden-son-ebook", "morning-star-ebook", "iron-gold-ebook", "dark-age-ebook", "light-bringer-ebook"):
            ebook_path = resolve_ebook_path(ebook_id)
            if ebook_path:
                if ebook_path.is_dir():
                    files.extend((item_path, f"ebooks/{ebook_id}/{inside_name}") for item_path, inside_name in iter_archive_files(ebook_path))
                else:
                    files.append((ebook_path, f"ebooks/{ebook_id}/{ebook_path.name}"))
        for audio_id in ("red-rising-audio", "golden-son-audio", "morning-star-audio", "iron-gold-audio", "dark-age-audio", "light-bringer-audio"):
            spec = AUDIO_PRODUCT_FOLDERS.get(audio_id, {})
            media_path = spec.get("path")
            if not media_path or not media_path.exists():
                continue
            if media_path.is_dir():
                for entry in sorted(media_path.iterdir()):
                    if entry.is_file() and entry.suffix.lower() in {".mp3", ".m4b"}:
                        files.append((entry, f"audio/{audio_id}/{entry.name}"))
            elif media_path.is_file():
                files.append((media_path, f"audio/{audio_id}/{media_path.name}"))
        return {"kind": "archive", "filename": "red-rising-saga-combo.zip", "files": files, "is_bundle": True}

    ebook_path = resolve_ebook_path(product_id)
    if ebook_path:
        if ebook_path.is_dir():
            files = iter_archive_files(ebook_path)
            if files:
                return {"kind": "archive", "filename": f"{ebook_path.name}.zip", "files": [(path, f"{ebook_path.name}/{inside_name}") for path, inside_name in files], "is_bundle": False}
            return None
        return {"kind": "file", "path": ebook_path, "filename": ebook_path.name, "content_type": MIME_TYPES.get(ebook_path.suffix.lower(), "application/octet-stream")}

    spec = AUDIO_PRODUCT_FOLDERS.get(product_id)
    if spec:
        media_path = spec["path"]
        if media_path.is_dir():
            files = []
            for entry in sorted(media_path.iterdir()):
                if entry.is_file() and entry.suffix.lower() in {".mp3", ".m4b"}:
                    files.append((entry, entry.name))
            if files:
                return {"kind": "archive", "filename": f"{product_id}.zip", "files": files, "is_bundle": False}
        if media_path.is_file():
            return {"kind": "file", "path": media_path, "filename": media_path.name, "content_type": MIME_TYPES.get(media_path.suffix.lower(), "application/octet-stream")}
    return None


def product_available(product_id):
    if product_id == "saga-combo":
        return all(product_available(key) for key in CATALOG if key != "saga-combo")
    payload = build_download_payload(product_id)
    if not payload:
        return False
    files = [path for path, _ in payload["files"]] if payload["kind"] == "archive" else [payload["path"]]
    return bool(files) and all(path.is_file() and path.stat().st_size > 0 for path in files)


def build_archive_bytes(files, archive_name):
    """Create a ZIP archive in memory for a set of files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, inside_name in files:
            archive.write(path, inside_name)
    return buffer.getvalue()


def is_protected_media_path(candidate):
    """Return True when a requested path points into a protected audiobook folder or file, or protected ebook."""
    cand = candidate.resolve()
    for spec in AUDIO_PRODUCT_FOLDERS.values():
        media_path = spec["path"].resolve()
        if media_path.is_dir():
            try:
                cand.relative_to(media_path)
                return True
            except ValueError:
                pass
        elif media_path.is_file():
            if cand == media_path:
                return True
    for file_list in EBOOK_PRODUCT_FILES.values():
        for ebook_path in file_list:
            ep = ebook_path.resolve()
            if ep.is_dir():
                try:
                    cand.relative_to(ep)
                    return True
                except ValueError:
                    pass
            elif ep.is_file():
                if cand == ep:
                    return True
    return False


def get_preview_limit_bytes(file_path, preview_seconds=PREVIEW_SECONDS):
    """Estimate a preview window size in bytes for a protected audiobook."""
    if not file_path or not file_path.exists():
        return 0
    file_size = file_path.stat().st_size
    if file_size <= 0:
        return 0
    preview_bytes = int(preview_seconds * PREVIEW_BYTES_PER_SECOND)
    return min(file_size, max(1, preview_bytes))


def has_access(session_token, product_id):
    """Check a database-backed, expiring browser session entitlement."""
    return bool(commerce_store and commerce_store.has_access(session_token, product_id))


class RazorpayAPIError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def razorpay_request(endpoint, payload):
    """Use Razorpay's API without exposing the secret to a client."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RazorpayAPIError(401, "Razorpay credentials are not configured on the server")
    credentials = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    request = urllib.request.Request(
        f"https://api.razorpay.com/v1/{endpoint}",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_msg = str(exc)
        try:
            err_json = json.loads(exc.read().decode("utf-8"))
            err_msg = err_json.get("error", {}).get("description", str(exc))
        except Exception:
            pass
        raise RazorpayAPIError(exc.code, err_msg) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RazorpayAPIError(500, f"Razorpay API request error: {exc}") from exc


def razorpay_get(endpoint):
    """Fetch canonical payment state from Razorpay before fulfilling an order."""
    credentials = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    request = urllib.request.Request(
        f"https://api.razorpay.com/v1/{endpoint}",
        headers={"Authorization": f"Basic {credentials}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RazorpayAPIError(exc.code, "Unable to retrieve payment status") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RazorpayAPIError(500, "Unable to retrieve payment status") from exc


class RedRisingServerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Do not retain payment IDs or capability tokens in access logs.
        print(f"[{self.log_date_time_string()}] {self.command}")

    def add_security_headers(self):
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://checkout.razorpay.com; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data: https://*.razorpay.com; media-src 'self'; connect-src 'self' https://*.razorpay.com; frame-src https://api.razorpay.com https://checkout.razorpay.com; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_security_headers()
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/api/health":
            return self.send_json_response(200, {"status": "online", "payments_enabled": PAYMENTS_ENABLED})
        if path == "/api/config":
            return self.send_json_response(200, {"payments_enabled": PAYMENTS_ENABLED, "razorpay_key_id": RAZORPAY_KEY_ID if PAYMENTS_ENABLED else None, "currencies": ["INR", "USD"]})
        if path == "/api/purchases":
            products = [product for product in CATALOG if has_access(self.session_token(), product)]
            return self.send_json_response(200, {"products": products})
        if path.startswith("/api/download-ready/"):
            product = path.rsplit("/", 1)[-1]
            if product not in CATALOG or not has_access(self.session_token(), product):
                return self.send_error_response(403, "Purchase required")
            if not product_available(product):
                return self.send_error_response(503, "Your purchase is saved, but its files are temporarily unavailable")
            return self.send_json_response(200, {"ready": True})
        if path.startswith("/api/audio/"):
            return self.serve_audio_file(path)
        if path.startswith("/api/download/"):
            return self.serve_download(path)
        if path == "/":
            path = "/index.html"
        candidate = (BASE_DIR / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(BASE_DIR)
        except ValueError:
            return self.send_error_response(403, "Invalid path")
        relative_parts = candidate.relative_to(BASE_DIR).parts
        # Only explicit website files and root-level artwork are public. This
        # also excludes duplicate media, database journals, and local reports.
        if len(relative_parts) != 1 or (candidate.name not in {"index.html", "style.css", "script.js", "robots.txt", "sitemap.xml"}
                and candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".ico"}):
            return self.send_error_response(404, "File not found")
        if (any(part.startswith(".") or part in PRIVATE_PATH_PARTS for part in relative_parts)
                or candidate.suffix.lower() in PRIVATE_SUFFIXES):
            return self.send_error_response(404, "File not found")
        if is_protected_media_path(candidate):
            return self.send_error_response(404, "File not found")
        if not candidate.is_file():
            return self.send_error_response(404, "File not found")
        self.serve_file(candidate)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 1 or length > MAX_BODY_BYTES or "application/json" not in self.headers.get("Content-Type", ""):
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            return body if isinstance(body, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/webhooks/razorpay":
            return self.handle_webhook()
        body = self.read_json_body()
        if body is None:
            return self.send_error_response(400, "A small JSON request body is required")
        if path not in {"/api/order", "/api/create-order", "/api/verify", "/api/verify-payment"}:
            return self.send_error_response(404, "Endpoint not found")
        if not PAYMENTS_ENABLED:
            return self.send_error_response(503, "Payments are unavailable on this deployment")
        if not checkout_allowed(self.client_address[0]):
            return self.send_error_response(429, "Too many checkout requests. Please wait a minute.")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send_error_response(403, "Cross-origin checkout is not allowed")
        if path in ("/api/order", "/api/create-order"):
            return self.create_order(body)
        if path in ("/api/verify", "/api/verify-payment"):
            return self.verify_payment(body)
        return self.send_error_response(404, "Endpoint not found")

    def create_order(self, body):
        product_id = body.get("product_id")
        currency = str(body.get("currency", "INR")).upper()
        if isinstance(product_id, str) and product_id in CATALOG:
            title, amounts = CATALOG[product_id]
            if currency not in amounts:
                return self.send_error_response(400, "Unsupported currency")
            amount = amounts[currency]
        else:
            return self.send_error_response(400, "A recognized product_id is required")
        if not product_available(product_id):
            return self.send_error_response(503, "This product is temporarily unavailable; no payment was requested")

        try:
            order = razorpay_request("orders", {
                "amount": amount,
                "currency": currency,
                "receipt": f"rr_{secrets.token_hex(10)}",
                "notes": {"product_id": product_id},
            })
            commerce_store.create_order(order["id"], product_id, amount, currency)
            checkout_token = commerce_store.bind_checkout_session(order["id"], self.session_token())
        except RazorpayAPIError:
            return self.send_error_response(502, "The payment provider could not create an order. Please try again.")
        except Exception:
            return self.send_error_response(503, "Unable to create an order. Please try again.")

        return self.send_json_response(200, {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": currency,
            "name": title,
            "simulation": False
        }, extra_headers={"Set-Cookie": self.session_cookie(checkout_token)})

    def verify_payment(self, body):
        order_id = body.get("razorpay_order_id", "")
        payment_id = body.get("razorpay_payment_id", "")
        signature = body.get("razorpay_signature", "")

        if not all(isinstance(value, str) and value.strip() for value in (order_id, payment_id, signature)):
            return self.send_error_response(400, "Missing required verification fields (razorpay_order_id, razorpay_payment_id, razorpay_signature)")

        known_order = commerce_store.get_order(order_id) if commerce_store else None
        if not known_order:
            return self.send_error_response(400, "Unknown or expired order")
        if not valid_signature(RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode("utf-8"), signature):
            return self.send_error_response(400, "Invalid payment signature")
        try:
            payment = razorpay_get(f"payments/{urllib.parse.quote(payment_id, safe='')}")
        except RazorpayAPIError:
            return self.send_error_response(502, "Unable to confirm payment status. Please try again.")
        if (payment.get("id") != payment_id or payment.get("order_id") != order_id
                or payment.get("amount") != known_order["amount"]
                or payment.get("currency") != known_order["currency"]):
            return self.send_error_response(400, "Payment does not match this order")
        if payment.get("status") != "captured":
            return self.send_error_response(409, "Payment is awaiting capture. Retry verification; do not pay again.")
        if not commerce_store.mark_payment_captured(order_id, payment_id):
            return self.send_error_response(409, "Payment cannot be applied to this order")
        session = commerce_store.issue_session_for_order(order_id, self.session_token())
        if not session:
            return self.send_error_response(500, "Payment recorded but entitlement could not be created")
        session_token, expires_at = session
        return self.send_json_response(200, {
            "success": True,
            "status": "verified",
            "product_id": known_order["product_id"],
            "expires_at": expires_at,
        }, extra_headers={"Set-Cookie": self.session_cookie(session_token)})

    def handle_webhook(self):
        if not WEBHOOKS_ENABLED:
            return self.send_error_response(503, "Payments are unavailable on this deployment")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if 0 < length <= MAX_BODY_BYTES else b""
        signature = self.headers.get("X-Razorpay-Signature", "")
        if not RAZORPAY_WEBHOOK_SECRET or not raw or not valid_signature(RAZORPAY_WEBHOOK_SECRET, raw, signature):
            return self.send_error_response(400, "Invalid webhook signature")
        try:
            event = json.loads(raw.decode("utf-8"))
            payment_info = event.get("payload", {}).get("payment", {}).get("entity", {})
            order_id, payment_id = payment_info.get("order_id"), payment_info.get("id")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
            return self.send_error_response(400, "Invalid webhook payload")
        if event.get("event") not in {"payment.captured", "order.paid"} or not order_id or not payment_id:
            return self.send_json_response(200, {"status": "ignored"})
        known_order = commerce_store.get_order(order_id)
        if not known_order:
            return self.send_json_response(200, {"status": "ignored"})
        try:
            payment = razorpay_get(f"payments/{urllib.parse.quote(payment_id, safe='')}")
        except RazorpayAPIError:
            return self.send_error_response(502, "Unable to confirm payment status")
        if (payment.get("id") != payment_id or payment.get("order_id") != order_id or payment.get("status") != "captured"
                or payment.get("amount") != known_order["amount"]
                or payment.get("currency") != known_order["currency"]):
            return self.send_error_response(400, "Webhook payment does not match order")
        if not commerce_store.mark_payment_captured(order_id, payment_id):
            return self.send_error_response(409, "Payment cannot be applied to this order")
        first_delivery = commerce_store.mark_webhook_processed(self.headers.get("X-Razorpay-Event-Id", ""))
        return self.send_json_response(200, {"status": "processed" if first_delivery else "duplicate"})

    def session_cookie(self, token):
        cookie = http.cookies.SimpleCookie()
        cookie["rr_session"] = token
        cookie["rr_session"]["path"] = "/"
        cookie["rr_session"]["httponly"] = True
        cookie["rr_session"]["samesite"] = "Strict"
        cookie["rr_session"]["max-age"] = SESSION_TTL_SECONDS
        if self.headers.get("X-Forwarded-Proto") == "https":
            cookie["rr_session"]["secure"] = True
        return cookie.output(header="").strip()

    def session_token(self):
        try:
            return http.cookies.SimpleCookie(self.headers.get("Cookie", "")).get("rr_session").value
        except (AttributeError, http.cookies.CookieError):
            return ""

    def serve_audio_file(self, path):
        parts = [part for part in urllib.parse.unquote(path).split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "audio":
            return self.send_error_response(404, "Audio endpoint not found")
        product_id = parts[2]
        filename = "/".join(parts[3:])
        if not product_id or not filename:
            return self.send_error_response(400, "Audio file path is required")
        audio_path = resolve_audio_path(product_id, filename)
        if not audio_path:
            return self.send_error_response(404, "Audio file not found")
        if has_access(self.session_token(), product_id):
            return self.serve_file(audio_path)
        preview_limit = get_preview_limit_bytes(audio_path)
        if preview_limit <= 0:
            return self.send_error_response(402, "Purchase this audiobook to unlock the full recording.")
        content_type = MIME_TYPES.get(audio_path.suffix.lower(), "application/octet-stream")
        size = audio_path.stat().st_size
        preview_end = min(preview_limit - 1, size - 1)
        self.send_response(206)
        self.add_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(preview_end + 1))
        self.send_header("Content-Range", f"bytes 0-{preview_end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with audio_path.open("rb") as stream:
            stream.seek(0)
            self.wfile.write(stream.read(preview_end + 1))

    def serve_download(self, path):
        parts = [part for part in urllib.parse.unquote(path).split("/") if part]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "download":
            return self.send_error_response(404, "Download endpoint not found")
        product_id = parts[2]
        if product_id not in CATALOG:
            return self.send_error_response(404, "Product not found")
        if not has_access(self.session_token(), product_id):
            return self.send_error_response(402, "Purchase this product to download the files.")
        payload = build_download_payload(product_id)
        if not payload:
            return self.send_error_response(404, "Download package not available")
        if payload["kind"] == "archive":
            # A full saga archive is several GB. Keep it on disk and copy bounded
            # chunks rather than allocating the entire library in process memory.
            with tempfile.TemporaryFile() as stream:
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for file_path, name in payload["files"]:
                        archive.write(file_path, name)
                size = stream.tell()
                stream.seek(0)
                self.send_response(200)
                self.add_security_headers()
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{payload["filename"]}"')
                self.send_header("Cache-Control", "private, no-store")
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile, 256 * 1024)
            return
        file_path = payload["path"]
        self.send_response(200)
        self.add_security_headers()
        self.send_header("Content-Type", payload.get("content_type", MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")))
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{payload["filename"]}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with file_path.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile, 256 * 1024)

    def serve_file(self, file_path):
        content_type = MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        size = file_path.stat().st_size
        range_header = self.headers.get("Range", "")
        if range_header and file_path.suffix.lower() in {".mp3", ".m4b"}:
            try:
                unit, value = range_header.split("=", 1)
                start_text, end_text = value.split("-", 1)
                if unit != "bytes" or "," in value:
                    raise ValueError
                start = int(start_text) if start_text else max(0, size - int(end_text))
                end = int(end_text) if end_text else size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
            except ValueError:
                self.send_response(416); self.add_security_headers(); self.send_header("Content-Range", f"bytes */{size}"); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(206); self.add_security_headers(); self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1)); self.send_header("Content-Range", f"bytes {start}-{end}/{size}"); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
            with file_path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(remaining, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        self.send_response(200); self.add_security_headers(); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(size)); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
        with file_path.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile, 256 * 1024)

    def send_json_response(self, code, data, extra_headers=None):
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.add_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_response(self, code, message):
        self.send_json_response(code, {"success": False, "status": "error", "error": message, "message": message})


def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), RedRisingServerHandler) as server:
        mode = "Razorpay payments enabled" if PAYMENTS_ENABLED else "payments unavailable"
        print(f"Red Rising server: http://localhost:{PORT} ({mode})")
        server.serve_forever()


if __name__ == "__main__":
    run_server()
