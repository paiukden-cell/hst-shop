"""Telegram shop bot + Mini App server (pure Python 3, no dependencies).

Env vars:
  BOT_TOKEN    token from @BotFather
  WEBAPP_URL   public https URL of this server (e.g. https://hst-shop.onrender.com)
  ADMIN_ID     chat id that receives orders (send /admin to the bot to learn yours)
  MANAGER      manager username without @
  TIKTOK       TikTok link
  Only ADMIN_ID can add/edit/hide products from inside the Mini App (stored in a pinned message in his chat).
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
JAR = os.environ.get("MONO_JAR", "")
YT_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YT_HANDLE = os.environ.get("YT_HANDLE", "g2hst")
MAINT = os.environ.get("MAINTENANCE", "").strip().lower() in ("1", "true", "on", "yes", "так")
YT_SUBS = os.environ.get("YT_SUBS", "")
TT_FOLLOWERS = os.environ.get("TT_FOLLOWERS", "")
_cache = {"t": 0, "yt": None}
YOUTUBE = os.environ.get("YOUTUBE", "https://youtube.com/@g2hst?si=qaQ2cwDuvmlo11iv")
API = f"https://api.telegram.org/bot{TOKEN}"

WELCOME = ("Вітаємо в магазині VST!\n\n"
           "Тут можна замовити таблички, брелки та стікерпаки Vinnitsa Stunt Team.\n\n"
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


def to_int(v):
    try:
        return int(str(v).replace(" ", "").replace(",", ""))
    except Exception:
        return None


def youtube_subs():
    """Live subscriber count via YouTube Data API (cached 30 min); falls back to YT_SUBS."""
    if YT_KEY and time.time() - _cache["t"] > 1800:
        try:
            url = ("https://www.googleapis.com/youtube/v3/channels?part=statistics&forHandle="
                   + urllib.parse.quote(YT_HANDLE) + "&key=" + urllib.parse.quote(YT_KEY))
            with urllib.request.urlopen(url, timeout=10) as r:
                items = json.load(r).get("items") or []
            if items:
                _cache["yt"] = int(items[0]["statistics"]["subscriberCount"])
            _cache["t"] = time.time()
        except Exception as e:
            print("youtube api error", e)
            _cache["t"] = time.time() - 1500   # retry in ~5 min
    return _cache["yt"] if _cache["yt"] is not None else to_int(YT_SUBS)


_titles = {}


def video_title(vid):
    """Video title via YouTube oEmbed (no API key needed), cached in memory."""
    import re
    if not re.fullmatch(r"[\w-]{11}", vid or ""):
        return None
    if vid in _titles:
        return _titles[vid]
    try:
        url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote("https://youtu.be/" + vid, safe="")
        with urllib.request.urlopen(url, timeout=10) as r:
            t = json.load(r).get("title")
        if t:
            _titles[vid] = t
        return t
    except Exception as e:
        print("oembed error", e)
        return None


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:300]



# ---------- products managed by the admin from inside the Mini App ----------
# Saved in a pinned message in the admin's chat with the bot, so it survives restarts on free hosting.
CUSTOM = []      # products the admin added
OV = {}          # overrides for built-in products: {id: {"out":1,"hide":1,"price":350}}
_st = {"mid": None, "last": ""}
LOCK = threading.Lock()
PHOTO_CACHE = {}
DATA_HEAD = "📦 VST каталог — не видаляй і не відкріплюй це повідомлення"


def is_admin(init_data):
    user = valid_init_data(init_data)
    if not user or not ADMIN_ID or str(user.get("id")) != str(ADMIN_ID):
        return False
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data))
        return time.time() - int(pairs.get("auth_date", "0")) < 3 * 86400
    except Exception:
        return False


def _dump():
    return DATA_HEAD + "\n" + json.dumps({"c": CUSTOM, "o": OV}, ensure_ascii=False, separators=(",", ":"))


def store_load():
    if not (TOKEN and ADMIN_ID):
        return
    r = tg("getChat", chat_id=ADMIN_ID)
    pm = (r.get("result") or {}).get("pinned_message") or {}
    text = pm.get("text") or ""
    if text.startswith(DATA_HEAD):
        try:
            d = json.loads(text.split("\n", 1)[1])
            CUSTOM[:] = d.get("c", [])
            OV.clear()
            OV.update(d.get("o", {}))
            _st["mid"] = pm.get("message_id")
            _st["last"] = text
            print("loaded catalog:", len(CUSTOM), "custom,", len(OV), "overrides")
        except Exception as e:
            print("store_load error", e)


def store_save():
    text = _dump()
    if len(text) > 4000:
        return False
    if text == _st["last"]:
        return True
    if _st["mid"]:
        r = tg("editMessageText", chat_id=ADMIN_ID, message_id=_st["mid"], text=text)
        if r.get("ok"):
            _st["last"] = text
            return True
    r = tg("sendMessage", chat_id=ADMIN_ID, text=text)
    if r.get("ok"):
        _st["mid"] = r["result"]["message_id"]
        _st["last"] = text
        tg("pinChatMessage", chat_id=ADMIN_ID, message_id=_st["mid"], disable_notification=True)
        return True
    return False


def upload_photo(jpeg, caption):
    b = "----vst" + hashlib.md5(jpeg[:64]).hexdigest()
    parts = []
    for k, v in (("chat_id", str(ADMIN_ID)), ("caption", caption)):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="p.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + jpeg + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(f"{API}/sendPhoto", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
        return res["result"]["photo"][-1]["file_id"]
    except Exception as e:
        print("upload error", e)
        return None


def get_photo(fid):
    if fid in PHOTO_CACHE:
        return PHOTO_CACHE[fid]
    r = tg("getFile", file_id=fid)
    path = (r.get("result") or {}).get("file_path")
    if not path:
        return None
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=40) as f:
            data = f.read()
    except Exception as e:
        print("photo error", e)
        return None
    if len(PHOTO_CACHE) > 40:
        PHOTO_CACHE.clear()
    PHOTO_CACHE[fid] = data
    return data


def catalog_state():
    items = [{"id": p["id"], "name": p["name"], "price": p["price"], "desc": p.get("desc", ""),
              "out": bool(p.get("out")), "img": "photo?id=" + p["photo"]} for p in CUSTOM]
    return {"items": items, "ov": OV}


MAINT_TEXT = "🛠 Технічні роботи\n\nМагазин тимчасово закритий. Скоро повернемось — дякуємо за терпіння!"
MAINT_HTML = """<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Технічні роботи</title><style>
html,body{margin:0;height:100%;background:#0c0b10;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;box-sizing:border-box;background:radial-gradient(60% 40% at 50% 30%,rgba(139,92,246,.25),transparent 70%),#0c0b10}
.lg{font-size:30px;font-weight:900;letter-spacing:1px}.lg b{background:linear-gradient(90deg,#8b5cf6,#e879f9);-webkit-background-clip:text;background-clip:text;color:transparent}
.g{font-size:64px;margin:22px 0 8px;display:inline-block;animation:r 4s linear infinite}@keyframes r{to{transform:rotate(360deg)}}
h1{font-size:24px;margin:6px 0 10px}p{color:#a8a3b8;font-size:15px;line-height:1.5;max-width:320px;margin:0}
small{position:fixed;bottom:22px;font-size:10px;letter-spacing:3px;color:#6f6a80;font-weight:600}
</style></head><body><div class="lg"><b>VST</b> SHOP</div><div class="g">⚙️</div><h1>Технічні роботи</h1>
<p>Магазин тимчасово закритий. Скоро повернемось — дякуємо за терпіння!</p><small>#VST</small></body></html>"""


class H(SimpleHTTPRequestHandler):
    _cc = "no-cache, must-revalidate"

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", self._cc)
        super().end_headers()

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/video":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._json({"title": video_title((q.get("id") or [""])[0])})
        if path == "/products":
            return self._json(catalog_state())
        if path == "/photo":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fid = (q.get("id") or [""])[0]
            if not any(p["photo"] == fid for p in CUSTOM):
                return self._json({"ok": False}, 404)
            data = get_photo(fid)
            if not data:
                return self._json({"ok": False}, 502)
            self._cc = "public, max-age=86400"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self._cc = "no-cache, must-revalidate"
            return
        if path == "/stats":
            return self._json({"youtube": youtube_subs(), "tiktok": to_int(TT_FOLLOWERS)})
        if path == "/config":
            return self._json({"manager": MANAGER, "tiktok": TIKTOK, "youtube": YOUTUBE, "jar": JAR})
        if MAINT and path in ("/", "/index.html"):
            b = MAINT_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if path == "/":
            self.path = "/index.html"
        elif not path.lower().endswith((".jpg", ".png", ".html")):
            return self._json({"ok": False}, 404)
        return super().do_GET()

    def _body(self, limit=4_000_000):
        n = int(self.headers.get("Content-Length", 0))
        if n > limit:
            raise ValueError("too big")
        return json.loads(self.rfile.read(n))

    def do_POST(self):
        path = self.path.split("?")[0]
        if path in ("/me", "/admin/add", "/admin/del", "/admin/set"):
            return self._admin(path)
        if path != "/order":
            return self._json({"ok": False}, 404)
        if MAINT:
            return self._json({"ok": False, "error": "maintenance"}, 503)
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
                    f"Оплата: {esc(d.get('payment') or '—')}\nДоставка: {esc(d.get('delivery') or '—')}\nАдреса/відділення: {esc(d.get('np') or '—')}\nКоментар: {esc(d.get('note') or '—')}\n\n"
                    f"Telegram: {esc(uname)}")
            r = tg("sendMessage", chat_id=ADMIN_ID, text=text, parse_mode="HTML")
            return self._json({"ok": r.get("ok", False)}, 200 if r.get("ok") else 502)
        except Exception as e:
            print("order error", e)
            return self._json({"ok": False}, 400)

    def _admin(self, path):
        try:
            d = self._body()
        except Exception:
            return self._json({"ok": False}, 400)
        adm = is_admin(d.get("initData", ""))
        if path == "/me":
            return self._json({"admin": adm})
        if not adm:
            return self._json({"ok": False, "error": "forbidden"}, 403)
        import base64, re
        with LOCK:
            if path == "/admin/add":
                name = str(d.get("name", "")).strip()[:60]
                desc = str(d.get("desc", "")).strip()[:140]
                price = to_int(d.get("price"))
                if len(name) < 2 or not price or not (0 < price < 1_000_000):
                    return self._json({"ok": False, "error": "Вкажи назву і ціну"}, 400)
                if len(CUSTOM) >= 12:
                    return self._json({"ok": False, "error": "Забагато товарів (максимум 12). Видали зайвий."}, 400)
                try:
                    img = d.get("image", "")
                    jpeg = base64.b64decode(img.split(",", 1)[1] if "," in img else img)
                except Exception:
                    return self._json({"ok": False, "error": "Погане фото"}, 400)
                if len(jpeg) < 500 or len(jpeg) > 3_000_000:
                    return self._json({"ok": False, "error": "Додай фото (до 3 МБ)"}, 400)
                fid = upload_photo(jpeg, f"Новий товар: {name} — {price} грн")
                if not fid:
                    return self._json({"ok": False, "error": "Не вдалось завантажити фото"}, 502)
                CUSTOM.append({"id": "c" + format(int(time.time() * 1000), "x"), "name": name, "price": price,
                               "desc": desc, "photo": fid, "out": False})
                if not store_save():
                    CUSTOM.pop()
                    return self._json({"ok": False, "error": "Не вдалось зберегти (забагато даних)"}, 500)
            else:
                pid = str(d.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", pid):
                    return self._json({"ok": False, "error": "Не знайдено"}, 404)
                it = next((p for p in CUSTOM if p["id"] == pid), None)
                if path == "/admin/del":
                    if it:
                        CUSTOM.remove(it)
                    else:
                        OV.setdefault(pid, {})["hide"] = 1
                else:
                    tgt = it if it is not None else OV.setdefault(pid, {})
                    if "out" in d:
                        if it is not None:
                            tgt["out"] = bool(d["out"])
                        elif d["out"]:
                            tgt["out"] = 1
                        else:
                            tgt["out"] = 0
                    if "price" in d:
                        pr = to_int(d["price"])
                        if not pr or not (0 < pr < 1_000_000):
                            return self._json({"ok": False, "error": "Некоректна ціна"}, 400)
                        tgt["price"] = pr
                    if "hide" in d and it is None:
                        if d["hide"]:
                            tgt["hide"] = 1
                        else:
                            tgt.pop("hide", None)
                    if it is None and len(OV) > 24:
                        return self._json({"ok": False, "error": "Забагато змін"}, 400)
                if not store_save():
                    return self._json({"ok": False, "error": "Не вдалось зберегти"}, 500)
        return self._json(dict(catalog_state(), ok=True))

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
            if txt.startswith("/start") and MAINT:
                tg("sendMessage", chat_id=chat, text=MAINT_TEXT)
            elif txt.startswith("/start"):
                kb = {"inline_keyboard": [[{"text": "Відкрити магазин", "web_app": {"url": WEBAPP_URL}}]]}
                r2 = tg("sendPhoto", chat_id=chat, photo=f"{WEBAPP_URL}/banner.png", caption=WELCOME, reply_markup=kb)
                if not r2.get("ok"):
                    tg("sendMessage", chat_id=chat, text=WELCOME, reply_markup=kb)
            elif txt.startswith("/admin"):
                tg("sendMessage", chat_id=chat,
                   text=f"Твій chat id: {chat}\nВстав його в налаштування ADMIN_ID, і заявки приходитимуть сюди.")


if __name__ == "__main__":
    store_load()
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), H).serve_forever()
