import os, random, sqlite3, asyncio, httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()
VERIFY = os.getenv("IG_VERIFY")
API = "https://graph.instagram.com/v21.0"
CONF = [
    {"name": "auto", "token": os.getenv("IG_TOKEN"), "db": "/opt/risha-bot/users.db", "tg": "risha_podborki_bot",
     "btn": "Забрать подборку", "replies": ["Отправила в директ 📩", "Лови в директе 🔥", "Проверь директ ⚡", "Уже в директе 🚗"]},
    {"name": "neuro", "token": os.getenv("IG_TOKEN_NEURO"), "db": "/opt/neuro-bot/users.db", "tg": "neurorisha_bot",
     "btn": "Забрать материал", "replies": ["Отправила в директ 📩", "Лови в директе 🔥", "Проверь директ ⚡", "Уже в директе 💸"]},
]

# ---------- Лид-магниты neuro.risha: всё в keywords.json, выдача только после проверки подписки ----------
import json, re
KW_PATH = "/opt/neuro-bot/keywords.json"
BASE_URL = "https://neurorisha.ru/ig/f"

def magnets():
    """Читается при каждом сообщении: правишь keywords.json — изменения работают без перезапуска."""
    try:
        with open(KW_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as ex:
        print("KEYWORDS error", ex)
        return {}

def has_word(text, words):
    """Целое слово, а не кусок: «рост» сработает, а «просто» и «простой» — нет."""
    t = (text or "").lower().replace("ё", "е")
    for w in words:
        w = w.lower().replace("ё", "е").strip()
        if w and re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t):
            return True
    return False

def find_magnet(text, field="words"):
    for key, m in magnets().items():
        if has_word(text, m.get(field, [])):
            return key, m
    return None, None

ACC = {}
seen = set()
tries = {}
app = FastAPI()
# Только IPv4 + повторы при обрыве: у сервера нестабильная сеть до Instagram
client = httpx.AsyncClient(timeout=30, transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=3))

async def load_accounts():
    """Не падаем, если Instagram временно недоступен: попробуем снова при следующем вебхуке."""
    for a in CONF:
        if not a["token"]:
            continue
        try:
            r = await client.get(f"{API}/me", params={"fields": "id,user_id,username", "access_token": a["token"]})
        except Exception as ex:
            print("ACCOUNT", a["name"], "network error:", repr(ex))
            continue
        print("ACCOUNT", a["name"], r.status_code, r.text)
        if r.status_code == 200:
            d = r.json()
            for k in ("id", "user_id"):
                if d.get(k):
                    ACC[str(d[k])] = a

@app.on_event("startup")
async def startup():
    await load_accounts()

def find_camp(acc, text):
    t = (text or "").lower()
    con = sqlite3.connect(acc["db"])
    try:
        rows = con.execute("SELECT id, words, dm FROM campaigns ORDER BY id").fetchall()
    except Exception as ex:
        print("DB error", acc["name"], ex)
        rows = []
    finally:
        con.close()
    for cid, words, dm in rows:
        if any(w.strip() and w.strip() in t for w in words.split(",")):
            return cid, dm
    return None

async def post_msg(acc, recipient, message, tag):
    for attempt in range(3):
        try:
            r = await client.post(f"{API}/me/messages", params={"access_token": acc["token"]},
                                  json={"recipient": recipient, "message": message})
            print(tag, acc["name"], r.status_code, r.text)
            return r.status_code == 200
        except Exception as ex:
            print(tag, acc["name"], "network error, try", attempt + 1, repr(ex))
            await asyncio.sleep(2)
    return False

async def send_dm(acc, recipient, src, text):
    link = f"https://t.me/{acc['tg']}?start={src}"
    msg = {"attachment": {"type": "template", "payload": {
        "template_type": "button", "text": text,
        "buttons": [{"type": "web_url", "url": link, "title": acc["btn"]}]}}}
    if not await post_msg(acc, recipient, msg, "DM button:"):
        await post_msg(acc, recipient, {"text": f"{text}\n{link}"}, "DM text:")

async def ask(acc, recipient, text, title, payload, word="ЧЕК-ЛИСТ"):
    """Сообщение с одной кнопкой: сначала быстрый ответ, затем postback, затем просто текст."""
    qr = {"text": text, "quick_replies": [{"content_type": "text", "title": title, "payload": payload}]}
    if await post_msg(acc, recipient, qr, "ASK quick"):
        return
    pb = {"attachment": {"type": "template", "payload": {"template_type": "button", "text": text,
          "buttons": [{"type": "postback", "title": title, "payload": payload}]}}}
    if await post_msg(acc, recipient, pb, "ASK postback"):
        return
    await post_msg(acc, recipient, {"text": text + f"\n\nНапиши в ответ: {word}"}, "ASK text")

async def is_follower(acc, uid):
    try:
        r = await client.get(f"{API}/{uid}", params={"fields": "username,is_user_follow_business",
                                                     "access_token": acc["token"]})
    except Exception as ex:
        print("FOLLOW network error", repr(ex))
        return None
    print("FOLLOW", acc["name"], r.status_code, r.text)
    if r.status_code != 200:
        return None
    return r.json().get("is_user_follow_business")

async def send_pdf(acc, uid, key, m):
    rec = {"id": uid}
    url = f"{BASE_URL}/{key}.pdf"
    await post_msg(acc, rec, {"text": m["done"]}, "PDF text " + key)
    if await post_msg(acc, rec, {"attachment": {"type": "file", "payload": {"url": url}}}, "PDF file " + key):
        return
    btn = {"attachment": {"type": "template", "payload": {"template_type": "button", "text": "Твой файл 👇",
           "buttons": [{"type": "web_url", "url": url, "title": m["btn_file"]}]}}}
    if not await post_msg(acc, rec, btn, "PDF button " + key):
        await post_msg(acc, rec, {"text": url}, "PDF link " + key)

async def check_and_send(acc, uid, key, m):
    f = await is_follower(acc, uid)
    if f is None:
        print("FOLLOW unknown -> send anyway", uid, key)
        await send_pdf(acc, uid, key, m)
    elif f:
        tries.pop((uid, key), None)
        await send_pdf(acc, uid, key, m)
    else:
        n = tries.get((uid, key), 0)
        tries[(uid, key)] = n + 1
        word = (m.get("reply_words") or ["ЧЕК-ЛИСТ"])[0].upper()
        await ask(acc, {"id": uid}, m["sub"] if n == 0 else m["nosub"], m["btn_check"], "CHECK:" + key, word)

def hello(acc, recipient, key, m):
    word = (m.get("reply_words") or ["ЧЕК-ЛИСТ"])[0].upper()
    return ask(acc, recipient, m["hello"], m["btn_get"], "GET:" + key, word)

@app.get("/ig/checklist.pdf")
async def checklist():
    m = magnets().get("checklist", {})
    return FileResponse(m.get("file", "/opt/neuro-bot/files/checklist_claude.pdf"), media_type="application/pdf",
                        filename=m.get("file_name", "checklist.pdf"))

@app.get("/ig/f/{key}.pdf")
async def magnet_file(key: str):
    m = magnets().get(key)
    if not m or not os.path.exists(m.get("file", "")):
        return Response(status_code=404)
    return FileResponse(m["file"], media_type="application/pdf", filename=m.get("file_name", key + ".pdf"))

@app.get("/ig/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY:
        return Response(p.get("hub.challenge"))
    return Response(status_code=403)

@app.post("/ig/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("IN:", data)
    for e in data.get("entry", []):
        me = str(e.get("id"))
        acc = ACC.get(me)
        if not acc:
            await load_accounts()
            acc = ACC.get(me)
        if not acc:
            print("UNKNOWN ACCOUNT", me)
            continue
        neuro = acc["name"] == "neuro"
        for ch in e.get("changes", []):
            if ch.get("field") != "comments":
                continue
            v = ch.get("value", {})
            cid = v.get("id")
            if str(v.get("from", {}).get("id")) == me or cid in seen:
                continue
            text = (v.get("text") or "").lower()
            key, m = find_magnet(text) if neuro else (None, None)
            replies = acc["replies"]
            if key:
                seen.add(cid)
                await hello(acc, {"comment_id": cid}, key, m)
                replies = m.get("comment_replies") or replies
            else:
                c = find_camp(acc, v.get("text"))
                if not c:
                    continue
                seen.add(cid)
                await send_dm(acc, {"comment_id": cid}, f"c{c[0]}_igc", c[1])
            try:
                r = await client.post(f"{API}/{cid}/replies",
                                      params={"access_token": acc["token"], "message": random.choice(replies)})
                print("Reply:", acc["name"], r.status_code, r.text)
            except Exception as ex:
                print("Reply:", acc["name"], "network error", repr(ex))
        for m in e.get("messaging", []):
            uid = str(m.get("sender", {}).get("id"))
            if uid == me:
                continue
            msg = m.get("message", {}) or {}
            pbk = m.get("postback", {}) or {}
            mid = msg.get("mid") or pbk.get("mid")
            if msg.get("is_echo") or (mid and mid in seen):
                continue
            payload = pbk.get("payload") or (msg.get("quick_reply") or {}).get("payload")
            text = (msg.get("text") or "").lower()
            if neuro:
                key = None
                if payload and ":" in payload:
                    key = payload.split(":", 1)[1]
                elif payload in ("GET_PDF", "CHECK_SUB"):  # старые кнопки из прошлой версии
                    key = "checklist"
                mg = magnets()
                if key in mg:
                    seen.add(mid)
                    await check_and_send(acc, uid, key, mg[key])
                    continue
                key, m = find_magnet(text, "reply_words")
                if key:
                    seen.add(mid)
                    await check_and_send(acc, uid, key, m)
                    continue
                key, m = find_magnet(text)
                if key:
                    seen.add(mid)
                    await hello(acc, {"id": uid}, key, m)
                    continue
            c = find_camp(acc, msg.get("text"))
            if c:
                seen.add(mid)
                await send_dm(acc, {"id": uid}, f"c{c[0]}_igd", c[1])
    return {"ok": True}
