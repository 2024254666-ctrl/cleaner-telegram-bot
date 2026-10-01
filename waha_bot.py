import os
import re
import sqlite3
import base64
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from fpdf import FPDF

# WAHA Configuration
WAHA_URL = "http://localhost:3000"
WAHA_SESSION = "default"
WAHA_API_KEY = "123456"

HEADERS = {
    "Content-Type": "application/json",
    "X-Api-Key": WAHA_API_KEY
}

# Put your phone number here without '+' or spaces (e.g., '60123456789')
# Leave empty [] to allow any user to trigger /reportmonthly
ADMIN_NUMBERS = []

BASE_DIR = r"C:\pyhton project\cleanerBOT"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "images")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
DB_PATH = os.path.join(BASE_DIR, "reports.db")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)

app = FastAPI()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            image_path TEXT,
            description TEXT,
            date_logged TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class TaskReportPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", style="I", size=8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

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
                print(f"Skipping image {img_path}: {e}")

            col += 1
            if col >= cols:
                col = 0
                curr_y += img_h + spacing

    pdf.output(output_pdf_path)
    return output_pdf_path

def send_whatsapp_text(chat_id, text):
    url = f"{WAHA_URL}/api/sendText"
    payload = {"session": WAHA_SESSION, "chatId": chat_id, "text": text}
    try:
        res = requests.post(url, json=payload, headers=HEADERS)
        if res.status_code != 200:
            print(f"Failed to send text: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error sending text: {e}")

def send_whatsapp_file(chat_id, file_path, filename, caption=""):
    url = f"{WAHA_URL}/api/sendFile"
    with open(file_path, "rb") as f:
        file_base64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "session": WAHA_SESSION,
        "chatId": chat_id,
        "file": {
            "mimetype": "application/pdf",
            "filename": filename,
            "data": f"data:application/pdf;base64,{file_base64}"
        },
        "caption": caption
    }
    try:
        res = requests.post(url, json=payload, headers=HEADERS)
        if res.status_code != 200:
            print(f"Failed to send file: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error sending file: {e}")

@app.post("/webhook")
async def waha_webhook(request: Request):
    data = await request.json()
    if data.get("event") != "message":
        return {"status": "ignored"}

    payload = data.get("payload", {})
    from_id = payload.get("from", "")
    phone_clean = from_id.split("@")[0]
    has_media = payload.get("hasMedia", False)
    body = payload.get("body", "").strip()

    # Admin report trigger
    if body.startswith("/reportmonthly"):
        if ADMIN_NUMBERS and phone_clean not in ADMIN_NUMBERS:
            send_whatsapp_text(from_id, "Access denied: Admin only.")
            return {"status": "unauthorized"}

        args = body.split(" ")
        if len(args) > 1:
            raw_arg = args[1].strip()
            if re.match(r"^\d{2}-\d{4}$", raw_arg):
                parts = raw_arg.split("-")
                target_ym = f"{parts[1]}-{parts[0]}"
            elif re.match(r"^\d{4}-\d{2}$", raw_arg):
                target_ym = raw_arg
            else:
                send_whatsapp_text(from_id, "Invalid format. Use `/reportmonthly MM-YYYY`")
                return {"status": "bad_format"}
        else:
            target_ym = datetime.now().strftime("%Y-%m")

        send_whatsapp_text(from_id, f"Compiling report for {target_ym}...")
        out_pdf = os.path.join(EXPORTS_DIR, f"Monthly_Report_{target_ym}.pdf")
        gen_path = generate_monthly_pdf(out_pdf, target_ym)

        if not gen_path or not os.path.exists(gen_path):
            send_whatsapp_text(from_id, f"No records found for {target_ym}.")
            return {"status": "no_data"}

        send_whatsapp_file(from_id, gen_path, f"Report_{target_ym}.pdf", f"Activity Report: {target_ym}")
        return {"status": "report_sent"}

    # Handle incoming photos
    if has_media:
        media_url = payload.get("media", {}).get("url")
        if not media_url:
            return {"status": "no_media_url"}

        # Include API key to download media from WAHA
        response = requests.get(media_url, headers={"X-Api-Key": WAHA_API_KEY})
        if response.status_code == 200:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            file_name = f"wa_{timestamp_str}.jpg"
            file_path = os.path.join(DOWNLOAD_DIR, file_name)

            with open(file_path, "wb") as f:
                f.write(response.content)

            raw_caption = body
            date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(.*)", raw_caption)
            if date_match:
                test_date = date_match.group(1)
                caption = date_match.group(2) or "No description"
                logged_time = f"{test_date} 12:00:00"
            else:
                caption = raw_caption or "No description"
                logged_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO task_reports (user_id, image_path, description, date_logged)
                VALUES (?, ?, ?, ?)
            ''', (phone_clean, file_path, caption, logged_time))
            conn.commit()
            conn.close()

            send_whatsapp_text(from_id, f"Task logged successfully for date: {logged_time.split(' ')[0]}")
            return {"status": "success"}
        else:
            print(f"Failed to download media: {response.status_code}")
            return {"status": "download_failed"}

    return {"status": "ok"}