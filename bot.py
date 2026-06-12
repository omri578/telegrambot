# -*- coding: utf-8 -*-
"""
בוט תיק מניות בטלגרם
=====================
מה הבוט עושה:
- כל יום בשעה 18:30 (שעון ישראל) שולח לך דוח רווח/הפסד על התיק שלך.
- מושך מחירים בזמן אמת + שער הדולר אוטומטית (ספריית yfinance, בלי API key).
- שתי השורות האחרונות מציגות את המגמה: איזו מניה הכי עולה ואיזו הכי יורדת
  (זו תצוגת מגמה, לא ייעוץ השקעות).

פקודות:
- /start  -> מציג לך את ה-chat id שלך (צריך אותו פעם אחת, ראה מדריך)
- /report -> שולח את הדוח עכשיו, בלי לחכות ל-18:30 (טוב לבדיקה)
- /help   -> עזרה

הגדרות (טוקן + לאן לשלוח) נקראות ממשתני סביבה:
- BOT_TOKEN : הטוקן מ-BotFather
- CHAT_ID   : מזהה הצ'אט שאליו נשלח הדוח היומי
"""

import os
import logging
import threading
from datetime import time as dtime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import yfinance as yf
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ===================== התיק שלך =====================
# ערוך כאן בקלות: שם, טיקר, כמות מניות, מחיר קנייה, מטבע.
# מטבעות: "USD" (דולר), "ILA" (אגורות, לבורסת ת"א), "ILS" (שקלים).
PORTFOLIO = [
    {"name": "אפל",   "ticker": "AAPL",    "shares": 0.25, "buy_price": 195.5,  "currency": "USD"},
    {"name": "אל על", "ticker": "ELAL.TA", "shares": 10.0, "buy_price": 1230.0, "currency": "ILA"},
    {"name": "דיסני", "ticker": "DIS",     "shares": 1.0,  "buy_price": 93.0,   "currency": "USD"},
]
# ====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


# ---------- שרת בריאות קטן (כדי שפלטפורמות ענן ישאירו אותנו רצים) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# ---------- משיכת מחירים ----------
def get_price(ticker: str) -> float:
    """מחזיר את המחיר הנוכחי של טיקר. זורק חריגה אם נכשל."""
    t = yf.Ticker(ticker)
    try:
        price = t.fast_info["last_price"]
        if price:
            return float(price)
    except Exception:
        pass
    # גיבוי: דרך היסטוריה
    hist = t.history(period="1d")
    return float(hist["Close"].iloc[-1])


def get_usdils() -> float:
    """שער דולר/שקל."""
    return get_price("ILS=X")


def to_ils(amount: float, currency: str, usdils: float) -> float:
    if currency == "USD":
        return amount * usdils
    if currency == "ILA":          # אגורות -> שקלים
        return amount / 100.0
    return amount                  # ILS


# ---------- בניית הדוח ----------
def build_report() -> str:
    try:
        usdils = get_usdils()
    except Exception as e:
        logger.error(f"כשל בשליפת שער הדולר: {e}")
        return "⚠️ לא הצלחתי למשוך את שער הדולר כרגע. ננסה שוב מחר."

    lines = ["📊 *דוח התיק היומי*", ""]
    total_cost = total_value = 0.0
    perf = []  # (שם, אחוז שינוי)
    errors = []

    for h in PORTFOLIO:
        try:
            cur = get_price(h["ticker"])
        except Exception as e:
            logger.error(f"כשל בשליפת {h['ticker']}: {e}")
            errors.append(h["name"])
            continue

        cost_ils  = to_ils(h["shares"] * h["buy_price"], h["currency"], usdils)
        value_ils = to_ils(h["shares"] * cur,            h["currency"], usdils)
        pl_ils = value_ils - cost_ils
        pct = (cur / h["buy_price"] - 1) * 100

        total_cost  += cost_ils
        total_value += value_ils
        perf.append((h["name"], pct))

        sign = "🟢" if pl_ils >= 0 else "🔴"
        lines.append(f"{sign} {h['name']}: {pl_ils:+.2f} ₪ ({pct:+.2f}%)")

    if total_cost == 0:
        return "⚠️ לא הצלחתי למשוך מחירים כרגע. ננסה שוב מחר."

    total_pl = total_value - total_cost
    total_pct = (total_value / total_cost - 1) * 100

    lines.append("")
    lines.append(f"💰 שווי נוכחי: {total_value:,.2f} ₪")
    total_sign = "🟢" if total_pl >= 0 else "🔴"
    lines.append(f"{total_sign} *רווח/הפסד כולל: {total_pl:+,.2f} ₪ ({total_pct:+.2f}%)*")
    lines.append(f"💵 שער דולר: {usdils:.3f} ₪")

    if errors:
        lines.append(f"⚠️ לא נמשכו: {', '.join(errors)}")

    # ---- שתי שורות המגמה (לא ייעוץ) ----
    if perf:
        best  = max(perf, key=lambda x: x[1])
        worst = min(perf, key=lambda x: x[1])
        lines.append("")
        lines.append(f"📈 הכי חזקה היום: {best[0]} ({best[1]:+.2f}%) | 📉 הכי חלשה: {worst[0]} ({worst[1]:+.2f}%)")
        lines.append("_מגמה בלבד, לא ייעוץ השקעות._")

    return "\n".join(lines)


# ---------- פקודות ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "שלום! 👋\n"
        f"מזהה הצ'אט שלך הוא: `{chat_id}`\n\n"
        "הכנס אותו למשתנה הסביבה CHAT_ID בהגדרות הפלטפורמה, "
        "כדי שאשלח לך את הדוח כל יום ב-18:30.\n\n"
        "אפשר גם לשלוח /report כדי לקבל דוח עכשיו.",
        parse_mode="Markdown",
    )


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("רגע, מושך מחירים... ⏳")
    text = build_report()
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "הפקודות שלי:\n"
        "/report - דוח התיק עכשיו\n"
        "/start - הצגת מזהה הצ'אט שלך\n"
        "/help - העזרה הזאת\n\n"
        "דוח אוטומטי נשלח כל יום ב-18:30 (שעון ישראל)."
    )


async def daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    text = build_report()
    await context.bot.send_message(
        chat_id=context.job.chat_id, text=text, parse_mode="Markdown"
    )


# ---------- הפעלה ----------
def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("חסר BOT_TOKEN במשתני הסביבה.")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("help", help_command))

    # תזמון הדוח היומי ל-18:30 שעון ישראל, אם הוגדר CHAT_ID
    chat_id = os.environ.get("CHAT_ID")
    if chat_id:
        app.job_queue.run_daily(
            daily_job,
            time=dtime(hour=18, minute=30, tzinfo=ISRAEL_TZ),
            chat_id=int(chat_id),
            name="daily_report",
        )
        logger.info(f"דוח יומי תוזמן ל-18:30 עבור chat_id={chat_id}")
    else:
        logger.warning("CHAT_ID לא הוגדר — הדוח האוטומטי כבוי. שלח /start כדי לקבל את המזהה.")

    logger.info("הבוט עלה ומאזין...")
    app.run_polling()


if __name__ == "__main__":
    main()
