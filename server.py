"""Telegram shop bot + Mini App server (pure Python 3, no dependencies).

Env vars:
  BOT_TOKEN    token from @BotFather
  WEBAPP_URL   public https URL of this server (e.g. https://hst-shop.onrender.com)
  ADMIN_ID     chat id that receives orders (send /admin to the bot to learn yours)
  MANAGER      manager username without @
  TIKTOK       TikTok link
"""
import hashlib, hmac, json, os, threading, time, urllib.parse, urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
ADMIN_ID = os.environ.get("ADMIN_ID", "")
MANAGER = os.environ.get("MANAGER", "g2hst_manager").lstrip("@")
TIKTOK = os.environ.get("TIKTOK", "https://www.tiktok.com/@g2hst")
API = f"https://api.telegram.org/bot{TOKEN}"

WELCOME = ("Вітаємо в магазині HST!\n\n"
           "Тут можна замовити таблички, брелки та стікерпаки Hutory Stunt Team.\n\n"
           "З усіх питань і замовлень звертайтесь до менеджера" + (f" @{MANAGER}." if MANAGER else "."))


def tg(method, **data):
    req = urllib.request.Request(f"{API}/{method}", json.dumps(data).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)
    except Exception as e:
        print("tg error", method, e)
        return {"ok": False}


def valid_init_data(init_data):
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        if hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
            return json.loads(pairs.get("user", "{}"))
    except Exception:
        pass
    return None


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:300]


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/config":
            return self._json({"manager": MANAGER, "tiktok": TIKTOK})
        if path == "/":
            self.path = "/index.html"
        elif not path.lower().endswith((".jpg", ".png", ".html")):
            return self._json({"ok": False}, 404)
        return super().do_GET()

    def do_POST(self):
        if self.path != "/order":
            return self._json({"ok": False}, 404)
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            user = valid_init_data(d.get("initData", ""))
            if user is None:
                return self._json({"ok": False, "error": "bad signature"}, 403)
            if not ADMIN_ID:
                return self._json({"ok": False, "error": "ADMIN_ID not set"}, 500)
            uname = f"@{user['username']}" if user.get("username") else f"id {user.get('id')}"
            text = ("🛒 <b>Нове замовлення</b>\n\n"
                    f"Товар: {esc(d.get('product'))}\nКількість: {int(d.get('qty', 1))}\n"
                    f"Сума: <b>{int(d.get('total', 0))} грн</b>\n\n"
                    f"Ім'я: {esc(d.get('name'))}\nТелефон: {esc(d.get('phone'))}\n"
                    f"Нова Пошта: {esc(d.get('np'))}\nКоментар: {esc(d.get('note') or '—')}\n\n"
                    f"Telegram: {esc(uname)}")
            r = tg("sendMessage", chat_id=ADMIN_ID, text=text, parse_mode="HTML")
            return self._json({"ok": r.get("ok", False)}, 200 if r.get("ok") else 502)
        except Exception as e:
            print("order error", e)
            return self._json({"ok": False}, 400)


def poll():
    if not TOKEN:
        print("BOT_TOKEN is not set: web only")
        return
    tg("deleteWebhook")
    if WEBAPP_URL:
        tg("setChatMenuButton", menu_button={"type": "web_app", "text": "Shop", "web_app": {"url": WEBAPP_URL}})
    offset = 0
    while True:
        r = tg("getUpdates", offset=offset, timeout=30)
        if not r.get("ok"):
            time.sleep(3)
            continue
        for u in r["result"]:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            txt = (m.get("text") or "").strip()
            chat = m.get("chat", {}).get("id")
            if txt.startswith("/start"):
                kb = {"inline_keyboard": [[{"text": "Відкрити магазин", "web_app": {"url": WEBAPP_URL}}]]}
                r2 = tg("sendPhoto", chat_id=chat, photo=f"{WEBAPP_URL}/banner.png", caption=WELCOME, reply_markup=kb)
                if not r2.get("ok"):
                    tg("sendMessage", chat_id=chat, text=WELCOME, reply_markup=kb)
            elif txt.startswith("/admin"):
                tg("sendMessage", chat_id=chat,
                   text=f"Твій chat id: {chat}\nВстав його в налаштування ADMIN_ID, і заявки приходитимуть сюди.")


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
