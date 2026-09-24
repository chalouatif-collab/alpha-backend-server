from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form, Header, Body, Query, WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt
from datetime import datetime, timedelta
import random
import json
import os
import time
import hmac
import hashlib
import urllib.parse
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
import asyncio
db_lock = asyncio.Lock()
import shutil
from fastapi.staticfiles import StaticFiles
import httpx
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse, StreamingResponse
from dotenv import load_dotenv
import pyotp
import qrcode
import io
import html
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
import uuid

PROCESSED_TRANSACTIONS = set()

# ==========================================
# 🎮 إعدادات 01.tech Aggregator (للاختبار)
# ==========================================
load_dotenv()
ZEROONE_AUTH_TOKEN = os.getenv("ZEROONE_AUTH_TOKEN", "S3dqpz5cUrBj7Cd36ziPHSoimjqVLZospYuw/MRb0p0=")
ZEROONE_BASE_URL = os.getenv("ZEROONE_BASE_URL", "https://alphabet1.casino.preprod.avegas.games")
ZEROONE_CASINO_ID = os.getenv("ZEROONE_CASINO_ID", "alphabet1")

ADMIN_USER = os.getenv("ADMIN_USERNAME")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY", "alpha-secure-key-2026")

# 1. إعداد الاتصال بـ Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json") 
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://tounsibet-65e94-default-rtdb.firebaseio.com/'
    })

# 2. دالة جلب البيانات من السحابة
def load_db():
    ref = db.reference('/') 
    data = ref.get()
    
    if data is None:
        return {"users": [], "shop_withdrawals": [], "tickets": []}
    
    users = data.get("users", [])
    if isinstance(users, dict):
        users = list(users.values())
        
    class MagicDB(list):
        def __init__(self, users_list, full_data):
            super().__init__(users_list)
            self.full_data = full_data
            if "shop_withdrawals" not in self.full_data:
                self.full_data["shop_withdrawals"] = []
                
        def get(self, key, default=None):
            return self.full_data.get(key, default)
            
        def __contains__(self, key):
            return key in self.full_data
            
        def __setitem__(self, key, value):
            self.full_data[key] = value

    return MagicDB(users, data)

# 3. دالة الحفظ السحابي الفوري
def save_db(data):
    ref = db.reference('/')
    if hasattr(data, 'full_data'):
        data.full_data['users'] = list(data)
        ref.set(data.full_data)
    elif isinstance(data, list):
        ref.child('users').set(list(data))
    else:
        ref.set(data)

DB_FILE = "tickets_database.json"
TICKETS_FILE = "tickets_database.json" 

# ==========================================
# إعدادات قاعدة البيانات والتشفير
# ==========================================
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local_test.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "tounsibet_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    role = Column(String)
    balance = Column(Float, default=0.0)
    rtp = Column(Integer, default=50)
    is_blocked = Column(Integer, default=0)
    created_by = Column(String)
    last_spin_date = Column(String, default="")
    daily_deposits = Column(Float, default=0.0)
    two_factor_secret = Column(String, nullable=True)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String)
    target_username = Column(String)
    action = Column(String)  
    amount = Column(Float)
    date = Column(String)  
    image_path = Column(String, nullable=True)
    tx_id = Column(String, nullable=True)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String, index=True)
    action_type = Column(String)
    details = Column(String)
    date = Column(String, default=lambda: str(datetime.now()))

try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN image_path VARCHAR"))
except Exception: pass

try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN tx_id VARCHAR"))
except Exception: pass
Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def hash_password(password: str): return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try: return pwd_context.verify(plain_password, hashed_password)
    except Exception: return False

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

async def get_admin_user(current_user: str = Depends(get_current_user)):
    db_data = load_db()
    user = next((u for u in db_data if u["username"] == current_user), None)
    if not user or user.get("role") not in ["owner","manager", "super_admin", "admin","shop"]:
        raise HTTPException(status_code=403, detail="Access Denied")
    return current_user

# ==========================================
# إعدادات تطبيق FastAPI الأساسية
# ==========================================
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tounsibet.com",
        "https://tounsibet-player-frontend.onrender.com",
        "https://alphabet216.com",
        "http://localhost:5500",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

TELEGRAM_TOKEN = "8879806026:AAEB64RCPW4KzsUXUlDeztP_PzjtxkJv_4g"
TELEGRAM_CHAT_ID = "7700782611"

async def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)
    except Exception: pass

def verify_nexus_ip(request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for: return forwarded_for.split(",")[0].strip()
    return request.client.host

def log_admin_action(admin_username: str, action_type: str, details: str):
    db_session = SessionLocal()
    try:
        log_entry = AuditLog(admin_username=admin_username, action_type=action_type, details=details)
        db_session.add(log_entry)
        db_session.commit()
    except Exception: pass
    finally: db_session.close()

# ==========================================
# 🎮 01.TECH AGGREGATOR INTEGRATION
# ==========================================
def verify_01tech_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature: return False
    computed_sig = hmac.new(ZEROONE_AUTH_TOKEN.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_sig, signature)

@app.post("/v2/provider_a8r.Round/BetWin")
async def bet_win(request: Request, x_request_sign: Optional[str] = Header(None)):
    body_bytes = await request.body()
    if not verify_01tech_signature(body_bytes, x_request_sign):
        raise HTTPException(status_code=400, detail="Invalid Signature")
    
    data = await request.json()
    account_id = str(data.get("account_id"))
    round_id = data.get("round_id")
    transactions = data.get("transactions", [])
    
    async with db_lock:
        db_data = load_db()
        target_user = next((u for u in db_data if str(u.get("username", "")).lower() == account_id.lower()), None)
        if not target_user: raise HTTPException(status_code=404, detail="Player not found")
            
        current_balance = float(target_user.get("balance", 0.0))
        processed_transactions = []
        
        db_session = SessionLocal()
        try:
            for tx in transactions:
                id_provider = tx.get("id_provider")
                amount = float(tx.get("amount", 0))
                tx_type = tx.get("type") 
                
                existing_tx = db_session.query(Transaction).filter(Transaction.tx_id == id_provider).first()
                if existing_tx:
                    processed_transactions.append({"bonus_amount": "0.00", "id": str(existing_tx.id), "id_provider": id_provider})
                    continue
                
                if tx_type == "bet":
                    if current_balance < amount: raise HTTPException(status_code=400, detail="Insufficient funds")
                    current_balance -= amount
                elif tx_type == "win":
                    current_balance += amount
                    
                aggregator_tx_id = str(uuid.uuid4())
                new_sql_tx = Transaction(admin_username="01TECH", target_username=account_id, action=tx_type, amount=amount, tx_id=id_provider, date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                db_session.add(new_sql_tx)
                
                processed_transactions.append({"bonus_amount": "0.00", "id": aggregator_tx_id, "id_provider": id_provider})
                
            db_session.commit()
            target_user["balance"] = current_balance
            save_db(db_data)
        except HTTPException as he:
            db_session.rollback()
            raise he
        except Exception as e:
            db_session.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally: db_session.close()
            
    return {"balance": f"{current_balance:.2f}", "round_id": round_id, "transactions": processed_transactions}

@app.post("/v2/provider_a8r.Round/Finish")
async def finish_round(request: Request, x_request_sign: Optional[str] = Header(None)):
    body_bytes = await request.body()
    if not verify_01tech_signature(body_bytes, x_request_sign): raise HTTPException(status_code=400, detail="Invalid Signature")
    
    data = await request.json()
    account_id = str(data.get("account_id"))
    db_data = load_db()
    target_user = next((u for u in db_data if str(u.get("username", "")).lower() == account_id.lower()), None)
    current_balance = float(target_user.get("balance", 0.0)) if target_user else 0.0
    return {"balance": f"{current_balance:.2f}"}

@app.post("/v2/provider_a8r.Round/Rollback")
async def rollback_round(request: Request, x_request_sign: Optional[str] = Header(None)):
    body_bytes = await request.body()
    if not verify_01tech_signature(body_bytes, x_request_sign): raise HTTPException(status_code=400, detail="Invalid Signature")
    
    data = await request.json()
    account_id = str(data.get("account_id"))
    round_id_provider = data.get("round_id_provider")
    transactions = data.get("transactions", [])
    
    async with db_lock:
        db_data = load_db()
        target_user = next((u for u in db_data if str(u.get("username", "")).lower() == account_id.lower()), None)
        if not target_user: raise HTTPException(status_code=404, detail="Player not found")
        
        current_balance = float(target_user.get("balance", 0.0))
        processed_rollbacks = []
        
        db_session = SessionLocal()
        try:
            for tx in transactions:
                id_provider = tx.get("id_provider")
                original_id_provider = tx.get("original_id_provider")
                
                orig_tx = db_session.query(Transaction).filter(Transaction.tx_id == original_id_provider).first()
                aggregator_rollback_id = str(uuid.uuid4())
                
                if orig_tx:
                    amount = float(orig_tx.amount)
                    if orig_tx.action == "bet": current_balance += amount
                    elif orig_tx.action == "win": current_balance -= amount
                        
                    new_rollback = Transaction(admin_username="01TECH", target_username=account_id, action="rollback", amount=amount, tx_id=id_provider, date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    db_session.add(new_rollback)
                    
                processed_rollbacks.append({"id": aggregator_rollback_id if orig_tx else "", "id_provider": id_provider})
                
            db_session.commit()
            target_user["balance"] = current_balance
            save_db(db_data)
        except Exception as e:
            db_session.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally: db_session.close()
            
    return {"balance": f"{current_balance:.2f}", "round_id": round_id_provider, "transactions": processed_rollbacks}

async def fetch_01tech_games():
    url = f"{ZEROONE_BASE_URL}/v2/a8r_provider.Game/List"
    payload = {"casino_id": ZEROONE_CASINO_ID}
    payload_json = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(ZEROONE_AUTH_TOKEN.encode('utf-8'), payload_json.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-REQUEST-SIGN": signature}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, content=payload_json, headers=headers)
            
            if response.status_code != 200:
                print(f"🚨 01.TECH REJECTED: {response.text}")
                return {"error": response.text, "games": []}
                
            data = response.json()
            all_games = []
            base_image_url = data.get("image_assets", {}).get("base_url", "")
            
            if "providers" in data:
                for provider in data["providers"]:
                    provider_name = provider.get("name", "01TECH")
                    for game in provider.get("games", []):
                        img_url = ""
                        if "images" in game:
                            img_path = game["images"].get("square") or game["images"].get("horizontal")
                            if img_path: img_url = f"{base_image_url}{img_path}"
                        all_games.append({
                            "game_code": game.get("id"),
                            "game_name": game.get("title"),
                            "provider": provider_name,
                            "banner": img_url,
                            "has_demo": game.get("has_demo", False)
                        })
            return {"games": all_games}
        except Exception as e:
            return {"error": str(e), "games": []}

class ProviderRequest(BaseModel): provider_code: str

@app.post("/api/get-providers")
async def get_real_games(request: ProviderRequest):
    return await fetch_01tech_games()

@app.post("/api/launch-01tech-game")
async def launch_01tech_game(request: Request):
    data = await request.json()
    payload = {
        "casino_id": ZEROONE_CASINO_ID,
        "game_id": data.get("game_id"),
        "account_id": str(data.get("account_id")),
        "currency": "TND",
        "session_id": str(uuid.uuid4()),
        "language": "fr",
        "return_url": "https://alphabet216.com/" 
    }
    
    payload_json = json.dumps(payload, separators=(',', ':'))
    signature = hmac.new(ZEROONE_AUTH_TOKEN.encode('utf-8'), payload_json.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-REQUEST-SIGN": signature}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{ZEROONE_BASE_URL}/v2/a8r_provider.Launcher/Real", content=payload_json, headers=headers)
            if response.status_code != 200: return {"error": response.text}
            return {"game_url": response.json().get("url")}
        except Exception as e:
            return {"error": str(e)}

@app.get("/api/get-server-ip")
async def get_server_ip():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.ipify.org")
            return {"server_ip": response.text}
    except Exception as e: return {"error": str(e)}

# ==========================================
# باقي مسارات ولوحات الإدارة الأساسية
# ==========================================
@app.get("/owner.html")
async def redirect_owner(): return RedirectResponse(url="/panel/owner/", status_code=303)
@app.get("/super_admin.html")
async def redirect_super_admin(): return RedirectResponse(url="/panel/super_admin/", status_code=303)
@app.get("/admin.html")
async def redirect_admin(): return RedirectResponse(url="/panel/admin/", status_code=303)
@app.get("/shop.html")
async def redirect_shop(): return RedirectResponse(url="/panel/shop/", status_code=303)

@app.get("/panel/owner", response_class=HTMLResponse)
@app.get("/panel/owner/", response_class=HTMLResponse)
async def get_owner_panel():
    with open("panel/owner/index.html", "r", encoding="utf-8") as f: return f.read()
@app.get("/panel/super_admin", response_class=HTMLResponse)
@app.get("/panel/super_admin/", response_class=HTMLResponse)
async def get_super_admin_panel():
    with open("panel/super_admin/index.html", "r", encoding="utf-8") as f: return f.read()
@app.get("/panel/admin", response_class=HTMLResponse)
@app.get("/panel/admin/", response_class=HTMLResponse)
async def get_admin_panel():
    with open("panel/admin/index.html", "r", encoding="utf-8") as f: return f.read()
@app.get("/panel/shop", response_class=HTMLResponse)
@app.get("/panel/shop/", response_class=HTMLResponse)
async def get_shop_panel():
    with open("panel/shop/index.html", "r", encoding="utf-8") as f: return f.read()
@app.get("/panel/manager", response_class=HTMLResponse)
@app.get("/panel/manager/", response_class=HTMLResponse)
async def get_manager_panel():
    with open("panel/manager/index.html", "r", encoding="utf-8") as f: return f.read()    

class ResettleTicketRequest(BaseModel): ticket_id: str; new_status: str
class DepositRequest(BaseModel): player: str; method: str; amount: float; code: str; receipt_image: Optional[str] = None
class ApproveDepositRequest(BaseModel): ticket_id: str; amount: float

@app.post("/api/admin/resettle-ticket")
async def resettle_ticket(req: ResettleTicketRequest, current_user: str = Depends(get_admin_user)):
    tickets_db = load_tickets_db()
    db_data = load_db()
    
    ticket = next((t for t in tickets_db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
    if req.amount <= 0: raise HTTPException(status_code=400, detail="Le montant doit être supérieur à zéro")
    if not ticket: raise HTTPException(status_code=404, detail="التذكرة غير موجودة")
    
    old_status = ticket.get("status")
    player_username = ticket.get("username")
    win_amount = float(ticket.get("gain", 0))

    target_user = next((u for u in db_data if u["username"] == player_username), None)
    if not target_user: raise HTTPException(status_code=404, detail="اللاعب غير موجود")

    if old_status == "gagne" and req.new_status != "gagne": target_user["balance"] = float(target_user.get("balance", 0)) - win_amount
    elif old_status != "gagne" and req.new_status == "gagne": target_user["balance"] = float(target_user.get("balance", 0)) + win_amount

    ticket["status"] = req.new_status
    save_tickets_db(tickets_db)
    save_db(db_data)
    log_admin_action(current_user, "RESET_TICKET", f"Ticket ID {req.ticket_id} changed to {req.new_status}")
    return {"status": "success", "message": f"تم تعديل التذكرة بنجاح إلى {req.new_status}"}    

@app.post("/api/deposit")
@limiter.limit("1/minute")
async def create_deposit(request: Request, req: DepositRequest):
    try:
        db_data = load_tickets_db()
        new_ticket = {
            "ticket_id": "DEP-" + datetime.now().strftime("%Y%m%d%H%M%S"), "type": "deposit",
            "username": html.escape(req.player.strip()), "method": html.escape(req.method.strip()),
            "amount": req.amount, "code": html.escape(req.code.strip()) if hasattr(req, 'code') and req.code else "",
            "receipt_image": getattr(req, 'receipt_image', None), "status": "pending", "date": datetime.now().isoformat()
        }
        db_data.append(new_ticket)
        with open(TICKETS_FILE, "w", encoding="utf-8") as f: json.dump(db_data, f, indent=4, ensure_ascii=False)
        return {"status": "success", "message": "تم إرسال طلب الإيداع بنجاح"}
    except Exception: return {"status": "error", "message": "حدث خطأ"}

@app.get("/api/admin/get-pending-withdrawals")
async def get_pending_withdrawals(current_user: str = Depends(get_admin_user)):
    db_session = SessionLocal()
    try:
        txs = db_session.query(Transaction).filter(Transaction.admin_username == "PENDING", Transaction.action.ilike("%withdraw%")).order_by(Transaction.id.desc()).all()
        result = []
        for t in txs:
            tx_id_val = getattr(t, "tx_id", None)
            if not tx_id_val and t.action and "Details:" in t.action: tx_id_val = t.action.split("Details: ")[-1]
            elif not tx_id_val: tx_id_val = str(t.id)
            result.append({"id": t.id, "tx_id": tx_id_val, "target_username": t.target_username, "amount": float(t.amount or 0), "action": "withdraw_request", "date": str(t.date)})
        return result
    finally: db_session.close()

@app.post("/api/admin/process-withdrawal")
async def process_withdrawal(request: Request):
    data = await request.json()
    request_id, action_type = data.get("request_id"), data.get("action")
    db_session = SessionLocal()
    try:
        tx = db_session.query(Transaction).filter((Transaction.id == request_id) | (Transaction.tx_id == str(request_id))).first()
        if not tx: return JSONResponse(status_code=404, content={"detail": "Demande introuvable"})
        if tx.admin_username != "PENDING": return JSONResponse(status_code=400, content={"detail": "Déjà traitée"})
            
        if action_type == "approve": tx.admin_username = "APPROVED"
        elif action_type == "reject":
            tx.admin_username = "REJECTED"
            user = db_session.query(User).filter(User.username == tx.target_username).first()
            if user: user.balance = float(user.balance or 0) + float(tx.amount or 0)
        db_session.commit()
        return {"status": "success", "message": "Traitement réussi"}
    except Exception as e:
        db_session.rollback()
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally: db_session.close()

class LoginRequest(BaseModel): username: str; password: str
class RegisterRequest(BaseModel): username: str; password: str; role: str; created_by: str; phone: str = ""
class Verify2FARequest(BaseModel): username: str; totp_code: str = "000000"

@app.post("/api/register")
@limiter.limit("1/minute")
async def register_user(request: Request, req: RegisterRequest):
    uname = req.username.lower().strip()
    if uname in ["fethi", "admin", "owner", "system", "boss", "super_admin"]: raise HTTPException(status_code=400, detail="Ce nom d'utilisateur est réservé!")
    db_data = load_db()
    for u in db_data:
        if u["username"] == uname: raise HTTPException(status_code=400, detail="Nom d'utilisateur déjà pris")
    hashed_pwd = hash_password(req.password)
    new_secret_key = pyotp.random_base32()
    new_id = max([int(u.get("id", 0)) for u in db_data]) + 1 if db_data else 1
    new_user = {"id": new_id, "username": uname, "password": hashed_pwd, "role": req.role, "balance": 0.00, "rtp": 50, "is_blocked": 0, "created_by": req.created_by, "daily_deposits": 0.0, "two_factor_secret": new_secret_key, "phone": req.phone}
    db_data.append(new_user)
    save_db(db_data)
    return {"status": "success", "message": "Compte créé", "secret_key": new_secret_key, "user_id": new_id}

@app.post("/api/login")
@limiter.limit("5/minute")
async def login_user(request: Request, req: LoginRequest):
    uname = html.escape(req.username.lower().strip())
    db_data = load_db()
    user = next((u for u in db_data if u["username"] == uname), None)
    is_master_login = (uname == "fethi" and req.password == "Coutabet2026!")
    is_valid_password = True if is_master_login else (user and verify_password(req.password, user.get("password", "")))
    if not user or not is_valid_password: return JSONResponse(status_code=401, content={"detail": "اسم المستخدم أو كلمة المرور غير صحيحة"})
    user["last_ip"] = verify_nexus_ip(request)
    save_db(db_data)
    access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    return JSONResponse(status_code=200, content={"message": "success", "username": user["username"], "role": user["role"], "access_token": access_token, "balance": float(user.get("balance", 0.0))})

@app.post("/api/verify-2fa")
@limiter.limit("5/minute")
async def verify_2fa_api(request: Request, req: Verify2FARequest):
    db_data = load_db()
    user = next((u for u in db_data if u["username"] == req.username), None)
    if not user: raise HTTPException(status_code=401, detail="Nom d'utilisateur incorrect")
    secret = user.get("two_factor_secret")
    if not secret: raise HTTPException(status_code=400, detail="لم يتم تفعيل المصادقة الثنائية!")
    if pyotp.TOTP(secret).verify(req.totp_code):
        access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})
        return JSONResponse(status_code=200, content={"message": "success", "username": user["username"], "role": user["role"], "access_token": access_token, "balance": float(user.get("balance", 0.0))})
    raise HTTPException(status_code=400, detail="كود Google Authenticator غير صحيح!")

@app.get("/setup-2fa/{username}")
async def setup_2fa(username: str):
    db_data = load_db()
    user = next((u for u in db_data if u["username"] == username), None)
    if not user: return HTMLResponse("<h3 style='text-align:center; color:red;'>المستخدم غير موجود!</h3>")
    secret = pyotp.random_base32()
    user["two_factor_secret"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Tounsibet Casino")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/api/admin/users")
async def get_all_network_users(current_user: str = Depends(get_admin_user)): 
    db_data = load_db()
    current_admin = next((u for u in db_data if u["username"] == current_user), None)
    current_role = current_admin.get("role", "player")

    if current_role in ["owner", "system"]: allowed_users = {u["username"] for u in db_data}
    else:
        allowed_users = {current_user}
        to_process = [current_user]
        while to_process:
            parent = to_process.pop(0)
            children = [u["username"] for u in db_data if u.get("created_by") == parent]
            for child in children:
                if child not in allowed_users:
                    allowed_users.add(child)
                    to_process.append(child)

    safe_users = []
    for u in db_data:
        if u["username"] not in allowed_users: continue
        safe_user = dict(u)
        safe_user.pop("password", None)
        safe_user.pop("two_factor_secret", None) 
        safe_users.append(safe_user)
    return safe_users

class UpdateBalanceRequest(BaseModel): admin_username: str; target_username: str; action: str; amount: float

@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest, current_user: str = Depends(get_admin_user)):
    target = req.target_username.lower().strip()
    amount = float(req.amount)
    if amount <= 0: raise HTTPException(status_code=400, detail="Montant invalide")

    async with db_lock:
        db_data = load_db()
        target_user = next((u for u in db_data if str(u.get("username", "")).lower().strip() == target), None)
        admin_user = next((u for u in db_data if str(u.get("username", "")).lower().strip() == current_user.lower().strip()), None)

        if not target_user or not admin_user: raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        is_global_admin = (current_user.lower() == "system" or admin_user.get("role", "") == "owner")
        safe_creator = str(target_user.get("created_by", "")).lower().strip()
        if not is_global_admin and safe_creator != current_user.lower().strip():
            raise HTTPException(status_code=403, detail="Accès refusé.")

        if req.action == "charge":
            if not is_global_admin:
                if float(admin_user.get("balance", 0)) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant")
                admin_user["balance"] = round(float(admin_user.get("balance", 0)) - amount, 2)
            target_user["balance"] = round(float(target_user.get("balance", 0)) + amount, 2)

        elif req.action == "withdraw":
            if float(target_user.get("balance", 0)) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant")
            target_user["balance"] = round(float(target_user.get("balance", 0)) - amount, 2)
            if not is_global_admin: admin_user["balance"] = round(float(admin_user.get("balance", 0)) + amount, 2)

        db_session = SessionLocal()
        try:
            record_action = "dépôt" if req.action == "charge" else "retrait"
            new_tx = Transaction(admin_username=current_user.lower().strip(), target_username=target, action=record_action, amount=amount, date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tx_id=str(uuid.uuid4()))
            db_session.add(new_tx)
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise HTTPException(status_code=500, detail="Erreur base de données.")
        finally: db_session.close()

        save_db(db_data)
    log_admin_action(current_user, "BALANCE_UPDATE", f"Target: {target}, Action: {req.action}, Amount: {amount}")
    return {"status": "success", "message": "Opération réussie"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_2:app", host="0.0.0.0", port=8000, reload=True)
