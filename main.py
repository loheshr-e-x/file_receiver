import os
import re
import shutil
import sqlite3
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Load environment variables from a local .env file (for development).
load_dotenv()

# --- Google Drive Imports ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DB_FILE = os.path.join(BASE_DIR, "transactions.db")
PRINT_JOBS_DIR = os.path.join(BASE_DIR, "print_jobs")
# Path to Google service account credentials JSON. Prefer providing this
# as an environment variable so secrets are not checked into git.
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", os.path.join(BASE_DIR, "credentials.json"))
GOOGLE_DRIVE_FOLDER_ID = "1IXIe_jpM2sLNCwy1cZSpVtsC2q39p1WH"

app = FastAPI()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_drive_service():
    """Authenticates with Google Drive using the Service Account."""
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    try:
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error connecting to Google Drive: {e}")
        return None
    
def init_db() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS used_utrs (
                utr TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


init_db()


def is_utr_used(utr: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM used_utrs WHERE utr = ?", (utr,))
        return cursor.fetchone() is not None


def mark_utr_used(utr: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO used_utrs (utr) VALUES (?)", (utr,))
        conn.commit()


@app.get("/", response_class=HTMLResponse)
async def payment_page(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.post("/verify-utr", response_class=HTMLResponse)
async def verify_utr(request: Request, utr_number: str = Form(...), roll_number: str = Form(...)):
    utr_id = utr_number.strip()
    roll_id = roll_number.strip()

    if not re.match(r"^240701\d{3}$", roll_id):
        return """
        <h3>Invalid Roll Number</h3>
        <p>Roll number must start with 240701 followed by exactly 3 digits.</p>
        <a href='/'>Go Back</a>
        """

    if not re.match(r"^\d{12}$", utr_id):
        return """
        <h3>Invalid UTR Format</h3>
        <p>Please enter a valid 12-digit Transaction ID/UTR.</p>
        <a href='/'>Go Back</a>
        """

    if is_utr_used(utr_id):
        return """
        <h3>Access Denied</h3>
        <p>This Transaction ID has already been used.</p>
        <a href='/'>Go Back</a>
        """

    return templates.TemplateResponse(request, "upload.html", {
        "request": request,
        "validated_utr": utr_id,
        "user_roll": roll_id,
    })


@app.post("/submit-document")
async def submit_document(validated_utr: str = Form(...), user_roll: str = Form(...), file: UploadFile = File(...)):
    # 1. Validation Check
    if not re.match(r"^240701\d{3}$", user_roll) or not re.match(r"^\d{12}$", validated_utr):
        raise HTTPException(status_code=400, detail="Data tampering detected.")

    # 2. Prevent Double-Clicks / Race Conditions
    if is_utr_used(validated_utr):
        raise HTTPException(status_code=400, detail="Transaction ID already consumed.")

    # Lock the UTR immediately before saving the file, not after!
    mark_utr_used(validated_utr)

    os.makedirs(PRINT_JOBS_DIR, exist_ok=True)

    safe_filename = f"{user_roll}_{os.path.basename(file.filename)}"
    file_location = os.path.join(PRINT_JOBS_DIR, safe_filename)

    with open(file_location, "wb+") as file_object:
        for chunk in file.file:
            file_object.write(chunk)

    return {"status": "Success", "message": "File saved locally for printing."}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
