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
import time
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
import asyncio
from fastapi import UploadFile, File, Form
import shutil
import os
from fastapi.staticfiles import StaticFiles
import httpx



# جلب رابط قاعدة البيانات
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local_test.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# تشغيل محرك قاعدة البيانات
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "alpha_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)
    balance = Column(Float, default=0.0)
    rtp = Column(Integer, default=50)
    is_blocked = Column(Integer, default=0)
    created_by = Column(String)
    # --- الأعمدة الجديدة التي كانت مفقودة لحفظ البيانات ---
    last_spin_date = Column(String, default="")
    daily_deposits = Column(Float, default=0.0)

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String)
    target_username = Column(String)
    action = Column(String)  
    amount = Column(Float)
    date = Column(String)  
    image_path = Column(String, nullable=True)

from sqlalchemy import text

try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN image_path VARCHAR"))
except Exception:
    pass
Base.metadata.create_all(bind=engine)

# إعداد خوارزمية التشفير
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

SECRET_KEY = "gdldf52145*ytfrf-frtredà@&6é0'+" 
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
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# سحب المفتاح بأمان
API_KEY = os.environ.get("API_KEY", "f9afe7e1bc006f79f75bafe764b0f117")
TICKETS_FILE = "tickets_database.json" 
# --- محرك التسوية التلقائي (Background Task) ---
async def auto_settle_tickets():
    """هذه الدالة تعمل في الخلفية بشكل دائم لفحص التذاكر"""
    await asyncio.sleep(10) 
    
    while True:
        try:
            print("⏳ [Auto-Settler] جاري فحص التذاكر المعلقة...")
            tickets_db = load_tickets_db()
            db = load_db()
            changes_made = False
            
            pending_tickets = [t for t in tickets_db if t.get("status") == "encours"]
            
            for ticket in pending_tickets:
                simulated_result = random.choice(["gagne", "perdu"]) 
                print(f"🔄 معالجة التذكرة #{ticket['ticket_id']} - النتيجة: {simulated_result}")
                
                ticket["status"] = simulated_result
                changes_made = True
                
                if simulated_result == "gagne":
                    target_username = ticket["username"]
                    win_amount = float(ticket.get("gain", 0))
                    
                    for u in db:
                        if u["username"] == target_username:
                            u["balance"] = float(u.get("balance", 0)) + win_amount
                            print(f"💰 تم إضافة {win_amount} TND لحساب {target_username}")
                            break
            
            if changes_made:
                save_tickets_db(tickets_db)
                save_db(db)
                print("✅ [Auto-Settler] تم حفظ النتائج وتحديث الأرصدة بنجاح.")
                
        except Exception as e:
            print(f"❌ [Auto-Settler] حدث خطأ: {e}")
        
        await asyncio.sleep(60) 

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(auto_settle_tickets())

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
            "created_by": u.created_by,
            "last_spin_date": u.last_spin_date,
            "daily_deposits": u.daily_deposits
        })
    
    if not result:
        default_users = [
            {"username": "fethi", "password": hash_password("123456"), "role": "owner", "balance": 999999.00, "rtp": 50, "is_blocked": 0, "created_by": "System", "last_spin_date": "", "daily_deposits": 0.0},
            {"username": "samir", "password": hash_password("123456"), "role": "super_admin", "balance": 5000.00, "rtp": 50, "is_blocked": 0, "created_by": "fethi", "last_spin_date": "", "daily_deposits": 0.0}
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
            user.last_spin_date = item.get("last_spin_date", user.last_spin_date)
            user.daily_deposits = item.get("daily_deposits", user.daily_deposits)
        else:
            new_user = User(
                username=item["username"],
                password=item["password"],
                role=item.get("role", "player"),
                balance=item.get("balance", 0.0),
                rtp=item.get("rtp", 50),
                is_blocked=item.get("is_blocked", 0),
                created_by=item.get("created_by", "System"),
                last_spin_date=item.get("last_spin_date", ""),
                daily_deposits=item.get("daily_deposits", 0.0)
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

@app.post("/api/login")
async def login_user(req: LoginRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    # ابحث عن المستخدم
    user = None
    for u in db:
        if u["username"] == uname:
           if u["username"] == uname:
                # التحقق من كلمة المرور وحل مشكلة الـ 72 بايت
                try:
                    is_valid = verify_password(req.password, u.get("password", ""))
                except ValueError:
                    is_valid = False
                
                if is_valid:
                    if u.get("is_blocked") == 1:
                        raise HTTPException(status_code=403, detail="Ce compte est bloqué")
                    user = u
                    break
    
    # 🚨 التعديل الضروري هنا: إذا لم نجد المستخدم، نرسل خطأ صريح
    if not user:
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect")
        
    # إذا نجح الدخول، ننشئ التوكن ونرسل البيانات
    token = create_access_token(data={"sub": user["username"]})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
        "balance": user["balance"],
        "created_by": user.get("created_by", "System")
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
        "created_by": req.created_by,
        "last_spin_date": "",
        "daily_deposits": 0.0
    }
    db.append(new_user)
    save_db(db)
    return {"status": "success", "message": "Compte créé"}

def has_user_spun_today(username):
    db = load_db()
    today = datetime.now().strftime("%Y-%m-%d")
    for u in db:
        if u["username"] == username:
            return u.get("last_spin_date") == today
    return False

def log_spin_usage(username):
    db = load_db()
    today = datetime.now().strftime("%Y-%m-%d")
    for u in db:
        if u["username"] == username:
            u["last_spin_date"] = today
            break
    save_db(db)

def add_balance(username, amount):
    db = load_db()
    for u in db:
        if u["username"] == username:
            u["balance"] = float(u.get("balance", 0)) + amount
            break
    save_db(db)

@app.post("/api/spin")
async def daily_spin(current_user: str = Depends(get_current_user)):
    if has_user_spun_today(current_user): 
        return {"status": "error", "message": "لقد استخدمت فرصتك اليوم، عد غداً!"}
    
    if random.random() < 0.95:
        log_spin_usage(current_user)
        return {"status": "loss", "message": "حظ سعيد في المرة القادمة!"}
    
    prizes = [5, 10, 20, 50] 
    won_amount = random.choice(prizes)
    
    add_balance(current_user, won_amount)
    log_spin_usage(current_user)
    
    return {"status": "win", "amount": won_amount, "message": f"مبروك! ربحت {won_amount} TND"}

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

    save_db(db)
    return {"status": "success", "balance": target_user["balance"]}

@app.get("/api/admin/transactions-history")
async def get_transactions_history(username: str):
    db_session = SessionLocal()
    uname = username.lower().strip()
    
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
class DeleteAccountRequest(BaseModel):
    admin_username: str
    target_username: str

@app.delete("/api/admin/delete-account")
async def delete_account(req: DeleteAccountRequest):
    db = load_db()
    target = req.target_username.lower().strip()
    
    # فلترة شاملة لاستثناء الحساب من القائمة
    new_db = [u for u in db if u.get("username", "").lower().strip() != target]
    
    if len(new_db) == len(db):
        raise HTTPException(status_code=404, detail="Non trouvé")
        
    save_db(new_db)
    return {"status": "success", "message": "Supprimé"}

@app.get("/api/get-sports-url")
async def get_sports_url():
    try:
        # البيانات الحساسة مخبأة هنا داخل السيرفر ولا يراها أي لاعب في المتصفح
        token = "9a418a80d898dd95f120c321012a67cf"
        
        # الرابط المباشر للسبورت بوك الخاص بالمزود
        provider_sports_url = f"https://alpha-backend-server.onrender.com/sports?token={token}"
        
        # نرسل الرابط للمتصفح بشكل نظيف
        return {"url": provider_sports_url}
        
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

# تعريف الذاكرة المؤقتة للمباريات لتفادي الخطأ
cache = {
    "last_update": 0,
    "matches": []
}

@app.get("/api/sports/get-live-matches")
async def get_sports():
    current_time = time.time()
    
    if current_time - cache["last_update"] > 900: 
        leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league"]
        all_matches = []
        for league in leagues:
            try:
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds?apiKey={API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal"
                response = requests.get(url, timeout=5) 
                if response.status_code == 200:
                    all_matches.extend(response.json())
            except Exception:
                pass
        
        cache["matches"] = all_matches
        cache["last_update"] = current_time
    
    return cache["matches"]

@app.get("/")
async def root():
    return {"status": "Alpha Secure Database Backend Running Perfectly"}

@app.post("/api/admin/request-transaction")
async def request_transaction(
    target_username: str = Form(...),
    action: str = Form(...),
    amount: float = Form(...),
    tx_id: str = Form(...),
    file: UploadFile = File(None),
    current_user: str = Depends(get_current_user)
):
    db_session = SessionLocal()
    try:
        file_path = ""
        if file and file.filename:
            UPLOAD_DIR = "uploads"
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            file_extension = os.path.splitext(file.filename)[1]
            file_name = f"{tx_id}{file_extension}"
            file_path = os.path.join(UPLOAD_DIR, file_name)
            
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        
        new_tx = Transaction(
            admin_username="PENDING",
            target_username=target_username,
            action=action,
            amount=amount,
            date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            image_path=file_path
        )
        
        db_session.add(new_tx)
        db_session.commit()
        return {"status": "success", "message": "طلبك قيد المراجعة"}
    except Exception as e:
        db_session.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db_session.close()
# ==========================================
# مسارات معالجة طلبات الإيداع والسحب المعلقة
# ==========================================

class HandleRequestModel(BaseModel):
    transaction_id: int
    decision: str # 'accept' or 'reject'
    admin_username: str

@app.get("/api/admin/pending-requests")
async def get_pending_requests():
    db_session = SessionLocal()
    # جلب جميع المعاملات التي تحمل اسم PENDING
    txs = db_session.query(Transaction).filter(Transaction.admin_username == "PENDING").order_by(Transaction.id.desc()).all()
    result = [{"id": t.id, "target_username": t.target_username, "action": t.action, "amount": t.amount, "date": t.date, "image_path": t.image_path} for t in txs]
    db_session.close()
    return result
# هذا هو الجسر الذي يربط اسم التنبيهات بالوظيفة الموجودة
@app.get("/api/admin/get-pending-deposits")
async def get_pending_deposits_alias():
    return await get_pending_requests()


@app.post("/api/admin/handle-request")
async def handle_pending_request(req: HandleRequestModel):
    db_session = SessionLocal()
    tx = db_session.query(Transaction).filter(Transaction.id == req.transaction_id).first()
    
    if not tx or tx.admin_username != "PENDING":
        db_session.close()
        raise HTTPException(status_code=404, detail="Demande introuvable ou déjà traitée")

    # في حالة الرفض: نقوم بحذف الطلب فقط
    if req.decision == "reject":
        db_session.delete(tx)
        db_session.commit()
        db_session.close()
        return {"status": "success", "message": "Demande rejetée"}

    # في حالة القبول: نقوم بتحديث رصيد اللاعب
    db = load_db()
    target_user = next((u for u in db if u["username"] == tx.target_username), None)
    if not target_user:
        db_session.close()
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if tx.action == "deposit_request":
        target_user["balance"] = float(target_user.get("balance", 0)) + tx.amount
        tx.action = "charge" # تحويلها إلى شحن رسمي
    elif tx.action == "withdraw_request":
        if target_user.get("balance", 0) < tx.amount:
            db_session.close()
            raise HTTPException(status_code=400, detail="Solde insuffisant pour le retrait")
        target_user["balance"] = float(target_user.get("balance", 0)) - tx.amount
        tx.action = "withdraw" # تحويلها إلى سحب رسمي

    # تسجيل اسم المدير الذي وافق على العملية
    tx.admin_username = req.admin_username
    db_session.commit()
    db_session.close()
    save_db(db) # حفظ الرصيد الجديد
    
    return {"status": "success", "message": "Demande approuvée avec succès"} 
@app.post("/api/provider/launch-sportsbook")
def launch_sportsbook(data: dict):
    print("--- 📢 وصل الطلب إلى السيرفر بنجاح! ---")
    # بيانات وكالتك الثابتة
    AGENT_CODE = "TUNISS10"
    AGENT_TOKEN = "1d370dd23266b78979ad81e0bda47708",
    PROVIDER_ENDPOINT = "https://api.nexusggr.com"
    
    # بناء الرسالة النهائية والمثالية
    provider_code = data.get("provider_code")
    payload = {
        "method": "game_list",
        "agent_code": "TUNISS10",
        "agent_token": "9a418a80d898dd95f120c321012a67cf",
        "provider_code": provider_code
      }
    
    
    headers = {
        "Content-Type": "application/json"
    }

    try:
        import requests
        response = requests.post(PROVIDER_ENDPOINT, json=payload, headers=headers)
        response_data = response.json()
        print("محتوى رد المزود هو:", response_data)
        
        # طباعة الرد في الكونسول للرقابة
     
        print("NexusGGR Response:", json.dumps(response_data, ensure_ascii=True))
        
        # استخراج الرابط الحقيقي
        game_url = response_data.get("url") or response_data.get("launch_url")
        
        if game_url:
            return {"launch_url": game_url}
        else:
            return {"error": "المزود رفض الطلب", "details": response_data}
            
    except Exception as e:
        return {"error": str(e)}
      
from fastapi import Request
from fastapi.responses import JSONResponse

@app.post("/api/provider/launch-casino")
@app.post("/api/provider/launch-casino")
async def launch_casino(request: Request):
    try:
        data = await request.json()
        game_code = data.get("game_code")
        provider_code = data.get("provider_code")
        user_code = "fethi2_test"  # تأكد من هذا الاسم إذا كان متغير
        
        PROVIDER_ENDPOINT = "https://api.nexusggr.com"

        payload = {
            "method": "game_launch",
            "agent_code": "TUNISS10",
            "agent_token": "9a418a80d898dd95f120c321012a67cf",
            "provider_code": provider_code,
            "game_code": game_code,
            "user_code": user_code,
            "lang": "fr",
            "currency": "TND"
        }

        headers = {"Content-Type": "application/json"}
        
        import requests
        response = requests.post(PROVIDER_ENDPOINT, json=payload, headers=headers)
        response_data = response.json()
        
        print(f"رد المزود للعبة {game_code}:", response_data)
        
        game_url = response_data.get("url") or response_data.get("launch_url")
        
        if game_url:
            return {"launch_url": game_url}
        else:
            return {"error": "المزود رفض الطلب", "details": response_data}
            
    except Exception as e:
        return {"error": str(e)}

@app.post("/gold_api")
async def seamless_wallet_handler(request: Request):
    try:
        data = await request.json()
        method = data.get("method")
        user_code = data.get("user_code")  # اسم اللاعب

        # ----------------------------------------------------
        # 1. حالة الاستعلام عن الرصيد
        # ----------------------------------------------------
        if method == "user_balance":
            player_balance = 100.00  # رقم مؤقت للتجربة
            return JSONResponse(content={
                "status": 1,
                "user_balance": player_balance
            })

        # ----------------------------------------------------
        # 2. حالة العمليات المالية (رهان أو فوز)
        # ----------------------------------------------------
        elif method == "transaction":
            game_type = data.get("game_type")  # لمعرفة نوع اللعبة (SB, slot, live)
            tx_data = data.get(game_type, {})
            
            bet_money = float(tx_data.get("bet_money", 0))
            win_money = float(tx_data.get("win_money", 0))
            txn_type = tx_data.get("txn_type")

            # 🎯 رادار التقاط تذاكر الرياضة
            if game_type == "SB" and "info" in data:
                try:
                    import json
                    ticket_info = json.loads(data.get("info"))
                    
                    coupon_code = ticket_info.get("couponCode")
                    ticket_status = ticket_info.get("status")
                    stake = ticket_info.get("stake")

                    print(f"🎟️ تم التقاط تذكرة! اللاعب: {user_code} | التذكرة: {coupon_code} | المبلغ: {stake} | الحالة: {ticket_status}")
                except Exception as e:
                    print(f"⚠️ خطأ في قراءة بيانات التذكرة: {e}")

            # TODO: جلب الرصيد الحقيقي من قاعدة البيانات قبل العملية
            player_balance = 100.00  # رقم مؤقت

            # أ. معالجة خصم الرهان (Debit)
            if txn_type in ["debit", "debit_credit"]:
                if player_balance < bet_money:
                    return JSONResponse(content={"status": 0, "msg": "INSUFFICIENT_USER_FUNDS"})
                player_balance -= bet_money

            # ب. معالجة إضافة الربح (Credit)
            if txn_type in ["credit", "debit_credit"]:
                player_balance += win_money

            return JSONResponse(content={
                "status": 1,
                "user_balance": round(player_balance, 2)
            })

        # حالة طلب غير معروف
        else:
            return JSONResponse(content={"status": 0, "msg": "UNKNOWN_METHOD"})

    except Exception as e:
        print(f"Error in gold_api: {e}")
        return JSONResponse(content={"status": 0, "msg": "INTERNAL_ERROR"})

# ==========================================
# نظام التخزين المؤقت (Cache) لألعاب الكازينو
# ==========================================
GAMES_CACHE = {}
CACHE_TIME_LIMIT = 3600  # مدة الحفظ بالثواني (ساعة واحدة)

@app.post("/api/get-games-list")
async def get_games(request: Request):
    data = await request.json()
    provider_code = data.get("provider_code")
    
    if not provider_code:
        return {"status": 0, "msg": "Provider code is missing"}

    import time
    current_time = time.time()

    # فحص الذاكرة
    if provider_code in GAMES_CACHE:
        cached_data = GAMES_CACHE[provider_code]
        if current_time - cached_data['time'] < CACHE_TIME_LIMIT:
            print(f"⚡ جلب ألعاب {provider_code} فوراً من ذاكرة السيرفر السريعة (الكاش)")
            return cached_data['data']

    # إذا لم تكن في الذاكرة، نكلم المزود
    print(f"🌍 جلب ألعاب {provider_code} من المزود الخارجي (Nexus)...")
    payload = {
        "method": "game_list",
        "agent_code": "TUNISS10",
        "agent_token": "9a418a80d898dd95f120c321012a67cf",
        "provider_code": provider_code
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.nexusggr.com", json=payload)
            response_data = response.json()
            
            if "games" in response_data or response_data.get("status") == 1:
                GAMES_CACHE[provider_code] = {
                    'time': current_time,
                    'data': response_data
                }
            return response_data
            
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال بالمزود: {e}")
            if provider_code in GAMES_CACHE:
                return GAMES_CACHE[provider_code]['data']
            return {"status": 0, "msg": "Error connecting to provider"}

# ==========================================
# جلب قائمة المزودين (Providers List) ديناميكياً
# ==========================================
@app.get("/api/get-providers")
async def get_providers():
    print("🌍 جلب قائمة المزودين من Nexus...")
    payload = {
        "method": "provider_list",
        "agent_code": "TUNISS10",
        "agent_token": "9a418a80d898dd95f120c321012a67cf"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.nexusggr.com", json=payload)
            return response.json()
        except Exception as e:
         print(f"⚠️ خطأ في جلب المزودين: {e}")
    return {"status": 0, "msg": "Error connecting to provider"}

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # يسمح لأي موقع بالاتصال، وهذا سيحل مشكلتك فوراً
    allow_methods=["*"],
    allow_headers=["*"],
)
