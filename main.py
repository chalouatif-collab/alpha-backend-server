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

# دالة الشحن الذكية لقراءة البيانات من الملف لضمان الحفظ الدائم
def load_db():
    if not os.path.exists(DB_FILE):
        default_db = [
            {"username": "fethi", "password": "123456", "role": "owner", "balance": 999999.00, "rtp": 50, "is_blocked": 0, "created_by": "System"},
            {"username": "samir", "password": "123456", "role": "super_admin", "balance": 5000.00, "rtp": 50, "is_blocked": 0, "created_by": "fethi"}
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

# --- النماذج وهياكل البيانات المدخلة (Pydantic Models) ---
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

class ChangePlayerPasswordRequest(BaseModel):
    admin_username: str
    target_username: str
    new_password: str

# --- 🔐 مسارات التحقق والحماية ---

@app.post("/api/login")
async def login_user(req: LoginRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    if uname == "fethi" and req.password == "123456":
        return {"username": "fethi", "role": "owner", "balance": 999999.00}
        
    for u in db:
        if u["username"] == uname and u.get("password", "123456") == req.password:
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
        "password": req.password,
        "role": req.role,
        "balance": 0.00,
        "rtp": 50,
        "is_blocked": 0,
        "created_by": req.created_by
    }
    db.append(new_user)
    save_db(db)
    return {"status": "success", "message": "Compte créé"}

# --- 📊 مسارات الإدارة العامة والتحكم المالي المطور ---

@app.get("/api/admin/users")
async def get_all_network_users(admin_username: Optional[str] = None):
    return load_db()

@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest):
    target = req.target_username.lower().strip()
    admin = req.admin_username.lower().strip()
    amount = float(req.amount)
    db = load_db()
    
    target_user = None
    admin_user = None
    
    # قراءة الحسابات من قاعدة البيانات
    for u in db:
        if u["username"] == target:
            target_user = u
        if u["username"] == admin:
            admin_user = u

    if not target_user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    # منظومة الشحن المحمية والمقيدة بالرصيد الفوقي
    if req.action == "charge":
        # إذا لم يكن النظام ولم يكن الأونر fethi، نخصم من رصيد المسؤول القائم بالشحن
        if admin != "system" and admin != "fethi":
            if not admin_user:
                raise HTTPException(status_code=404, detail="Admin القائم بالعملية غير موجود")
            if admin_user["balance"] < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant chez l'admin")
            
            # خصم الرصيد من الأدمن أو السوبر أدمن الموزع
            admin_user["balance"] -= amount
        
        # إضافة الرصيد للحساب المستهدف
        target_user["balance"] += amount

    # منظومة السحب المحمية
    elif req.action == "withdraw":
        if target_user["balance"] < amount:
            raise HTTPException(status_code=400, detail="Solde insuffisant chez le compte cible")
        
        # سحب الرصيد من الحساب المستهدف
        target_user["balance"] -= amount
        
        # إرجاع الرصيد المسحوب لعداد المسؤول المباشر
        if admin != "system" and admin != "fethi" and admin_user:
            admin_user["balance"] += amount

    save_db(db)
    return {"status": "success", "balance": target_user["balance"]}

# مسار تغيير كلمة المرور المباشر من اللوحات
@app.post("/api/admin/change-player-password")
async def change_player_password(req: ChangePlayerPasswordRequest):
    target = req.target_username.lower().strip()
    db = load_db()
    
    for u in db:
        if u["username"] == target:
            u["password"] = req.new_password
            save_db(db)
            return {"status": "success", "message": "Mot de passe modifié avec succès"}
            
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

# --- ⚽ مسارات الرهان الرياضي ---
@app.get("/api/sports/live")
async def get_sports():
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league"]
    all_matches = []
    for league in leagues:
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={API_KEY}&regions=eu&markets=h2h"
        try:
            response = requests.get(url)
            if response.status_code == 200:
                all_matches.extend(response.json())
        except Exception:
            pass
    return all_matches

@app.get("/")
async def root():
    return {"status": "Alpha Secure Database Backend Running Perfectly"}
