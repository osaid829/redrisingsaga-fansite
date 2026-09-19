#!/usr/bin/env python3
"""Small, dependency-free server for the Red Rising site and Razorpay checkout.

Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and (in production) RAZORPAY_WEBHOOK_SECRET.
Without keys the site intentionally stays in simulation mode; it can never charge a card.
"""

import base64
import hashlib
import hmac
import http.server
import io
import json
import os
import secrets
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PORT = int(os.environ.get("PORT", "8000"))
BASE_DIR = Path(__file__).parent.resolve()
MAX_BODY_BYTES = 16_384
MAX_PENDING_ORDERS = 500
ORDER_TTL_SECONDS = 15 * 60
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
PAYMENTS_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)

# Prices live on the server. Never trust a title or amount received from a browser.
CATALOG = {
    "red-rising-audio": ("Red Rising Audiobook", {"INR": 199, "USD": 199}),
    "golden-son-audio": ("Golden Son Audiobook", {"INR": 199, "USD": 199}),
    "morning-star-audio": ("Morning Star Audiobook", {"INR": 199, "USD": 199}),
    "iron-gold-audio": ("Iron Gold Audiobook", {"INR": 249, "USD": 249}),
    "dark-age-audio": ("Dark Age Audiobook", {"INR": 249, "USD": 249}),
    "light-bringer-audio": ("Light Bringer Audiobook", {"INR": 249, "USD": 249}),
    "red-rising-ebook": ("Red Rising Ebook", {"INR": 99, "USD": 99}),
    "golden-son-ebook": ("Golden Son Ebook", {"INR": 99, "USD": 99}),
    "morning-star-ebook": ("Morning Star Ebook", {"INR": 99, "USD": 99}),
    "iron-gold-ebook": ("Iron Gold Ebook", {"INR": 99, "USD": 99}),
    "dark-age-ebook": ("Dark Age Ebook", {"INR": 99, "USD": 99}),
    "light-bringer-ebook": ("Light Bringer Ebook", {"INR": 99, "USD": 99}),
    "saga-combo": ("Complete Ebooks & Audiobooks Mega Combo", {"INR": 899, "USD": 899}),
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
PENDING_ORDERS = {}
PROCESSED_PAYMENTS = set()
ACCESS_TOKENS = {}
PURCHASES = {}
PAYMENT_LOCK = threading.Lock()
PREVIEW_SECONDS = 600
PREVIEW_BYTES_PER_SECOND = 128_000 / 8

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
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def prune_expired_orders(now=None):
    """Remove stale pending orders so memory use stays bounded."""
    if now is None:
        now = time.time()
    expired_ids = [order_id for order_id, order in PENDING_ORDERS.items() if now - order.get("created", now) > ORDER_TTL_SECONDS]
    for order_id in expired_ids:
        PENDING_ORDERS.pop(order_id, None)


def issue_access_token(product_id):
    """Grant a browser token for full audiobook access after purchase."""
    token = secrets.token_urlsafe(24)
    ACCESS_TOKENS[token] = product_id
    PURCHASES[token] = {"product_id": product_id, "created": time.time()}
    return token


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


def build_archive_bytes(files, archive_name):
    """Create a ZIP archive in memory for a set of files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, inside_name in files:
            archive.write(path, inside_name)
    return buffer.getvalue()


def is_protected_media_path(candidate):
    """Return True when a requested path points into a protected audiobook folder or file."""
    for spec in AUDIO_PRODUCT_FOLDERS.values():
        media_path = spec["path"].resolve()
        base_path = media_path if media_path.is_dir() else media_path.parent
        try:
            candidate.relative_to(base_path)
            return True
        except ValueError:
            continue
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


def has_access(token, product_id):
    """Return True if the supplied token grants access to the requested product."""
    return bool(token) and ACCESS_TOKENS.get(token) == product_id


def razorpay_request(endpoint, payload):
    """Use Razorpay's Orders API without exposing the secret to a client."""
    credentials = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    request = urllib.request.Request(
        f"https://api.razorpay.com/v1/{endpoint}",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Razorpay could not create the order") from exc


class RedRisingServerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {self.command} {self.path} -> {args[0]}")

    def add_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, X-Access-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' https://checkout.razorpay.com; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; connect-src 'self' https://api.razorpay.com https://checkout.razorpay.com; frame-src https://api.razorpay.com https://checkout.razorpay.com; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_security_headers()
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/api/health":
            return self.send_json_response(200, {"status": "online", "payments_enabled": PAYMENTS_ENABLED})
        if path == "/api/config":
            return self.send_json_response(200, {"payments_enabled": PAYMENTS_ENABLED, "razorpay_key_id": RAZORPAY_KEY_ID if PAYMENTS_ENABLED else None, "currencies": ["INR", "USD"]})
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
        if path == "/api/order":
            return self.create_order(body)
        if path == "/api/verify":
            return self.verify_payment(body)
        if path == "/api/access/grant":
            return self.grant_access(body)
        return self.send_error_response(404, "Endpoint not found")

    def create_order(self, body):
        product_id = body.get("product_id")
        if product_id not in CATALOG:
            return self.send_error_response(400, "Unknown product")
        title, amounts = CATALOG[product_id]
        currency = body.get("currency", "INR")
        if currency not in amounts:
            return self.send_error_response(400, "Unsupported currency")
        amount = amounts[currency]
        receipt = f"rr_{secrets.token_hex(10)}"
        if PAYMENTS_ENABLED:
            try:
                order = razorpay_request("orders", {"amount": amount, "currency": currency, "receipt": receipt, "notes": {"product_id": product_id}})
            except RuntimeError:
                return self.send_error_response(503, "Payment service is temporarily unavailable")
        else:
            order = {"id": f"order_sim_{secrets.token_hex(12)}", "amount": amount, "currency": currency}
        with PAYMENT_LOCK:
            prune_expired_orders()
            if len(PENDING_ORDERS) >= MAX_PENDING_ORDERS:
                return self.send_error_response(429, "Too many pending orders. Please try again shortly.")
            PENDING_ORDERS[order["id"]] = {"product_id": product_id, "amount": amount, "currency": currency, "created": time.time()}
        return self.send_json_response(201, {"order_id": order["id"], "amount": order["amount"], "currency": currency, "name": title, "simulation": not PAYMENTS_ENABLED})

    def verify_payment(self, body):
        order_id = body.get("razorpay_order_id", "")
        payment_id = body.get("razorpay_payment_id", "")
        signature = body.get("razorpay_signature", "")
        if not all(isinstance(value, str) and value for value in (order_id, payment_id, signature)):
            return self.send_error_response(400, "Missing payment verification values")
        with PAYMENT_LOCK:
            prune_expired_orders()
            known_order = PENDING_ORDERS.get(order_id)
            duplicate = payment_id in PROCESSED_PAYMENTS
        if not known_order:
            return self.send_error_response(400, "Unknown or expired order")
        if duplicate:
            return self.send_error_response(409, "Payment was already processed")
        if not PAYMENTS_ENABLED:
            product_id = known_order["product_id"]
            token = issue_access_token(product_id)
            return self.send_json_response(200, {"status": "verified", "payment_id": payment_id, "access_token": token, "product_id": product_id})
        if not valid_signature(RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode(), signature):
            return self.send_error_response(400, "Invalid payment signature")
        with PAYMENT_LOCK:
            PROCESSED_PAYMENTS.add(payment_id)
            PENDING_ORDERS.pop(order_id, None)
        product_id = known_order["product_id"]
        token = issue_access_token(product_id)
        return self.send_json_response(200, {"status": "verified", "payment_id": payment_id, "access_token": token, "product_id": product_id})

    def grant_access(self, body):
        product_id = body.get("product_id")
        if product_id not in CATALOG:
            return self.send_error_response(400, "Unsupported product")
        token = issue_access_token(product_id)
        return self.send_json_response(200, {"access_token": token, "product_id": product_id})

    def handle_webhook(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if 0 < length <= MAX_BODY_BYTES else b""
        signature = self.headers.get("X-Razorpay-Signature", "")
        if not RAZORPAY_WEBHOOK_SECRET or not raw or not valid_signature(RAZORPAY_WEBHOOK_SECRET, raw, signature):
            return self.send_error_response(400, "Invalid webhook signature")
        # A production fulfilment worker should consume verified webhook events durably.
        return self.send_json_response(200, {"status": "received"})

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
        access_token = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [self.headers.get("X-Access-Token", "")])[0]
        if has_access(access_token, product_id):
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
        access_token = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [self.headers.get("X-Access-Token", "")])[0]
        if not has_access(access_token, product_id):
            return self.send_error_response(402, "Purchase this product to download the files.")
        payload = build_download_payload(product_id)
        if not payload:
            return self.send_error_response(404, "Download package not available")
        if payload["kind"] == "archive":
            archive_bytes = build_archive_bytes(payload["files"], payload["filename"])
            self.send_response(200)
            self.add_security_headers()
            self.send_header("Content-Type", MIME_TYPES.get(".zip", "application/zip"))
            self.send_header("Content-Length", str(len(archive_bytes)))
            self.send_header("Content-Disposition", f'attachment; filename="{payload["filename"]}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(archive_bytes)
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
            self.wfile.write(stream.read())

    def serve_file(self, file_path):
        content_type = MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
        size = file_path.stat().st_size
        range_header = self.headers.get("Range", "")
        if range_header and file_path.suffix.lower() == ".mp3":
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
                self.send_response(416); self.add_security_headers(); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            self.send_response(206); self.add_security_headers(); self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1)); self.send_header("Content-Range", f"bytes {start}-{end}/{size}"); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
            with file_path.open("rb") as stream:
                stream.seek(start); self.wfile.write(stream.read(end - start + 1))
            return
        self.send_response(200); self.add_security_headers(); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(size)); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
        with file_path.open("rb") as stream: self.wfile.write(stream.read())

    def send_json_response(self, code, data):
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(code); self.add_security_headers(); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def send_error_response(self, code, message):
        self.send_json_response(code, {"status": "error", "message": message})


def run_server():
    with socketserver.ThreadingTCPServer(("", PORT), RedRisingServerHandler) as server:
        print(f"Red Rising server: http://localhost:{PORT} ({'live payments' if PAYMENTS_ENABLED else 'payment simulation'})")
        server.serve_forever()


if __name__ == "__main__":
    run_server()
