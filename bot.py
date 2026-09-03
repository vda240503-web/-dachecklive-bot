import os
import re
import asyncio
from datetime import datetime
from aiohttp import web, ClientSession, ClientTimeout
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
if not TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")

def is_admin(update):
    return update.effective_user and update.effective_user.id in ADMIN_IDS

async def check_fb(uid):
    if not re.fullmatch(r"\d{5,30}", uid):
        return {"state":"invalid","uid":uid,"url":None,"name":"Không xác định",
                "username":"Không xác định","created":"Không xác định"}
    url = f"https://www.facebook.com/profile.php?id={uid}"
    try:
        async with ClientSession(
            timeout=ClientTimeout(total=15),
            headers={"User-Agent": UA, "Accept-Language":"vi-VN,vi;q=0.9,en;q=0.8"}
        ) as s:
            async with s.get(url, allow_redirects=True) as r:
                body = (await r.text(errors="ignore"))[:500000]
                low = body.lower()
                blocked = any(x in low for x in (
                    "log in to facebook", "đăng nhập facebook",
                    "checkpoint", "security check"
                ))
                if r.status == 200 and not blocked:
                    state = "live"
                else:
                    state = "unknown"
                # Public HTML parsing is intentionally conservative.
                name = "Không xác định"
                m = re.search(r'<title>(.*?)</title>', body, re.I|re.S)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()
                    title = re.sub(r"\s*\|\s*Facebook.*$", "", title, flags=re.I)
                    if 1 < len(title) < 120:
                        name = title
                return {"state":state,"uid":uid,"url":str(r.url),
                        "name":name,"username":"Không xác định",
                        "created":"Không xác định","http":r.status}
    except Exception:
        return {"state":"unknown","uid":uid,"url":url,"name":"Không xác định",
                "username":"Không xác định","created":"Không xác định"}

def card_text(r, note="583", processing="Không xác định"):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S (UTC+7)")
    if r["state"] == "live":
        status = "🟢 Trạng thái: ĐÃ HOẠT ĐỘNG ✅"
    elif r["state"] == "invalid":
        status = "🔴 Trạng thái: UID KHÔNG HỢP LỆ ❌"
    else:
        status = "⚪ Trạng thái: KHÔNG XÁC MINH ĐƯỢC"
    fb = f'<a href="{r["url"]}">Link FB</a>' if r["url"] else "Không có"
    return (
        "🔵 <b>Thêm theo dõi thành công</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🆔 UID: <code>{r['uid']}</code> · 🔗 {fb}\n"
        f"🟢 {status}\n"
        f"📝 Ghi chú: {note}\n"
        f"⏱ Thời gian xử lý: {processing}\n"
        f"📅 Thời gian tạo: {now}\n"
        f"👤 Username lúc add: {r['username']}\n"
        f"👤 Tên hiển thị: {r['name']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

def keyboard(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Cập nhật", callback_data=f"update:{uid}"),
         InlineKeyboardButton("🙈 Hiện thông tin", callback_data=f"info:{uid}")],
        [InlineKeyboardButton("❌ Hủy kèo", callback_data=f"cancel:{uid}"),
         InlineKeyboardButton("✅ Done kèo", callback_data=f"done:{uid}")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Facebook UID Checker</b>\n\n"
        "Dùng:\n<code>/add 100062825581259</code>\n"
        "hoặc:\n<code>/check 100062825581259</code>",
        parse_mode="HTML"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Bạn không có quyền dùng chức năng này.")
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text("Dùng: <code>/add UID</code>", parse_mode="HTML")
        return
    uids = [x for x in re.split(r"[\s,]+", raw) if x]
    if len(uids) > 20:
        await update.message.reply_text("❌ Tối đa 20 UID mỗi lần.")
        return
    msg = await update.message.reply_text(f"🔎 Đang kiểm tra {len(uids)} UID...")
    for i, uid in enumerate(uids):
        r = await check_fb(uid)
        await msg.edit_text(
            card_text(r, note="583", processing=f"{i+1}/{len(uids)}"),
            parse_mode="HTML", disable_web_page_preview=False,
            reply_markup=keyboard(uid)
        )
        if i < len(uids)-1:
            await asyncio.sleep(.4)

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Alias cho /add
    await add(update, context)

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, uid = q.data.split(":", 1)
    if action == "update":
        await q.message.reply_text(
            f"✏️ Cập nhật UID <code>{uid}</code>\n"
            f"Dùng <code>/add {uid}</code> để kiểm tra lại.",
            parse_mode="HTML"
        )
    elif action == "info":
        await q.message.reply_text(
            f"🆔 UID: <code>{uid}</code>\n"
            "🔎 Thông tin chi tiết chỉ hiển thị những dữ liệu Facebook trả công khai.",
            parse_mode="HTML"
        )
    elif action == "cancel":
        await q.message.edit_reply_markup(reply_markup=None)
        await q.message.reply_text(f"❌ Đã hủy kèo UID <code>{uid}</code>", parse_mode="HTML")
    elif action == "done":
        await q.message.reply_text(f"✅ Done kèo UID <code>{uid}</code>", parse_mode="HTML")

async def health(request):
    return web.Response(text="OK")

async def main():
    port = int(os.getenv("PORT", "10000"))
    webapp = web.Application()
    webapp.router.add_get("/healthz", health)
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CallbackQueryHandler(buttons))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("Telegram bot running")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
