import os
import asyncio
import httpx
import io
import requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

# ── الإعدادات ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_KEY     = os.environ["OPENAI_KEY"]
TG_API         = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
FONT_DIR       = Path("fonts")
ARABIC_FONT    = FONT_DIR / "Amiri-Bold.ttf"
LATIN_FONT     = FONT_DIR / "PlayfairDisplay-Bold.ttf"

user_states    = {}  # {chat_id: {step, gender, name, lang}}
last_update_id = 0

# ── تحميل الخطوط ───────────────────────────────────────────────────────────
def setup_fonts():
    FONT_DIR.mkdir(exist_ok=True)
    fonts = {
        ARABIC_FONT: "https://github.com/aliftype/amiri/raw/main/fonts/Amiri-Bold.ttf",
        LATIN_FONT:  "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf",
    }
    for path, url in fonts.items():
        if not path.exists():
            print(f"⬇️ Downloading {path.name}...")
            r = requests.get(url, timeout=30)
            path.write_bytes(r.content)
            print(f"✅ {path.name} ready")

# ── نص عربي صح ────────────────────────────────────────────────────────────
def ar(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))

# ── بروميت DALL-E ─────────────────────────────────────────────────────────
def get_prompt(gender: str) -> str:
    if gender == "boy":
        return (
            "A luxurious soft baby boy birth announcement card background. "
            "Pastel sky blue and white watercolor gradient, delicate golden stars, "
            "soft clouds, subtle Islamic geometric ornaments. "
            "No text, no people, no faces. Clean, elegant, professional. "
            "Gold foil accents, high quality, Instagram-worthy."
        )
    else:
        return (
            "A luxurious soft baby girl birth announcement card background. "
            "Blush pink and white watercolor gradient, delicate golden flowers, "
            "roses, butterflies, subtle floral ornaments. "
            "No text, no people, no faces. Clean, elegant, professional. "
            "Gold foil accents, high quality, Instagram-worthy."
        )

# ── توليد الصورة من DALL-E 3 ──────────────────────────────────────────────
async def generate_bg(prompt: str) -> bytes:
    async with httpx.AsyncClient(timeout=90) as client:
        res = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "model":   "dall-e-2",
                "prompt":  prompt,
                "size":    "1024x1024",
                "n":       1
            }
        )
        data    = res.json()
        if "error" in data:
            raise Exception(f"OpenAI: {data['error'].get('message', str(data['error']))}")
        if "data" not in data:
            raise Exception(f"OpenAI response: {str(data)[:300]}")
        img_url = data["data"][0]["url"]
        img_res = await client.get(img_url)
        return img_res.content

# ── إضافة النص على الصورة ──────────────────────────────────────────────────
def add_text(img_bytes: bytes, name: str, gender: str, lang: str) -> bytes:
    img    = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w, h   = img.size

    # ── بلاطة بيضاء شبه شفافة في المنتصف ──
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d       = ImageDraw.Draw(overlay)
    pad     = 90
    top_r   = h // 3
    bot_r   = h * 2 // 3
    d.rounded_rectangle([pad, top_r, w - pad, bot_r], radius=24, fill=(255, 255, 255, 190))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # ── ألوان ──
    gold  = (170, 128, 0)
    dark  = (50,  35, 15)

    is_ar = (lang == "arabic")
    font_path = str(ARABIC_FONT) if is_ar else str(LATIN_FONT)

    try:
        f_top  = ImageFont.truetype(font_path, 52)
        f_name = ImageFont.truetype(font_path, 100)
        f_bot  = ImageFont.truetype(font_path, 44)
    except Exception:
        f_top = f_name = f_bot = ImageFont.load_default()

    cx = w // 2

    if is_ar:
        top_txt  = ar("الحمدلله")
        name_txt = ar(name)
        bot_txt  = ar("مبارك المولودة" if gender == "girl" else "مبارك المولود")
    else:
        top_txt  = "Alhamdulillah"
        name_txt = name.title()
        bot_txt  = "Welcome to the World"

    # ── رسم النصوص ──
    top_y  = top_r  + 35
    name_y = (top_r + bot_r) // 2
    bot_y  = bot_r  - 35

    # ظل خفيف
    for dx, dy in [(2, 2), (-2, 2), (2, -2), (-2, -2)]:
        draw.text((cx + dx, top_y  + dy), top_txt,  font=f_top,  fill=(200,200,200,120), anchor="mt")
        draw.text((cx + dx, name_y + dy), name_txt, font=f_name, fill=(200,200,200,120), anchor="mm")
        draw.text((cx + dx, bot_y  + dy), bot_txt,  font=f_bot,  fill=(200,200,200,120), anchor="mb")

    draw.text((cx, top_y),  top_txt,  font=f_top,  fill=gold, anchor="mt")
    draw.text((cx, name_y), name_txt, font=f_name, fill=dark, anchor="mm")
    draw.text((cx, bot_y),  bot_txt,  font=f_bot,  fill=gold, anchor="mb")

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()

# ── Telegram API ───────────────────────────────────────────────────────────
async def tg_send(chat_id, text: str, markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if markup:
        payload["reply_markup"] = markup
    async with httpx.AsyncClient() as c:
        await c.post(f"{TG_API}/sendMessage", json=payload)

async def tg_photo(chat_id, photo: bytes, caption=""):
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post(
            f"{TG_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("mawlood.jpg", photo, "image/jpeg")}
        )

async def tg_answer(cq_id: str):
    async with httpx.AsyncClient() as c:
        await c.post(f"{TG_API}/answerCallbackQuery", json={"callback_query_id": cq_id})

async def tg_updates():
    global last_update_id
    async with httpx.AsyncClient(timeout=35) as c:
        r = await c.get(f"{TG_API}/getUpdates", params={"offset": last_update_id + 1, "timeout": 25})
        return r.json().get("result", [])

# ── لوحات المفاتيح ─────────────────────────────────────────────────────────
def gender_kb():
    return {"inline_keyboard": [[
        {"text": "👦 ولد",  "callback_data": "g_boy"},
        {"text": "👧 بنت",  "callback_data": "g_girl"}
    ]]}

def lang_kb():
    return {"inline_keyboard": [[
        {"text": "🇸🇦 عربي",    "callback_data": "l_arabic"},
        {"text": "🇺🇸 English", "callback_data": "l_english"}
    ]]}

# ── معالجة التحديثات ───────────────────────────────────────────────────────
async def handle_update(update: dict):
    global last_update_id
    last_update_id = update["update_id"]

    # ── رسالة نصية ──
    if "message" in update:
        msg     = update["message"]
        chat_id = str(msg["chat"]["id"])
        text    = msg.get("text", "").strip()

        if text in ["/start", "/help"]:
            user_states[chat_id] = {"step": 0}
            await tg_send(chat_id,
                "👶 *بوت بشارة المولود*\n\nاختر جنس المولود:",
                gender_kb()
            )
            return

        state = user_states.get(chat_id, {})

        # ينتظر الاسم
        if state.get("step") == 1 and text and not text.startswith("/"):
            user_states[chat_id]["name"] = text
            user_states[chat_id]["step"] = 2
            await tg_send(chat_id,
                f"✅ الاسم: *{text}*\n\nاختر لغة البشارة:",
                lang_kb()
            )
            return

        # أي رسالة ثانية → ابدأ
        if text and not text.startswith("/"):
            user_states[chat_id] = {"step": 0}
            await tg_send(chat_id, "👶 اختر جنس المولود:", gender_kb())

    # ── Callback ──
    elif "callback_query" in update:
        cq      = update["callback_query"]
        await tg_answer(cq["id"])
        chat_id = str(cq["message"]["chat"]["id"])
        data    = cq["data"]
        state   = user_states.get(chat_id, {})

        if data in ["g_boy", "g_girl"]:
            gender = "boy" if data == "g_boy" else "girl"
            user_states[chat_id] = {"step": 1, "gender": gender}
            label = "ولد 👦" if gender == "boy" else "بنت 👧"
            await tg_send(chat_id, f"✅ {label}\n\nاكتب اسم المولود/المولودة:")
            return

        if data in ["l_arabic", "l_english"]:
            lang           = "arabic" if data == "l_arabic" else "english"
            state["lang"]  = lang
            state["step"]  = 3
            user_states[chat_id] = state

            name   = state.get("name", "")
            gender = state.get("gender", "boy")

            await tg_send(chat_id, "🎨 جاري تصميم البشارة... انتظر 30 ثانية ✨")

            try:
                bg_bytes  = await generate_bg(get_prompt(gender))
                final_img = add_text(bg_bytes, name, gender, lang)

                if lang == "arabic":
                    caption = f"🎉 مبارك المولود{'ة' if gender == 'girl' else ''}\n👶 {name}"
                else:
                    caption = f"🎉 Congratulations!\n👶 Welcome, {name.title()}"

                await tg_photo(chat_id, final_img, caption)
                await tg_send(chat_id, "أرسل /start لبشارة جديدة 👶")

            except Exception as e:
                print(f"❌ Error: {e}")
                await tg_send(chat_id, "❌ حدث خطأ، حاول مرة ثانية\n/start")

            user_states.pop(chat_id, None)

# ── Polling ────────────────────────────────────────────────────────────────
async def polling_loop():
    while True:
        try:
            updates = await tg_updates()
            for u in updates:
                await handle_update(u)
        except Exception as e:
            print(f"Polling error: {e}")
            await asyncio.sleep(5)

async def main():
    print("⬇️  Setting up fonts...")
    setup_fonts()
    print("✅ Mawlood Bot شغال!")
    await polling_loop()

if __name__ == "__main__":
    asyncio.run(main())
