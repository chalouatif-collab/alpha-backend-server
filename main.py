from fastapi import FastAPI, HTTPException
import requests
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import json
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = "f9afe7e1bc006f79f75bafe764b0f117"
DB_FILE = "network_database.json"
TICKETS_FILE = "tickets_database.json"

# --- دالة قراءة آمنة ---
def load_json_file(filename, default_value):
    if not os.path.exists(filename):
        return default_value
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except:
        return default_value

# --- دالة حفظ آمنة ---
def save_json_file(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)
        f.flush()
        os.fsync(f.fileno())

# --- مسار حفظ التذكرة المحدث ---
@app.post("/api/admin/save-ticket")
async def save_player_ticket(req: SaveTicketRequest):
    tickets_db = load_json_file(TICKETS_FILE, [])
    
    # تحويل البيانات إلى الشكل الموحد
    new_ticket = {
        "username": req.username.lower().strip(),
        "ticket_id": req.ticket_data.get("id"),
        "status": req.ticket_data.get("status", "encours"),
        "games_count": req.ticket_data.get("gamesCount", 1),
        "details_list": req.ticket_data.get("detailsList", []),
        "total_cote": req.ticket_data.get("totalCote", 1.0),
        "mise": req.ticket_data.get("mise", 0.0),
        "gain": req.ticket_data.get("gain", 0.0),
        "date": req.ticket_data.get("date", datetime.now().strftime("%H:%M:%S"))
    }
    
    tickets_db.append(new_ticket)
    save_json_file(TICKETS_FILE, tickets_db)
    return {"status": "success", "message": "Ticket synced successfully"}

# ... (احتفظ بباقي المسارات كما هي في ملفك)
