from fastapi import FastAPI, HTTPException
import requests
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import random
from datetime import datetime
import json
import os

app = FastAPI()

# تفعيل الـ CORS بشكل كامل لجميع النطاقات والواجهات المعزولة
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = "f9afe7e1bc006f79f75bafe764b0f117"
DB_FILE = "network_database.json"

# دالة ذكية لشحن الحسابات من ملف ثابت لضمان عدم ضياع البيانات عند إعادة التشغيل
def load_db():
    if not os.path.exists(DB_FILE):
        default_db = [
            {"username": "fethi", "role": "owner", "balance": 999999.00, "rtp": 50, "is_blocked": 0, "created_by": "System"},
            {"username": "samir", "role": "super_admin", "balance": 5000.00, "rtp": 50, "is_blocked": 0, "created_by": "fethi"}
        ]
        with open(DB_FILE, "w") as f:
            json.dump(default_db, f)
        return default_db
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- النماذج وهياكل البيانات المدخلة ---
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str
    created_by: str

class ConfigureAccountRequest(BaseModel):
    admin_username: str
    target_username: str
    rtp: int
    is_blocked: int

class UpdateBalanceRequest(BaseModel):
    admin_username: str
    target_username: str
    action: str  
    amount: float

# --- 🔐 مسارات التحقق وإنشاء الحسابات الثابتة ---

@app.post("/api/login")
async def login_user(req: LoginRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    if uname == "fethi" and req.password == "123456":
        return {"username": "fethi", "role": "owner", "balance": 999999.00}
        
    for u in db:
        if u["username"] == uname and req.password == "123456":
            if u["is_blocked"] == 1:
                raise HTTPException(status_code=403, detail="Ce compte est bloqué")
            return {"username": u["username"], "role": u["role"], "balance": u["balance"]}
            
    raise HTTPException(status_code=401, detail="Identifiants incorrects")

@app.post("/api/register")
async def register_user(req: RegisterRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    for u in db:
        if u["username"] == uname:
            raise HTTPException(status_code=400, detail="Nom d'utilisateur déjà pris")
            
    new_user = {
        "username": uname,
        "role": req.role,
        "balance": 0.00,
        "rtp": 50,
        "is_blocked": 0,
        "created_by": req.created_by
    }
    db.append(new_user)
    save_db(db)
    return {"status": "success", "message": "Compte créé"}

@app.get("/api/admin/users")
async def get_all_network_users(admin_username: Optional[str] = None):
    return load_db()

@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest):
    target = req.target_username.lower().strip()
    amount = float(req.amount)
    db = load_db()
    
    for u in db:
        if u["username"] == target:
            if req.action == "charge":
                u["balance"] += amount
            elif req.action == "withdraw":
                if u["balance"] < amount:
                    raise HTTPException(status_code=400, detail="Solde insuffisant")
                u["balance"] -= amount
            save_db(db)
            return {"status": "success", "balance": u["balance"]}
            
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.post("/api/admin/configure-account")
async def configure_account(req: ConfigureAccountRequest):
    db = load_db()
    for u in db:
        if u["username"] == req.target_username.lower().strip():
            u["rtp"] = req.rtp
            u["is_blocked"] = req.is_blocked
            save_db(db)
            return {"status": "success", "message": "Configuration enregistrée"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.delete("/api/admin/delete-account")
async def delete_account(admin_username: str, target_username: str):
    db = load_db()
    target = target_username.lower().strip()
    for i, u in enumerate(db):
        if u["username"] == target:
            db.pop(i)
            save_db(db)
            return {"status": "success", "message": "Supprimé"}
    raise HTTPException(status_code=404, detail="Non trouvé")

@app.get("/")
async def root():
    return {"status": "Alpha Database Server Running Perfectly"}