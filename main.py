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
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
# ضع هذا الكود هنا في أعلى الملف
import time
cache = {"matches": [], "last_update": 0}
# جلب رابط قاعدة البيانات السري أو استخدام قاعدة محلية للتجربة
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local_test.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# تشغيل محرك قاعدة البيانات
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)
    balance = Column(Float, default=0.0)
    rtp = Column(Integer, default=50)
    is_blocked = Column(Integer, default=0)
    created_by = Column(String)

# --- (الجديد) جدول سجل المعاملات المالية ---
class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String)
    target_username = Column(String)
    action = Column(String)  # 'charge' أو 'withdraw'
    amount = Column(Float)
    date = Column(String)    # حفظ التاريخ والوقت

# هذا السطر السحري يقوم بإنشاء الجداول في السيرفر فوراً إذا لم تكن موجودة
Base.metadata.create_all(bind=engine)

# إعداد خوارزمية التشفير
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

# --- إعدادات الأمان ونظام التوكن ---
SECRET_KEY = "gdldf52145*ytfrf-frtredà@&6é0'+" # هذا هو مفتاحك السري
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = "f9afe7e1bc006f79f75bafe764b0f117"
DB_FILE = "network_database.json"
TICKETS_FILE = "tickets_database.json" 

# --- دالات الحفظ والقراءة المرتبطة بقاعدة البيانات ---
def load_db():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    
    result = []
    for u in users:
        result.append({
            "username": u.username,
            "password": u.password,
            "role": u.role,
            "balance": u.balance,
            "rtp": u.rtp,
            "is_blocked": u.is_blocked,
            "created_by": u.created_by
        })
    
    if not result:
        default_users = [
            {"username": "fethi", "password": hash_password("123456"), "role": "owner", "balance": 999999.00, "rtp": 50, "is_blocked": 0, "created_by": "System"},
            {"username": "samir", "password": hash_password("123456"), "role": "super_admin", "balance": 5000.00, "rtp": 50, "is_blocked": 0, "created_by": "fethi"}
        ]
        save_db(default_users)
        return default_users
        
    return result

def save_db(data):
    db = SessionLocal()
    for item in data:
        user = db.query(User).filter(User.username == item["username"]).first()
        if user:
            user.password = item.get("password", user.password)
            user.role = item.get("role", user.role)
            user.balance = item.get("balance", user.balance)
            user.rtp = item.get("rtp", user.rtp)
            user.is_blocked = item.get("is_blocked", user.is_blocked)
            user.created_by = item.get("created_by", user.created_by)
        else:
            new_user = User(
                username=item["username"],
                password=item["password"],
                role=item.get("role", "player"),
                balance=item.get("balance", 0.0),
                rtp=item.get("rtp", 50),
                is_blocked=item.get("is_blocked", 0),
                created_by=item.get("created_by", "System")
            )
            db.add(new_user)
    
    db.commit()
    db.close()

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

# --- النماذج وهياكل البيانات ---
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
    
    user = None
    if uname == "fethi" and req.password == "123456":
        user = {"username": "fethi", "role": "owner", "balance": 999999.00}
    else:
        for u in db:
            if u["username"] == uname:
                if verify_password(req.password, u.get("password", "")):
                    if u["is_blocked"] == 1: 
                        raise HTTPException(status_code=403, detail="Ce compte est bloqué")
                    user = u
                    break
    
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    
    access_token = create_access_token(data={"sub": user["username"]})
    
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
            
    hashed_pwd = hash_password(req.password)
    
    new_user = {
        "username": uname,
        "password": hashed_pwd,
        "role": req.role,
        "balance": 0.00,
        "rtp": 50,
        "is_blocked": 0,
        "created_by": req.created_by
    }
    db.append(new_user)
    save_db(db)
    return {"status": "success", "message": "Compte créé"}

# أضف هذه الدوال في أعلى الملف (مثلاً قبل السطر 200 أو فوق دالة daily_spin)
def has_user_spun_today(username):
    db = load_db()
    for u in db:
        if u["username"] == username:
            return u.get("last_spin_date") == "2026-07-04"
    return False

def log_spin_usage(username):
    db = load_db()
    for u in db:
        if u["username"] == username:
            u["last_spin_date"] = "2026-07-04"
            break
    save_db(db)

def add_balance(username, amount):
    db = load_db()
    for u in db:
        if u["username"] == username:
            # نستخدم float لتحويل الرصيد لرقم صحيح للعمليات الحسابية
            u["balance"] = float(u.get("balance", 0)) + amount
            break
    save_db(db)

@app.post("/api/spin")
async def daily_spin(current_user: User = Depends(get_current_user)):
    # تحقق من قاعدة البيانات هل دار اللاعب العجلة اليوم
    if has_user_spun_today(current_user.username): 
        return {"status": "error", "message": "لقد استخدمت فرصتك اليوم، عد غداً!"}
    
    # نسبة 95% للخسارة
    if random.random() < 0.95:
        log_spin_usage(current_user.username)
        return {"status": "loss", "message": "حظ سعيد في المرة القادمة!"}
    
    # نسبة 5% للفوز
    prizes = [5, 10, 20, 50] 
    won_amount = random.choice(prizes)
    
    # تحديث الرصيد وتسجيل العملية
    add_balance(current_user.username, won_amount)
    log_spin_usage(current_user.username)
    
    return {"status": "win", "amount": won_amount, "message": f"مبروك! ربحت {won_amount} TND"}


# --- 📊 مسارات الإدارة والتحكم المالي ---
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

        current_balance = target_user.get("balance", 0)
        daily_deps = target_user.get("daily_deposits", 0)

        if current_balance < 1.0 and daily_deps > 0:
            cashback_bonus = daily_deps * 0.10
            target_user["balance"] = current_balance + cashback_bonus
            target_user["daily_deposits"] = 0
        
        target_user["balance"] = target_user.get("balance", 0) + amount
        
        if admin != "system":
            target_user["daily_deposits"] = target_user.get("daily_deposits", 0) + amount

    elif req.action == "withdraw":
        if target_user.get("balance", 0) < amount:
            raise HTTPException(status_code=400, detail="Solde insuffisant")
        
        target_user["balance"] -= amount
        
        if admin != "system" and admin != "fethi" and admin_user:
            admin_user["balance"] = admin_user.get("balance", 0) + amount

    # --- (الجديد) تسجيل المعاملة في قاعدة البيانات ---
    db_session = SessionLocal()
    new_tx = Transaction(
        admin_username=admin,
        target_username=target,
        action=req.action,
        amount=amount,
        date=datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    db_session.add(new_tx)
    db_session.commit()
    db_session.close()
    # ------------------------------------------------

    save_db(db)
    return {"status": "success", "balance": target_user["balance"]}

# --- (الجديد) مسار جلب سجل المعاملات ---
@app.get("/api/admin/transactions-history")
async def get_transactions_history(username: str):
    db_session = SessionLocal()
    uname = username.lower().strip()
    
    # إذا كان المدير الأساسي يرى كل شيء، وإلا يرى التحويلات التي قام بها أو استلمها فقط
    if uname == "fethi":
        txs = db_session.query(Transaction).order_by(Transaction.id.desc()).all()
    else:
        txs = db_session.query(Transaction).filter(
            (Transaction.admin_username == uname) | (Transaction.target_username == uname)
        ).order_by(Transaction.id.desc()).all()
        
    result = []
    for t in txs:
        result.append({
            "id": t.id,
            "admin_username": t.admin_username,
            "target_username": t.target_username,
            "action": t.action,
            "amount": t.amount,
            "date": t.date
        })
    db_session.close()
    return result


# --- مسارات أوراق الرهان ---
@app.post("/api/admin/save-ticket")
async def save_player_ticket(req: SaveTicketRequest):
    tickets_db = load_tickets_db()
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

@app.get("/api/admin/get-player-tickets")
async def get_player_tickets(username: str):
    tickets_db = load_tickets_db()
    uname = username.lower().strip()
    player_tickets = [t for t in tickets_db if t["username"] == uname]
    return player_tickets

@app.get("/api/admin/get-history")
async def get_history(username: str):
    tickets_db = load_tickets_db()
    uname = username.lower().strip()
    user_history = [t for t in tickets_db if t["username"] == uname]
    return {"history": user_history}


# --- مسارات إضافية ---
@app.post("/api/admin/change-player-password")
async def change_player_password(req: ChangePlayerPasswordRequest):
    target = req.target_username.lower().strip()
    db = load_db()
    
    for u in db:
        if u["username"] == target:
            u["password"] = hash_password(req.new_password)
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
    current_time = time.time()
    
    # لا تجلب البيانات إلا إذا مر أكثر من 15 دقيقة (900 ثانية) على آخر تحديث
    if current_time - cache["last_update"] > 900: 
        leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league"]
        all_matches = []
        for league in leagues:
            try:
                # تأكد أن API_KEY معرف لديك في الملف
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds?apiKey={API_KEY}&regions=eu&markets=h2h"
                response = requests.get(url, timeout=5) 
                if response.status_code == 200:
                    all_matches.extend(response.json())
            except Exception:
                pass
        
        # تحديث الكاش
        cache["matches"] = all_matches
        cache["last_update"] = current_time
    
    # إرجاع البيانات المخزنة فوراً
    return cache["matches"]

@app.get("/")
async def root():
    return {"status": "Alpha Secure Database Backend Running Perfectly"}
