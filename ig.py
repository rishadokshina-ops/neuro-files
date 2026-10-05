import os, random, sqlite3, httpx
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

# ---------- Лид-магнит neuro.risha: чек-лист за подписку ----------
LEAD_WORDS = ["claude", "клод", "клауд"]
CHECK_WORDS = ["чек-лист", "чеклист", "чек лист"]
PDF_PATH = "/opt/neuro-bot/files/checklist_claude.pdf"
PDF_URL = "https://neurorisha.ru/ig/checklist.pdf"
T_HELLO = ("Привет! Я Риша 🔥 Показываю на своём примере, как с нуля автоматизировать бизнес с помощью Claude. "
           "Твой чек-лист «10 процессов бизнеса, которые можно автоматизировать в Claude» уже готов 👇")
B_GET = "Получить чек-лист 📄"
T_SUB = ("Почти твой! 🔥 Чек-лист отдаю своим: подпишись на @neuro.risha. Здесь я каждый день показываю путь с нуля: "
         "кейсы, цифры, рабочие связки нейросетей. Подписался(ась)? Жми 👇")
T_NOSUB = "Пока не вижу подписку 🙈 Проверь, что нажал(а) «Подписаться» на @neuro.risha, и жми ещё раз 👇"
B_CHECK = "Готово, проверить ✅"
T_PDF = ("Лови свой чек-лист 🔥 Отметь галочками, что сейчас делаешь руками, и начни с 1–2 пунктов. "
         "Дальше будет ещё мощнее, следи за сторис 💸")
B_PDF = "Скачать чек-лист 📄"

ACC = {}
seen = set()
tries = {}
app = FastAPI()
client = httpx.AsyncClient(timeout=20)

@app.on_event("startup")
async def load_accounts():
    for a in CONF:
        if not a["token"]:
            continue
        r = await client.get(f"{API}/me", params={"fields": "id,user_id,username", "access_token": a["token"]})
        print("ACCOUNT", a["name"], r.status_code, r.text)
        if r.status_code == 200:
            d = r.json()
            for k in ("id", "user_id"):
                if d.get(k):
                    ACC[str(d[k])] = a

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
    r = await client.post(f"{API}/me/messages", params={"access_token": acc["token"]},
                          json={"recipient": recipient, "message": message})
    print(tag, acc["name"], r.status_code, r.text)
    return r.status_code == 200

async def send_dm(acc, recipient, src, text):
    link = f"https://t.me/{acc['tg']}?start={src}"
    msg = {"attachment": {"type": "template", "payload": {
        "template_type": "button", "text": text,
        "buttons": [{"type": "web_url", "url": link, "title": acc["btn"]}]}}}
    if not await post_msg(acc, recipient, msg, "DM button:"):
        await post_msg(acc, recipient, {"text": f"{text}\n{link}"}, "DM text:")

async def ask(acc, recipient, text, title, payload):
    """Сообщение с одной кнопкой: сначала быстрый ответ, затем postback, затем просто текст."""
    qr = {"text": text, "quick_replies": [{"content_type": "text", "title": title, "payload": payload}]}
    if await post_msg(acc, recipient, qr, "ASK quick"):
        return
    pb = {"attachment": {"type": "template", "payload": {"template_type": "button", "text": text,
          "buttons": [{"type": "postback", "title": title, "payload": payload}]}}}
    if await post_msg(acc, recipient, pb, "ASK postback"):
        return
    await post_msg(acc, recipient, {"text": text + "\n\nНапиши в ответ: ЧЕК-ЛИСТ"}, "ASK text")

async def is_follower(acc, uid):
    r = await client.get(f"{API}/{uid}", params={"fields": "username,is_user_follow_business",
                                                 "access_token": acc["token"]})
    print("FOLLOW", acc["name"], r.status_code, r.text)
    if r.status_code != 200:
        return None
    return r.json().get("is_user_follow_business")

async def send_pdf(acc, uid):
    rec = {"id": uid}
    await post_msg(acc, rec, {"text": T_PDF}, "PDF text")
    if await post_msg(acc, rec, {"attachment": {"type": "file", "payload": {"url": PDF_URL}}}, "PDF file"):
        return
    btn = {"attachment": {"type": "template", "payload": {"template_type": "button", "text": "Твой чек-лист 👇",
           "buttons": [{"type": "web_url", "url": PDF_URL, "title": B_PDF}]}}}
    if not await post_msg(acc, rec, btn, "PDF button"):
        await post_msg(acc, rec, {"text": PDF_URL}, "PDF link")

async def check_and_send(acc, uid):
    f = await is_follower(acc, uid)
    if f is None:
        print("FOLLOW unknown -> send anyway", uid)
        await send_pdf(acc, uid)
    elif f:
        tries.pop(uid, None)
        await send_pdf(acc, uid)
    else:
        n = tries.get(uid, 0)
        tries[uid] = n + 1
        await ask(acc, {"id": uid}, T_SUB if n == 0 else T_NOSUB, B_CHECK, "CHECK_SUB")

@app.get("/ig/checklist.pdf")
async def checklist():
    return FileResponse(PDF_PATH, media_type="application/pdf", filename="checklist_10_processov_claude.pdf")

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
            if neuro and any(w in text for w in LEAD_WORDS):
                seen.add(cid)
                await ask(acc, {"comment_id": cid}, T_HELLO, B_GET, "GET_PDF")
            else:
                c = find_camp(acc, v.get("text"))
                if not c:
                    continue
                seen.add(cid)
                await send_dm(acc, {"comment_id": cid}, f"c{c[0]}_igc", c[1])
            r = await client.post(f"{API}/{cid}/replies",
                                  params={"access_token": acc["token"], "message": random.choice(acc["replies"])})
            print("Reply:", acc["name"], r.status_code, r.text)
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
                if payload in ("GET_PDF", "CHECK_SUB") or any(w in text for w in CHECK_WORDS):
                    seen.add(mid)
                    await check_and_send(acc, uid)
                    continue
                if any(w in text for w in LEAD_WORDS):
                    seen.add(mid)
                    await ask(acc, {"id": uid}, T_HELLO, B_GET, "GET_PDF")
                    continue
            c = find_camp(acc, msg.get("text"))
            if c:
                seen.add(mid)
                await send_dm(acc, {"id": uid}, f"c{c[0]}_igd", c[1])
    return {"ok": True}
