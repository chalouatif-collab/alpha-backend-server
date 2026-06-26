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

# --- دالة تحميل آمنة ---
def load_json_file(filename, default_value):
    if not os.path.exists(filename):
        with open(filename, "w") as f:
            json.dump(default_value, f)
        return default_value
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except:
        return default_value

# --- مسارات الأوراق (معدلة للحفظ الفوري) ---
@app.post("/api/admin/save-ticket")
async def save_player_ticket(req: SaveTicketRequest):
    tickets_db = load_json_file(TICKETS_FILE, [])
    
    # تحويل البيانات وإضافتها
    new_ticket = req.ticket_data
    new_ticket["username"] = req.username.lower().strip()
    
    tickets_db.append(new_ticket)
    
    with open(TICKETS_FILE, "w") as f:
        json.dump(tickets_db, f, indent=4)
        f.flush()
        os.fsync(f.fileno())
        
    return {"status": "success", "count": len(tickets_db)}

@app.get("/api/admin/get-player-tickets")
async def get_player_tickets(username: str):
    tickets_db = load_json_file(TICKETS_FILE, [])
    uname = username.lower().strip()
    # فلترة الأوراق بناءً على اسم اللاعب
    player_tickets = [t for t in tickets_db if t.get("username") == uname]
    return player_tickets

# ... (احتفظ بباقي دوال السيرفر كما هي في ملفك القديم)
