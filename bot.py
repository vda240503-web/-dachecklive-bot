import os, sqlite3, logging, asyncio, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}
SHOP_NAME = os.getenv("SHOP_NAME","DAChecklive Shop")
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT","10000"))
DB = "shop.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, item TEXT NOT NULL, sold INTEGER DEFAULT 0, order_id INTEGER);
    CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product_id INTEGER, price INTEGER, item TEXT, status TEXT DEFAULT 'completed', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    """)
    c.commit(); c.close()

def upsert_user(u):
    c=db(); c.execute("INSERT INTO users(id,username) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username",(u.id,u.username or "")); c.commit(); c.close()

def money(n): return f"{int(n):,}đ"

def main_menu(uid):
    rows=[
      [("💰 Nạp tiền","deposit"),("🛒 Mua hàng","shop")],
      [("👤 Tài khoản","account"),("🧾 Đơn hàng","orders")],
      [("🔎 Tìm kiếm TK","search"),("📋 Lịch sử nạp","deposits")],
      [("🔍 Check UID","checkuid")]
    ]
    if uid in ADMIN_IDS: rows.append([("🛠 Admin","admin")])
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=d) for t,d in r] for r in rows])

async def start(update, ctx):
    u=update.effective_user; upsert_user(u)
    c=db(); r=c.execute("SELECT balance FROM users WHERE id=?",(u.id,)).fetchone(); c.close()
    await update.message.reply_text(f"👋 Chào {u.first_name}!\n\n🏪 *{SHOP_NAME}*\n💳 Số dư: *{money(r['balance'])}*\n\nChọn chức năng bên dưới.", parse_mode="Markdown", reply_markup=main_menu(u.id))

async def menu(update,ctx):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    upsert_user(q.from_user); d=q.data
    if d=="home":
        await q.edit_message_text("🏠 Menu chính", reply_markup=main_menu(uid))
    elif d=="account":
        c=db(); r=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); c.close()
        await q.edit_message_text(f"👤 *Tài khoản*\n\n🆔 UserID: `{uid}`\n👤 @{r['username'] or '—'}\n💰 Số dư: *{money(r['balance'])}*",parse_mode="Markdown",reply_markup=back())
    elif d=="shop": await show_shop(q)
    elif d=="orders": await show_orders(q,uid)
    elif d=="deposits": await show_deposits(q,uid)
    elif d=="deposit":
        ctx.user_data["state"]="deposit"
        await q.edit_message_text(f"💰 *Nạp tiền*\n\nNhập số tiền muốn nạp (tối thiểu {money(MIN_DEPOSIT)}).\nVí dụ: `50000`",parse_mode="Markdown",reply_markup=back())
    elif d=="search":
        ctx.user_data["state"]="search"; await q.edit_message_text("🔎 Nhập UserID Telegram cần tìm:",reply_markup=back())
    elif d=="checkuid":
        ctx.user_data["state"]="checkuid"; await q.edit_message_text("🔍 Nhập UID số cần kiểm tra.\n\nChức năng này chỉ kiểm tra định dạng UID trong hệ thống bot, không truy cập tài khoản bên ngoài.",reply_markup=back())
    elif d=="admin":
        if uid in ADMIN_IDS: await admin_menu(q)
    elif d.startswith("buy:"):
        pid=int(d.split(":")[1]); await buy(q,uid,pid)
    elif d.startswith("ad:"):
        if uid in ADMIN_IDS: await admin_action(q,ctx,d)

def back(): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại",callback_data="home")]])

async def show_shop(q):
    c=db(); ps=c.execute("SELECT p.*, COUNT(s.id) stock FROM products p LEFT JOIN stock s ON s.product_id=p.id AND s.sold=0 WHERE p.active=1 GROUP BY p.id ORDER BY p.id DESC").fetchall(); c.close()
    if not ps: text="🛒 *Cửa hàng*\n\nHiện chưa có sản phẩm."
    else: text="🛒 *Cửa hàng*\n\n" + "\n".join(f"• {p['name']} — *{money(p['price'])}* — Kho: {p['stock']}" for p in ps)
    kb=[[InlineKeyboardButton(f"🛍 {p['name']} • {money(p['price'])}",callback_data=f"buy:{p['id']}")] for p in ps]
    kb.append([InlineKeyboardButton("⬅️ Quay lại",callback_data="home")])
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

async def buy(q,uid,pid):
    c=db()
    p=c.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
    if not p: c.close(); return await q.answer("Sản phẩm không tồn tại",show_alert=True)
    u=c.execute("SELECT balance FROM users WHERE id=?",(uid,)).fetchone()
    s=c.execute("SELECT * FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1",(pid,)).fetchone()
    if not s: c.close(); return await q.answer("❌ Sản phẩm đã hết hàng",show_alert=True)
    if u["balance"] < p["price"]: c.close(); return await q.answer("❌ Số dư không đủ",show_alert=True)
    c.execute("UPDATE users SET balance=balance-? WHERE id=?",(p["price"],uid))
    c.execute("INSERT INTO orders(user_id,product_id,price,item,status) VALUES(?,?,?,?,?)",(uid,pid,p["price"],s["item"],"completed"))
    oid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("UPDATE stock SET sold=1,order_id=? WHERE id=?",(oid,s["id"]))
    c.commit()
    bal=c.execute("SELECT balance FROM users WHERE id=?",(uid,)).fetchone()["balance"]; c.close()
    await q.edit_message_text(f"✅ *Mua hàng thành công!*\n\n📦 Sản phẩm: *{p['name']}*\n🧾 Đơn hàng: `#{oid}`\n💰 Giá: {money(p['price'])}\n\n🎁 *Sản phẩm của bạn:*\n`{s['item']}`\n\n💳 Số dư còn lại: *{money(bal)}*",parse_mode="Markdown",reply_markup=back())

async def show_orders(q,uid):
    c=db(); rs=c.execute("SELECT o.*,p.name FROM orders o LEFT JOIN products p ON p.id=o.product_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20",(uid,)).fetchall(); c.close()
    text="🧾 *Lịch sử đơn hàng*\n\n" + ("\n".join(f"#{r['id']} • {r['name']} • {money(r['price'])} • {r['status']}" for r in rs) if rs else "Chưa có đơn hàng.")
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=back())

async def show_deposits(q,uid):
    c=db(); rs=c.execute("SELECT * FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 20",(uid,)).fetchall(); c.close()
    text="📋 *Lịch sử nạp*\n\n" + ("\n".join(f"#{r['id']} • {money(r['amount'])} • {r['status']}" for r in rs) if rs else "Chưa có giao dịch.")
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=back())

async def admin_menu(q):
    kb=[
      [InlineKeyboardButton("➕ Thêm sản phẩm",callback_data="ad:add"),InlineKeyboardButton("✏️ Sửa SP",callback_data="ad:edit")],
      [InlineKeyboardButton("🗑 Xóa SP",callback_data="ad:delete"),InlineKeyboardButton("📦 Quản lý kho",callback_data="ad:stock")],
      [InlineKeyboardButton("💳 Duyệt nạp",callback_data="ad:deposits"),InlineKeyboardButton("🧾 Đơn hàng",callback_data="ad:orders")],
      [InlineKeyboardButton("👥 Người dùng",callback_data="ad:users"),InlineKeyboardButton("📊 Thống kê",callback_data="ad:stats")],
      [InlineKeyboardButton("⬅️ Menu chính",callback_data="home")]
    ]
    await q.edit_message_text("🛠 *ADMIN PANEL*\n\nQuản lý sản phẩm, kho, nạp tiền và đơn hàng.",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

async def admin_action(q,ctx,d):
    act=d.split(":")[1]
    if act=="add":
        ctx.user_data["state"]="add"; await q.edit_message_text("➕ Nhập theo mẫu:\n`Tên sản phẩm | Giá`\nVí dụ: `Via A | 50000`",parse_mode="Markdown",reply_markup=back())
    elif act=="edit":
        ctx.user_data["state"]="edit"; await q.edit_message_text("✏️ Nhập:\n`ID | Tên mới | Giá mới`",parse_mode="Markdown",reply_markup=back())
    elif act=="delete":
        ctx.user_data["state"]="delete"; await q.edit_message_text("🗑 Nhập ID sản phẩm cần xóa:",reply_markup=back())
    elif act=="stock":
        ctx.user_data["state"]="stock"; await q.edit_message_text("📦 Nhập:\n`ID sản phẩm | mỗi dòng 1 sản phẩm`\nVí dụ:\n`2 | account1:pass1\n2 | account2:pass2`",reply_markup=back())
    elif act=="deposits": await admin_deposits(q)
    elif act=="orders": await admin_orders(q)
    elif act=="users": await admin_users(q)
    elif act=="stats": await admin_stats(q)

async def admin_deposits(q):
    c=db(); rs=c.execute("SELECT d.*,u.username FROM deposits d LEFT JOIN users u ON u.id=d.user_id WHERE d.status='pending' ORDER BY d.id").fetchall(); c.close()
    kb=[[InlineKeyboardButton(f"✅ #{r['id']} {money(r['amount'])}",callback_data=f"ad:approve:{r['id']}"),InlineKeyboardButton("❌",callback_data=f"ad:reject:{r['id']}")] for r in rs]
    await q.edit_message_text("💳 *Yêu cầu nạp đang chờ*\n\n"+("\n".join(f"#{r['id']} • {r['user_id']} • {money(r['amount'])} • @{r['username'] or '—'}" for r in rs) if rs else "Không có yêu cầu."),parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb+[[InlineKeyboardButton("⬅️ Admin",callback_data="admin")]]))

async def admin_orders(q):
    c=db(); rs=c.execute("SELECT o.*,p.name FROM orders o LEFT JOIN products p ON p.id=o.product_id ORDER BY o.id DESC LIMIT 30").fetchall(); c.close()
    text="🧾 *Đơn hàng gần đây*\n\n"+("\n".join(f"#{r['id']} • U:{r['user_id']} • {r['name']} • {money(r['price'])} • {r['status']}" for r in rs) if rs else "Chưa có đơn.")
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin",callback_data="admin")]]))

async def admin_users(q):
    c=db(); rs=c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 30").fetchall(); c.close()
    text="👥 *Người dùng*\n\n"+("\n".join(f"{r['id']} • @{r['username'] or '—'} • {money(r['balance'])}" for r in rs) if rs else "Chưa có.")
    await q.edit_message_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin",callback_data="admin")]]))

async def admin_stats(q):
    c=db()
    u=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]; p=c.execute("SELECT COUNT(*) n FROM products WHERE active=1").fetchone()["n"]
    s=c.execute("SELECT COUNT(*) n FROM stock WHERE sold=0").fetchone()["n"]; o=c.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    rev=c.execute("SELECT COALESCE(SUM(price),0) n FROM orders").fetchone()["n"]; c.close()
    await q.edit_message_text(f"📊 *THỐNG KÊ*\n\n👥 Users: {u}\n🛒 Sản phẩm: {p}\n📦 Hàng tồn: {s}\n🧾 Đơn hàng: {o}\n💰 Doanh số: {money(rev)}",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin",callback_data="admin")]]))

async def text_handler(update,ctx):
    u=update.effective_user; upsert_user(u); st=ctx.user_data.get("state"); text=update.message.text.strip()
    if st=="deposit":
        try: amount=int(text.replace(",","").replace(".",""))
        except: return await update.message.reply_text("❌ Số tiền không hợp lệ.")
        if amount<MIN_DEPOSIT: return await update.message.reply_text(f"❌ Tối thiểu {money(MIN_DEPOSIT)}.")
        c=db(); c.execute("INSERT INTO deposits(user_id,amount) VALUES(?,?)",(u.id,amount)); did=c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.commit(); c.close()
        ctx.user_data.clear()
        return await update.message.reply_text(f"💳 Đã tạo yêu cầu nạp #{did} — {money(amount)}.\n\nAdmin sẽ kiểm tra và cộng tiền.",reply_markup=back())
    if st=="search":
        if not text.isdigit(): return await update.message.reply_text("❌ UserID phải là số.")
        c=db(); r=c.execute("SELECT id,username,balance FROM users WHERE id=?",(int(text),)).fetchone(); c.close()
        ctx.user_data.clear(); return await update.message.reply_text(f"🔎 {r['id']} • @{r['username'] or '—'} • {money(r['balance'])}" if r else "❌ Không tìm thấy UserID trong shop.",reply_markup=back())
    if st=="checkuid":
        ctx.user_data.clear(); return await update.message.reply_text("✅ UID hợp lệ về mặt định dạng." if text.isdigit() else "❌ UID phải là chuỗi số.",reply_markup=back())
    if u.id not in ADMIN_IDS: return
    c=db()
    try:
        if st=="add":
            name,price=text.split("|",1); c.execute("INSERT INTO products(name,price) VALUES(?,?)",(name.strip(),int(price.strip())))
            c.commit(); await update.message.reply_text("✅ Đã thêm sản phẩm.",reply_markup=back())
        elif st=="edit":
            pid,name,price=[x.strip() for x in text.split("|",2)]; c.execute("UPDATE products SET name=?,price=? WHERE id=?",(name,int(price),int(pid))); c.commit(); await update.message.reply_text("✅ Đã sửa sản phẩm.",reply_markup=back())
        elif st=="delete":
            pid=int(text); c.execute("UPDATE products SET active=0 WHERE id=?",(pid,)); c.commit(); await update.message.reply_text("✅ Đã ẩn/xóa sản phẩm.",reply_markup=back())
        elif st=="stock":
            n=0
            for line in text.splitlines():
                pid,item=[x.strip() for x in line.split("|",1)]
                c.execute("INSERT INTO stock(product_id,item) VALUES(?,?)",(int(pid),item)); n+=1
            c.commit(); await update.message.reply_text(f"✅ Đã thêm {n} sản phẩm vào kho.",reply_markup=back())
    except Exception as e:
        await update.message.reply_text(f"❌ Dữ liệu không đúng: {e}")
    finally:
        c.close(); ctx.user_data.clear()

async def admin_callback(update,ctx):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    if uid not in ADMIN_IDS: return
    d=q.data
    if d.startswith("ad:approve:") or d.startswith("ad:reject:"):
        approve=d.startswith("ad:approve:"); did=int(d.split(":")[2]); c=db()
        r=c.execute("SELECT * FROM deposits WHERE id=? AND status='pending'",(did,)).fetchone()
        if not r: c.close(); return await q.answer("Đã xử lý hoặc không tồn tại",show_alert=True)
        if approve:
            c.execute("UPDATE users SET balance=balance+? WHERE id=?",(r["amount"],r["user_id"])); c.execute("UPDATE deposits SET status='approved' WHERE id=?",(did,))
        else: c.execute("UPDATE deposits SET status='rejected' WHERE id=?",(did,))
        c.commit(); c.close(); await q.edit_message_text(f"✅ Đã {'duyệt' if approve else 'từ chối'} yêu cầu #{did}.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin",callback_data="admin")]]))

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self,*args): pass

def health_server():
    port=int(os.getenv("PORT","10000")); HTTPServer(("0.0.0.0",port),Health).serve_forever()

async def run():
    if not TOKEN: raise RuntimeError("BOT_TOKEN is missing")
    init_db()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^ad:(approve|reject):"))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    threading.Thread(target=health_server,daemon=True).start()
    print(f"{SHOP_NAME} đang chạy...")
    await app.initialize(); await app.start(); await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__=="__main__":
    asyncio.run(run())
