from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
import requests
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt
from datetime import datetime, timedelta
import random
import json
import os

# --- إعدادات الأمان ونظام التوكن ---
SECRET_KEY = "gdldf52145*ytfrf-frtredà@&6é0'+" # هذا هو مفتاحك السري (غيره!)
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except:
        raise HTTPException(status_code=401, detail="Invalid token")
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
TICKETS_FILE = "tickets_database.json"  # قاعدة بيانات الأوراق السحابية الجديدة

# --- دالات الحفظ والقراءة الذكية لبيانات الشبكة والأوراق ---
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

def load_tickets_db():
    if not os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, "w") as f:
            json.dump([], f)
        return []
    try:
        with open(TICKETS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_tickets_db(data):
    with open(TICKETS_FILE, "w") as f:
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

# هياكل الرهان والربط السحابي الجديدة للسوبر أونر
class SaveTicketRequest(BaseModel):
    username: str
    ticket_data: dict

class UpdateTicketStatusRequest(BaseModel):
    ticket_id: int
    status: str
    amount_paid: float


# --- 🔐 مسارات التحقق والحماية ---

@app.post("/api/login")
async def login_user(req: LoginRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    # 1. التحقق من بيانات المستخدم
    user = None
    if uname == "fethi" and req.password == "123456":
        user = {"username": "fethi", "role": "owner", "balance": 999999.00}
    else:
        for u in db:
            if u["username"] == uname and u.get("password") == req.password:
                if u["is_blocked"] == 1: raise HTTPException(status_code=403, detail="Ce compte est bloqué")
                user = u
                break
    
    # 2. إذا لم نجد المستخدم
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    
    # 3. إصدار الـ Token المشفر
    access_token = create_access_token(data={"sub": user["username"]})
    
    # 4. إرجاع النتيجة مع التوكن
    return {
        "access_token": access_token, 
        "token_type": "bearer", 
        "username": user["username"], 
        "role": user.get("role", "user"), 
        "balance": user.get("balance", 0.0)
    }

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
async def update_balance(req: UpdateBalanceRequest, current_user: str = Depends(get_current_user)):
    target = req.target_username.lower().strip()
    admin = req.admin_username.lower().strip()
    amount = float(req.amount)
    db = load_db()

    target_user = None
    admin_user = None

    for u in db:
        if u.get("username") == target:
            target_user = u
        if u.get("username") == admin:
            admin_user = u

    if not target_user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if req.action == "charge":
        if admin != "system" and admin != "fethi":
            if not admin_user:
                raise HTTPException(status_code=404, detail="القائم بالعملية غير موجود")
            if admin_user.get("balance", 0) < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant chez l'admin")
            admin_user["balance"] -= amount

        # --- 🚀 محرك الـ Cashback الذكي (10%) ---
        current_balance = target_user.get("balance", 0)
        daily_deps = target_user.get("daily_deposits", 0)

        # إذا كان رصيد اللاعب منتهي (أقل من 1) وعنده إيداعات سابقة، نفعل الكاش باك!
        if current_balance < 1.0 and daily_deps > 0:
            cashback_bonus = daily_deps * 0.10
            target_user["balance"] = current_balance + cashback_bonus
            target_user["daily_deposits"] = 0 # تصفير العداد بعد أخذ الهدية
        
        # إضافة الشحن الجديد
        target_user["balance"] = target_user.get("balance", 0) + amount
        
        # تسجيل الشحن في عداد الإيداعات (فقط إذا كان الشحن من مسؤول وليس من سيستم الأرباح)
        if admin != "system":
            target_user["daily_deposits"] = target_user.get("daily_deposits", 0) + amount
        # ----------------------------------------

    elif req.action == "withdraw":
        # 🚀 السماح للسيستم (واللاعبين) بخصم ثمن الورقة بدون أخطاء
        if target_user.get("balance", 0) < amount:
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        
        target_user["balance"] -= amount
        
        if admin != "system" and admin != "fethi" and admin_user:
            admin_user["balance"] = admin_user.get("balance", 0) + amount

    save_db(db)
    return {"status": "success", "balance": target_user["balance"]}


# --- 🚀 مسارات إدارة ومزامنة أوراق الرهان الرياضي للسوبر أونر ---

@app.post("/api/admin/save-ticket")
async def save_player_ticket(req: SaveTicketRequest):
    tickets_db = load_tickets_db()
    
    # ربط التذكرة باسم المستخدم وحفظها سحابياً
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
    save_tickets_db(tickets_db)
    return {"status": "success", "message": "Ticket synced with server database successfully"}

@app.post("/api/admin/update-ticket-status")
async def update_ticket_status(req: UpdateTicketStatusRequest):
    tickets_db = load_tickets_db()
    for t in tickets_db:
        if t["ticket_id"] == req.ticket_id:
            t["status"] = req.status
            t["final_cashout_paid"] = req.amount_paid
            save_tickets_db(tickets_db)
            return {"status": "success", "message": "Ticket status updated on server"}
    raise HTTPException(status_code=404, detail="Ticket non trouvé")

# المسار الخاص بجلب أوراق لاعب معين لعرضها فوراً في شاشة السوبر أونر المنبثقة
@app.get("/api/admin/get-player-tickets")
async def get_player_tickets(username: str):
    tickets_db = load_tickets_db()
    uname = username.lower().strip()
    player_tickets = [t for t in tickets_db if t["username"] == uname]
    return player_tickets
# --- مسار جلب سجل التحويلات المالي والرياضي ---
@app.get("/api/admin/get-history")
async def get_history(username: str):
    # جلب التذاكر الرياضية الخاصة باللاعب
    tickets_db = load_tickets_db()
    uname = username.lower().strip()
    
    # فلترة التذاكر الخاصة بهذا المستخدم فقط
    user_history = [t for t in tickets_db if t["username"] == uname]
    
    # إرجاع النتائج للواجهة
    return {"history": user_history}


# --- 🔐 بقية مسارات الإدارة العامة وسحب البيانات الأصلية ---

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
