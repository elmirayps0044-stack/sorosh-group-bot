import os
import json
import time
import queue
import asyncio
import threading
from flask import Flask, request, redirect, session as flask_session

from spluslib import SplusClient

# ----------------------------------------------------------------------
# تنظیمات پایه (همه از طریق Environment Variable هم قابل تغییرن)
# ----------------------------------------------------------------------
OWNER_ID = int(os.environ.get("OWNER_ID", "37161821"))
PHONE_NUMBER = os.environ.get("PHONE_NUMBER", "")          # می‌تونی خالی بذاری و از صفحه‌ی لاگین وارد کنی
ACTIVATION_CODE = "YaSeR"
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", str(OWNER_ID))
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key")
SESSION_NAME = "userbot_session"
DATA_FILE = "bot_data.json"
PORT = int(os.environ.get("PORT", "8080"))

# ----------------------------------------------------------------------
# ذخیره‌سازی داده (فایل JSON ساده)
# ----------------------------------------------------------------------
_lock = threading.Lock()


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"groups": {}, "admins": []}


def save_data(data):
    with _lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get_group(data, chat_id):
    key = str(chat_id)
    if key not in data["groups"]:
        data["groups"][key] = {
            "active": False,
            "title": "",
            "warns": {},
            "soft_banned": [],
            "muted": [],
            "rules": "قوانینی تنظیم نشده است.",
            "welcome": "خوش آمدید!",
            "lock_links": False,
        }
    return data["groups"][key]


def is_admin_user(data, user_id):
    return user_id == OWNER_ID or user_id in data.get("admins", [])


# ----------------------------------------------------------------------
# اجرای یک event loop مجزا در یک ترد جدا، تا هم Flask و هم کلاینت سروش
# هم‌زمان روی یک پروسه اجرا بشن.
# ----------------------------------------------------------------------
loop = asyncio.new_event_loop()
client = SplusClient(SESSION_NAME)

login_status = {"logged_in": False, "waiting_for_code": False, "error": None}
_code_queue = queue.Queue()


def code_callback():
    """
    وقتی SplusLib برای لاگین به کد پیامکی نیاز داشته باشه این تابع صدا زده می‌شه.
    اینجا منتظر می‌مونیم تا کاربر از طریق صفحه‌ی وب کد رو وارد کنه.
    """
    login_status["waiting_for_code"] = True
    code = _code_queue.get()  # تا وقتی کد از صفحه‌ی وب نیاد، بلاک می‌مونه
    login_status["waiting_for_code"] = False
    return code


def session_file_exists():
    for fname in os.listdir("."):
        if fname.startswith(SESSION_NAME):
            return True
    return False


async def start_client(phone: str):
    try:
        await client.start(phone=phone, code_callback=code_callback)
        login_status["logged_in"] = True
        login_status["error"] = None
        register_handlers()
        await client.run_until_disconnected()
    except Exception as e:
        login_status["error"] = str(e)


def run_loop():
    asyncio.set_event_loop(loop)
    if session_file_exists() or PHONE_NUMBER:
        loop.run_until_complete(start_client(PHONE_NUMBER))
    else:
        loop.run_forever()  # منتظر می‌مونیم تا از صفحه‌ی وب شماره وارد بشه


threading.Thread(target=run_loop, daemon=True).start()


def submit_phone(phone: str):
    return asyncio.run_coroutine_threadsafe(start_client(phone), loop)


def submit_code(code: str):
    _code_queue.put(code)


# ----------------------------------------------------------------------
# اقدامات مدیریتی واقعی روی گروه (بن/کیک/میوت). چون این کتابخونه خیلی
# تازه‌ست و مستندات کامل رسمی نداره، چند اسم محتمل برای متدها امتحان
# می‌کنیم؛ اگه هیچ‌کدوم جواب نداد میفتیم رو حالت soft (حذف خودکار
# پیام‌های بعدی همون کاربر).
# ----------------------------------------------------------------------
async def try_methods(method_names, *args, **kwargs):
    last_err = None
    for name in method_names:
        fn = getattr(client, name, None)
        if fn is None:
            continue
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise AttributeError("هیچ‌کدام از متدهای مدیریتی روی کلاینت پیدا نشد")


async def do_ban(chat_id, user_id):
    return await try_methods(["ban_user", "ban_chat_member", "kick_user"], chat_id, user_id)


async def do_kick(chat_id, user_id):
    return await try_methods(["kick_user", "kick_chat_member", "remove_user"], chat_id, user_id)


async def do_mute(chat_id, user_id):
    return await try_methods(["mute_user", "restrict_user"], chat_id, user_id)


async def do_unmute(chat_id, user_id):
    return await try_methods(["unmute_user", "unrestrict_user"], chat_id, user_id)


# ----------------------------------------------------------------------
# منطق دستورات
# ----------------------------------------------------------------------
def register_handlers():

    @client.on_message()
    async def handler(event):
        message = event.message if hasattr(event, "message") else event
        text = (getattr(message, "text", "") or "").strip()
        chat_id = message.chat_id
        sender_id = message.sender_id
        is_group = getattr(message, "is_group", chat_id != sender_id)

        data = load_data()

        if text == f"/start {ACTIVATION_CODE}" and sender_id == OWNER_ID and is_group:
            g = get_group(data, chat_id)
            g["active"] = True
            save_data(data)
            await message.reply("✅ ربات در این گروه فعال شد.")
            return

        if not is_group:
            return

        g = get_group(data, chat_id)
        if not g["active"]:
            return

        if g["lock_links"] and not is_admin_user(data, sender_id):
            if "http://" in text or "https://" in text or "t.me/" in text or "splus.ir/" in text:
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        if not text.startswith("/"):
            return

        cmd, *rest = text.split(maxsplit=1)
        arg = rest[0] if rest else ""
        reply_to = getattr(message, "reply_to_message", None)
        target_id = reply_to.sender_id if reply_to else None

        if cmd == "/help":
            await message.reply(
                "دستورات:\n"
                "/rules - نمایش قوانین\n"
                "/setrules <متن> - تنظیم قوانین (ادمین)\n"
                "/setwelcome <متن> - تنظیم پیام خوش‌آمد (ادمین)\n"
                "/warn (ریپلای) - اخطار\n"
                "/unwarn (ریپلای) - حذف اخطار\n"
                "/ban /unban /kick (ریپلای)\n"
                "/mute /unmute (ریپلای)\n"
                "/lock links | /unlock links\n"
                "/addadmin <id> | /deladmin <id> (فقط مالک)\n"
                "/admins - لیست ادمین‌ها\n"
                "/stats - آمار گروه"
            )
            return

        if cmd == "/rules":
            await message.reply(g["rules"])
            return

        if cmd == "/stats":
            await message.reply(
                f"عنوان: {g.get('title','-')}\n"
                f"تعداد اخطارها: {len(g['warns'])}\n"
                f"کاربران بن‌شده: {len(g['soft_banned'])}\n"
                f"قفل لینک: {'فعال' if g['lock_links'] else 'غیرفعال'}"
            )
            return

        if cmd == "/admins":
            admins = [str(OWNER_ID)] + [str(a) for a in data.get("admins", [])]
            await message.reply("ادمین‌های بات:\n" + "\n".join(admins))
            return

        if not is_admin_user(data, sender_id):
            return

        if cmd == "/setrules":
            g["rules"] = arg or g["rules"]
            save_data(data)
            await message.reply("✅ قوانین به‌روزرسانی شد.")
            return

        if cmd == "/setwelcome":
            g["welcome"] = arg or g["welcome"]
            save_data(data)
            await message.reply("✅ پیام خوش‌آمد به‌روزرسانی شد.")
            return

        if cmd == "/lock" and arg.strip() == "links":
            g["lock_links"] = True
            save_data(data)
            await message.reply("🔒 لینک قفل شد.")
            return

        if cmd == "/unlock" and arg.strip() == "links":
            g["lock_links"] = False
            save_data(data)
            await message.reply("🔓 قفل لینک برداشته شد.")
            return

        if cmd in ("/warn", "/unwarn", "/ban", "/unban", "/kick", "/mute", "/unmute"):
            if not target_id:
                await message.reply("باید روی پیام فرد موردنظر ریپلای کنی.")
                return

            if cmd == "/warn":
                w = g["warns"].get(str(target_id), 0) + 1
                g["warns"][str(target_id)] = w
                save_data(data)
                if w >= 3:
                    try:
                        await do_kick(chat_id, target_id)
                    except Exception:
                        g["soft_banned"].append(target_id)
                        save_data(data)
                    await message.reply("⚠️ کاربر ۳ اخطار گرفت و اخراج شد.")
                else:
                    await message.reply(f"⚠️ اخطار {w}/۳ ثبت شد.")
                return

            if cmd == "/unwarn":
                if str(target_id) in g["warns"]:
                    g["warns"][str(target_id)] = max(0, g["warns"][str(target_id)] - 1)
                    save_data(data)
                await message.reply("✅ یک اخطار حذف شد.")
                return

            if cmd == "/ban":
                try:
                    await do_ban(chat_id, target_id)
                    await message.reply("🚫 کاربر بن شد.")
                except Exception:
                    if target_id not in g["soft_banned"]:
                        g["soft_banned"].append(target_id)
                        save_data(data)
                    await message.reply("🚫 کاربر بن شد (حالت soft، چون دسترسی مستقیم موفق نبود).")
                return

            if cmd == "/unban":
                if target_id in g["soft_banned"]:
                    g["soft_banned"].remove(target_id)
                    save_data(data)
                await message.reply("✅ کاربر آنبن شد.")
                return

            if cmd == "/kick":
                try:
                    await do_kick(chat_id, target_id)
                    await message.reply("👢 کاربر اخراج شد.")
                except Exception:
                    await message.reply("❌ اخراج ناموفق بود (دسترسی کافی نیست).")
                return

            if cmd == "/mute":
                try:
                    await do_mute(chat_id, target_id)
                    await message.reply("🔇 کاربر سایلنت شد.")
                except Exception:
                    if target_id not in g["muted"]:
                        g["muted"].append(target_id)
                        save_data(data)
                    await message.reply("🔇 کاربر سایلنت شد (حالت soft).")
                return

            if cmd == "/unmute":
                try:
                    await do_unmute(chat_id, target_id)
                except Exception:
                    pass
                if target_id in g["muted"]:
                    g["muted"].remove(target_id)
                    save_data(data)
                await message.reply("✅ سایلنت کاربر برداشته شد.")
                return

        if cmd == "/addadmin" and sender_id == OWNER_ID:
            try:
                new_id = int(arg.strip())
                if new_id not in data["admins"]:
                    data["admins"].append(new_id)
                    save_data(data)
                await message.reply("✅ ادمین اضافه شد.")
            except ValueError:
                await message.reply("آیدی عددی نامعتبره.")
            return

        if cmd == "/deladmin" and sender_id == OWNER_ID:
            try:
                del_id = int(arg.strip())
                if del_id in data["admins"]:
                    data["admins"].remove(del_id)
                    save_data(data)
                await message.reply("✅ ادمین حذف شد.")
            except ValueError:
                await message.reply("آیدی عددی نامعتبره.")
            return

    @client.on_message()
    async def filter_handler(event):
        message = event.message if hasattr(event, "message") else event
        chat_id = message.chat_id
        sender_id = message.sender_id
        data = load_data()
        g = get_group(data, chat_id)
        if sender_id in g.get("soft_banned", []) or sender_id in g.get("muted", []):
            try:
                await message.delete()
            except Exception:
                pass


# ----------------------------------------------------------------------
# پنل وب (هم برای لاگین اولیه، هم برای مدیریت تنظیمات)
# ----------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

LOGIN_PAGE = """
<h2>ورود اکانت یوزربات</h2>
<form method="post" action="/login/phone">
  شماره (با کد کشور، مثل +98912...):
  <input name="phone" style="width:250px">
  <button type="submit">ارسال کد</button>
</form>
"""

CODE_PAGE = """
<h2>کد پیامکی رو وارد کن</h2>
<form method="post" action="/login/code">
  <input name="code" style="width:150px">
  <button type="submit">تأیید</button>
</form>
"""

PANEL_LOGIN_PAGE = """
<h2>ورود به پنل مدیریت</h2>
<form method="post">
  رمز عبور: <input type="password" name="password">
  <button type="submit">ورود</button>
</form>
"""


@app.route("/")
def index():
    if login_status["logged_in"]:
        return redirect("/panel")
    if session_file_exists():
        return "در حال اتصال به اکانت... چند لحظه صبر کن و صفحه رو رفرش کن."
    if login_status["waiting_for_code"]:
        return CODE_PAGE
    return LOGIN_PAGE


@app.route("/login/phone", methods=["POST"])
def login_phone():
    phone = request.form.get("phone", "").strip()
    submit_phone(phone)
    time.sleep(2)
    return redirect("/")


@app.route("/login/code", methods=["POST"])
def login_code():
    code = request.form.get("code", "").strip()
    submit_code(code)
    time.sleep(2)
    return redirect("/")


@app.route("/panel", methods=["GET", "POST"])
def panel():
    if not flask_session.get("panel_auth"):
        if request.method == "POST":
            if request.form.get("password") == PANEL_PASSWORD:
                flask_session["panel_auth"] = True
                return redirect("/panel")
        return PANEL_LOGIN_PAGE

    data = load_data()
    rows = ""
    for gid, g in data["groups"].items():
        rows += f"<tr><td>{gid}</td><td>{g.get('title','-')}</td><td>{'فعال' if g['active'] else 'غیرفعال'}</td></tr>"

    return f"""
    <h2>پنل مدیریت ربات گروهی</h2>
    <p>وضعیت اتصال: {"متصل ✅" if login_status["logged_in"] else "متصل نیست ❌"}</p>
    <p>ادمین‌های سراسری: {data.get('admins', [])}</p>
    <table border="1" cellpadding="6">
      <tr><th>آیدی گروه</th><th>عنوان</th><th>وضعیت</th></tr>
      {rows}
    </table>
    """


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
