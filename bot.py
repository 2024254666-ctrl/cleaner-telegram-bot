import os
import re
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from fpdf import FPDF

# Set up absolute paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "images")
DB_PATH = os.path.join(BASE_DIR, "reports.db")
TOKEN_PATH = os.path.join(BASE_DIR, "token.txt")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- LOAD SECURE TOKEN ---
if not os.path.exists(TOKEN_PATH):
    raise FileNotFoundError(f"Could not find 'token.txt' at {TOKEN_PATH}. Please create it and paste your bot token inside.")

with open(TOKEN_PATH, "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

if not TOKEN:
    raise ValueError(f"'token.txt' at {TOKEN_PATH} is empty. Please add your Telegram Bot Token.")

# --- SECURITY CONFIGURATION ---
# Put your numeric Telegram user ID(s) here
ADMIN_IDS = [
    1807919625,
    # Example: 123456789,
]

# Put worker numeric Telegram IDs here.
# If you leave this list empty ([]), anyone can submit photos, but only ADMIN_IDS can run /reportmonthly.
ALLOWED_WORKER_IDS = [
    # Example: 987654321,
]


class TaskReportPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", style="I", size=8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            image_path TEXT,
            description TEXT,
            date_logged TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def generate_monthly_pdf(output_pdf_path, year_month_filter):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT image_path, description, date_logged 
        FROM task_reports 
        WHERE date_logged LIKE ? 
        ORDER BY date_logged ASC
    ''', (f"{year_month_filter}%",))
    records = cursor.fetchall()
    conn.close()

    if not records:
        return None

    grouped_by_date = {}
    for img_path, desc, dt in records:
        day_str = dt.split(" ")[0]
        grouped_by_date.setdefault(day_str, []).append((img_path, desc, dt))

    pdf = TaskReportPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=False)

    margin_x = 7
    start_y = 20
    spacing = 0.5
    cols = 4
    img_w = 48.75
    img_h = 48.75
    max_y_limit = 275

    month_dt = datetime.strptime(year_month_filter, "%Y-%m")
    month_name_upper = month_dt.strftime("%B %Y").upper()

    for day_str, items in grouped_by_date.items():
        pdf.add_page()

        formatted_date = ".".join(day_str.split("-")[::-1])
        pdf.set_xy(margin_x, 8)
        pdf.set_font("Helvetica", style="B", size=11)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 8, f"MONTHLY ACTIVITY REPORT ({month_name_upper}) - {formatted_date}", ln=True)

        col = 0
        curr_y = start_y

        for img_path, desc, dt in items:
            if not os.path.exists(img_path):
                continue

            if curr_y + img_h > max_y_limit:
                pdf.add_page()
                pdf.set_xy(margin_x, 8)
                pdf.set_font("Helvetica", style="I", size=10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 8, f"{formatted_date} (Continued)", ln=True)
                curr_y = start_y
                col = 0

            curr_x = margin_x + col * (img_w + spacing)

            try:
                pdf.image(img_path, x=curr_x, y=curr_y, w=img_w, h=img_h)
            except Exception as e:
                print(f"Skipping damaged image {img_path}: {e}")

            col += 1
            if col >= cols:
                col = 0
                curr_y += img_h + spacing

    pdf.output(output_pdf_path)
    return output_pdf_path


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Task Reporting Bot!\n\n"
        "• Send a photo to submit a task log.\n"
        "• Use /myid to get your Telegram user ID.\n"
        "• Admins can use /reportmonthly to compile the PDF report."
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"Your Telegram User ID is: `{user_id}`", parse_mode="Markdown")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Check if worker whitelisting is active
    if ALLOWED_WORKER_IDS and user_id not in ALLOWED_WORKER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized: You are not authorized to submit task photos.")
        return

    photo = update.message.photo[-1]
    raw_caption = update.message.caption or ""

    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(.*)", raw_caption)
    if date_match:
        test_date = date_match.group(1)
        caption = date_match.group(2) or "No description provided"
        timestamp = f"{test_date} 12:00:00"
    else:
        caption = raw_caption or "No description provided"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    file = await context.bot.get_file(photo.file_id)
    file_name = f"{photo.file_unique_id}.jpg"
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    await file.download_to_drive(file_path)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO task_reports (user_id, image_path, description, date_logged)
        VALUES (?, ?, ?, ?)
    ''', (user_id, file_path, caption, timestamp))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Task saved for date: {timestamp.split(' ')[0]}")


async def report_monthly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Check admin privileges
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("Access denied: Only designated admins can generate reports.")
        return

    if context.args:
        user_arg = context.args[0].strip()
        if re.match(r"^\d{2}-\d{4}$", user_arg):
            parts = user_arg.split("-")
            target_ym = f"{parts[1]}-{parts[0]}"
        elif re.match(r"^\d{4}-\d{2}$", user_arg):
            target_ym = user_arg
        else:
            await update.message.reply_text("Invalid format. Please use: `/reportmonthly MM-YYYY` (e.g., `/reportmonthly 08-2026`)")
            return
    else:
        target_ym = datetime.now().strftime("%Y-%m")

    status_msg = await update.message.reply_text(f"Compiling monthly report for `{target_ym}`...")

    output_pdf = os.path.join(BASE_DIR, f"Monthly_Report_{target_ym}.pdf")
    generated_path = generate_monthly_pdf(output_pdf, target_ym)

    if not generated_path or not os.path.exists(generated_path):
        await status_msg.edit_text(f"No records found in database for the month `{target_ym}`.")
        return

    with open(generated_path, 'rb') as doc:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=doc,
            filename=f"Report_{target_ym}.pdf",
            caption=f"Here is your Task Activity Report for {target_ym}."
        )

    await status_msg.delete()


if __name__ == '__main__':
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("reportmonthly", report_monthly_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print(f"Bot running... Database at: {DB_PATH}")
    app.run_polling()