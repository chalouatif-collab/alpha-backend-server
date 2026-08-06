from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form, Header, Body, Query
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
import hmac
import hashlib
import urllib.parse
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
import asyncio
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

# ==========================================
# 🎰 إعدادات الكازينو (NexusGGR)
# ==========================================
AGENT_CODE = "Alphabet1"
AGENT_TOKEN = "60f44d247b838f1c7cb99655fc943292"
NEXUS_SECRET_KEY = "6f1dbd5da827207a5302fd4b3fc4c151"
PROVIDER_ENDPOINT = "https://api.nexusggr.eu"

# ==========================================
# ⚽ إعدادات الألعاب الافتراضية (EuroVirtuals) الجديدة
# ==========================================
EURO_APP_KEY = "c5868dec-99e5-42cd-af4b-a1b6e8a3f4e6"
EURO_API_KEY = "g30STgsrspwEieqthZfbfCKhxw==.WWzm63ep2yijXEw1rj2QCt3mOmfZDISUleUifQT9Fd5CQVCOqO"
EURO_BASE_URL = "https://api.staging.betkraft.co.uk"

# ==========================================
# سحب الأسرار لحفظها في متغيرات داخل الكود
# ==========================================
from dotenv import load_dotenv
import os

load_dotenv()
ADMIN_USER = os.getenv("ADMIN_USERNAME")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")

# 1. إعداد الاتصال بـ Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json") 
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://alphabet-7d14c-default-rtdb.firebaseio.com/'
    })

# 2. دالة جلب البيانات من السحابة
def load_db():
    ref = db.reference('/') 
    data = ref.get()
    
    if data is None:
        return {"users": [], "shop_withdrawals": [], "tickets": []}
    
    # 3. كائن سحري يجمع بين خصائص القائمة والقاموس
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

# اسم ملف التخزين الموجود في مشروعك
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
    __tablename__ = "alpha_users"
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
    tx_id = Column(String, nullable=True) # 👈 السطر السحري الذي سيحفظ بيانات D17 و Wafacash

try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN image_path VARCHAR"))
except Exception:
    pass

# 👈 أمر إجباري لتحديث الجداول القديمة
try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN tx_id VARCHAR"))
except Exception:
    pass
Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def hash_password(password: str):
    return pwd_context.hash(password)
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

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
    db = load_db()
    user = next((u for u in db if u["username"] == current_user), None)
    
    if not user or user.get("role") not in ["owner", "super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Access Denied: Admin privileges required")
    
    return current_user

def send_whatsapp_2fa(phone_number: str, username: str, password: str, secret_key: str):
    INSTANCE_ID = "instance185867"
    TOKEN = "76jnhy79la7a5bxx"
    
    message = f"""*مرحباً بك في نظام Alpha Core 🔐*

تم إنشاء حساب الإدارة الخاص بك بنجاح.

👤 *اسم المستخدم:* {username}
🔑 *كلمة المرور:* {password}

🛡️ *خطوات تفعيل الحماية (Google Authenticator):*
1️⃣ افتح تطبيق Google Authenticator.
2️⃣ اختر (إدخال مفتاح الإعداد).
3️⃣ اسم الحساب: AlphaCore - {username}
4️⃣ المفتاح السري:
*{secret_key}*

⚠️ _يرجى حذف هذه الرسالة بعد التفعيل للحفاظ على سرية بياناتك._"""

    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    url = f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat"
    payload = {"token": TOKEN, "to": phone_number, "body": message}
    headers = {'content-type': 'application/x-www-form-urlencoded'}

    try:
        response = httpx.post(url, data=payload, headers=headers)
        if response.status_code == 200:
            print(f"✅ تم إرسال رسالة الواتساب بنجاح إلى: {phone_number}")
        else:
            print(f"❌ خطأ في إرسال الواتساب: {response.text}")
    except Exception as e:
        print(f"❌ حدث خطأ في الاتصال: {e}")

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
        "https://alphabet216.com",
        "https://alpha-player-frontend.onrender.com",
        "https://www.admin-alphabets.com",
        "https://admin-alphabets.com",
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
API_KEY = os.environ.get("API_KEY")

if not API_KEY:
    raise ValueError("⚠️ تنبيه أمني: مفتاح API_KEY مفقود من إعدادات الخادم (Environment Variables)!")

# 🚨 إعدادات الإنذار المبكر (Telegram)
TELEGRAM_TOKEN = "8879806026:AAEB64RCPW4KzsUXUlDeztP_PzjtxkJv_4g"
TELEGRAM_CHAT_ID = "7700782611"

async def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Alert Error: {e}")

ALLOWED_NEXUS_IPS = [
    "127.0.0.1",       
]

def verify_nexus_ip(request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host
    return client_ip

# --- التوجيه الذكي اليدوي لإجبار الروابط القديمة على العمل بالروابط النظيفة ---
@app.get("/owner.html")
async def redirect_owner():
    return RedirectResponse(url="/panel/owner/", status_code=303)

@app.get("/super_admin.html")
async def redirect_super_admin():
    return RedirectResponse(url="/panel/super_admin/", status_code=303)

@app.get("/admin.html")
async def redirect_admin():
    return RedirectResponse(url="/panel/admin/", status_code=303)

@app.get("/shop.html")
async def redirect_shop():
    return RedirectResponse(url="/panel/shop/", status_code=303)

# --- مسارات لوحات الإدارة النظيفة ---
@app.get("/panel/owner", response_class=HTMLResponse)
@app.get("/panel/owner/", response_class=HTMLResponse)
async def get_owner_panel():
    with open("panel/owner/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/panel/super_admin", response_class=HTMLResponse)
@app.get("/panel/super_admin/", response_class=HTMLResponse)
async def get_super_admin_panel():
    with open("panel/super_admin/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/panel/admin", response_class=HTMLResponse)
@app.get("/panel/admin/", response_class=HTMLResponse)
async def get_admin_panel():
    with open("panel/admin/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/panel/shop", response_class=HTMLResponse)
@app.get("/panel/shop/", response_class=HTMLResponse)
async def get_shop_panel():
    with open("panel/shop/index.html", "r", encoding="utf-8") as f:
        return f.read()
    
class ResettleTicketRequest(BaseModel):
    ticket_id: str
    new_status: str

@app.post("/api/admin/resettle-ticket")
async def resettle_ticket(req: ResettleTicketRequest, current_user: str = Depends(get_admin_user)):
    tickets_db = load_tickets_db()
    db = load_db()
    
    ticket = next((t for t in tickets_db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="التذكرة غير موجودة")
    
    old_status = ticket.get("status")
    player_username = ticket.get("username")
    win_amount = float(ticket.get("gain", 0))

    target_user = next((u for u in db if u["username"] == player_username), None)
    if not target_user:
        raise HTTPException(status_code=404, detail="اللاعب غير موجود")

    if old_status == "gagne" and req.new_status != "gagne":
        target_user["balance"] = float(target_user.get("balance", 0)) - win_amount
    elif old_status != "gagne" and req.new_status == "gagne":
        target_user["balance"] = float(target_user.get("balance", 0)) + win_amount

    ticket["status"] = req.new_status
    save_tickets_db(tickets_db)
    save_db(db)
    
    return {"status": "success", "message": f"تم تعديل التذكرة بنجاح إلى {req.new_status}"}    

class DepositRequest(BaseModel):
    player: str
    method: str
    amount: float
    code: str
    receipt_image: Optional[str] = None # 👈 السطر السحري لاستقبال لقطة الشاشة

@app.post("/api/deposit")
async def create_deposit(req: DepositRequest):
    try:
        db = load_tickets_db()
        new_ticket = {
            "ticket_id": "DEP-" + datetime.now().strftime("%Y%m%d%H%M%S"),
            "type": "deposit",
            "username": html.escape(req.player.strip()),
            "method": html.escape(req.method.strip()),
            "amount": req.amount,
            "code": html.escape(req.code.strip()) if hasattr(req, 'code') and req.code else "",
            "receipt_image": getattr(req, 'receipt_image', None),
            "status": "pending",
            "date": datetime.now().isoformat()
        }
        db.append(new_ticket)
        
        alert_msg = f"🚨 <b>عملية إيداع جديدة!</b>\n👤 اللاعب: <code>{new_ticket['username']}</code>\n💰 المبلغ: <b>{new_ticket['amount']}</b>\n💳 الطريقة: {new_ticket['method']}"
        asyncio.create_task(send_telegram_alert(alert_msg))
 
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        return {"status": "success", "message": "تم إرسال طلب الإيداع بنجاح"}
    except Exception as e:
        print(f"Error in create_deposit: {e}")
        return {"status": "error", "message": "حدث خطأ أثناء معالجة الطلب"}

@app.get("/api/admin/get-pending-withdrawals")
async def get_pending_withdrawals():
    """جلب جميع طلبات السحب المعلقة (القديمة والجديدة)"""
    db_session = SessionLocal()
    try:
        # استخدمنا like لكي نصطاد الطلبات حتى لو كان معها تفاصيل مدمجة
        txs = db_session.query(Transaction).filter(
            Transaction.admin_username == "PENDING",
            Transaction.action.like("withdraw_request%")
        ).order_by(Transaction.id.desc()).all()
        
        result = []
        for t in txs:
            # استخراج التفاصيل بأمان تام لعرضها للأونر
            tx_id_val = getattr(t, "tx_id", None)
            if not tx_id_val and t.action and "Details:" in t.action:
                tx_id_val = t.action.split("Details: ")[-1]
            elif not tx_id_val:
                tx_id_val = str(t.id)

            result.append({
                "id": t.id,
                "tx_id": tx_id_val,
                "target_username": t.target_username,
                "amount": float(t.amount or 0),
                "action": "withdraw_request", # إرجاع الاسم النظيف للوحة
                "date": str(t.date)
            })
        return result
    except Exception as e:
        print(f"Error GET withdrawals: {e}")
        return []
    finally:
        db_session.close()

@app.post("/api/admin/process-withdrawal")
async def process_withdrawal(request: Request):
    """معالجة طلب السحب (موافقة أو رفض)"""
    data = await request.json()
    request_id = data.get("request_id")
    action_type = data.get("action")
    
    db_session = SessionLocal()
    try:
        # البحث عن الطلب سواء بـ id أو tx_id
        tx = db_session.query(Transaction).filter(
            (Transaction.id == request_id) | (Transaction.tx_id == str(request_id))
        ).first()
        
        if not tx:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"detail": "Demande introuvable"})
            
        if tx.admin_username != "PENDING":
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"detail": "Cette demande a déjà été traitée"})
            
        if action_type == "approve":
            # تمت الموافقة وإرسال الأموال
            tx.admin_username = "APPROVED"
            
        elif action_type == "reject":
            # تم الرفض، نعيد الرصيد للاعب
            tx.admin_username = "REJECTED"
            
            # (تأكد أن جدول المستخدمين اسمه User في ملفك)
            user = db_session.query(User).filter(User.username == tx.target_username).first()
            if user:
                user.balance = float(user.balance or 0) + float(tx.amount or 0)
                
        db_session.commit()
        return {"status": "success", "message": "Traitement réussi"}
        
    except Exception as e:
        db_session.rollback()
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally:
        db_session.close()

@app.get("/api/admin/get-pending-deposits")
async def get_pending_deposits(current_user: str = Depends(get_admin_user)):
    try:
        db = load_tickets_db()
        pending_deposits = [
            t for t in db 
            if t.get("type") == "deposit" and t.get("status") == "pending"
        ]
        return pending_deposits
    except Exception as e:
        print(f"Error fetching pending deposits: {e}")
        raise HTTPException(status_code=500, detail="خطأ في السيرفر أثناء جلب الطلبات")

class ApproveDepositRequest(BaseModel):
    ticket_id: str
    amount: float

@app.post("/api/admin/approve-deposit")
async def approve_deposit(req: ApproveDepositRequest, current_user: str = Depends(get_admin_user)):
    try:
        db = load_tickets_db()
        ticket = next((t for t in db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
        
        if not ticket:
            raise HTTPException(status_code=404, detail="التذكرة غير موجودة")
            
        if ticket.get("status") != "pending":
            raise HTTPException(status_code=400, detail="هذه التذكرة تمت معالجتها مسبقاً")

        real_amount = req.amount
        ticket["status"] = "approuvé"
        ticket["amount"] = real_amount
        
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        return {"status": "success", "message": f"تمت الموافقة وإضافة {real_amount} بنجاح"}
        
    except Exception as e:
        print(f"Error approving deposit: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي أثناء الموافقة")

@app.get("/api/admin/get-all-tickets")
async def get_all_tickets(current_user: str = Depends(get_admin_user)):
    tickets_db = load_tickets_db()
    if tickets_db is None:
        return []
    return sorted(tickets_db, key=lambda x: x.get('date', ''), reverse=True)

# ==========================================
# الوظائف الخلفية وقاعدة البيانات (Background & DB)
# ==========================================
async def auto_settle_tickets():
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
                ticket["status"] = simulated_result
                changes_made = True
                if simulated_result == "gagne":
                    target_username = ticket["username"]
                    win_amount = float(ticket.get("gain", 0))
                    for u in db:
                        if u["username"] == target_username:
                            u["balance"] = float(u.get("balance", 0)) + win_amount
                            break
            if changes_made:
                save_tickets_db(tickets_db)
                save_db(db)
        except Exception as e:
            print(f"❌ [Auto-Settler] حدث خطأ: {e}")
        await asyncio.sleep(60) 

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(auto_settle_tickets())
    asyncio.create_task(daily_cashback_system()) 

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

async def daily_cashback_system():
    await asyncio.sleep(15)
    while True:
        try:
            now = datetime.now()
            if now.hour == 0 and now.minute < 10:
                print("⏳ [Cashback] جاري فحص وتوزيع الكاش باك اليومي...")
                db = load_db()
                changes_made = False
                
                for u in db:
                    current_balance = float(u.get("balance", 0.0))
                    daily_deps = float(u.get("daily_deposits", 0.0))
                    
                    if daily_deps > 0:
                        if current_balance < 1.0:
                            cashback_amount = daily_deps * 0.10
                            u["balance"] = round(current_balance + cashback_amount, 2)
                        
                        u["daily_deposits"] = 0
                        changes_made = True
                        
                if changes_made:
                    save_db(db)
                    print("✅ [Cashback] تم الانتهاء من التوزيع وتصفير العدادات بنجاح!")
                
                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(300)
        except Exception as e:
            print(f"❌ [Cashback] حدث خطأ: {e}")
            await asyncio.sleep(300)        

# ==========================================
# النماذج (Models)
# ==========================================
class LoginRequest(BaseModel): username: str; password: str
class RegisterRequest(BaseModel): username: str; password: str; role: str; created_by: str; phone: str
class ConfigureAccountRequest(BaseModel): admin_username: str; target_username: str; rtp: int; is_blocked: int
class UpdateBalanceRequest(BaseModel): admin_username: str; target_username: str; action: str; amount: float
class ChangePlayerPasswordRequest(BaseModel): admin_username: str; target_username: str; new_password: str
class SaveTicketRequest(BaseModel): username: str; ticket_data: dict
class UpdateTicketStatusRequest(BaseModel): ticket_id: int; status: str; amount_paid: float
class HandleRequestModel(BaseModel): transaction_id: int; decision: str; admin_username: str
class DeleteAccountRequest(BaseModel): admin_username: str; target_username: str
class ProviderRequest(BaseModel): provider_code: str
class ChangeMyPasswordRequest(BaseModel): username: str; new_password: str
class Verify2FARequest(BaseModel): username: str; totp_code: str

# ==========================================
# مسارات المستخدمين والإدارة (Auth & Admin)
# ==========================================
@app.post("/api/login")
@limiter.limit("5/minute")
async def login_user(request: Request, req: LoginRequest):
    uname = html.escape(req.username.lower().strip())
    db = load_db()
    user = next((u for u in db if u["username"] == uname), None)

    if not user or not verify_password(req.password, user["password"]):
        bad_alert = f"⚠️ <b>محاولة دخول فاشلة للإدارة!</b>\n👤 اسم المستخدم: <code>{req.username}</code>\n❌ السبب: كلمة المرور خاطئة"
        asyncio.create_task(send_telegram_alert(bad_alert))
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect")

    return {"message": "success", "username": user["username"]}
   
@app.post("/api/verify-2fa")
@limiter.limit("5/minute")
async def verify_2fa_api(request: Request, req: Verify2FARequest):
    db = load_db()
    user = next((u for u in db if u["username"] == req.username), None)
    
    if not user:
        bad_alert = f"⚠️ <b>محاولة دخول فاشلة للإدارة!</b>\n👤 اسم المستخدم: <code>{req.username}</code>\n❌ السبب: بيانات غير صحيحة"
        asyncio.create_task(send_telegram_alert(bad_alert))
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect")
        
    secret = user.get("two_factor_secret")
    
    if req.username == "fethi":
        secret = "JBSWY3DPEHPK3PXP"
        
    if not secret:
        raise HTTPException(status_code=400, detail="لم يتم تفعيل المصادقة الثنائية لهذا الحساب!")
        
    totp = pyotp.TOTP(secret)
    if totp.verify(req.totp_code):
        access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})
        good_alert = f"🔐 <b>دخول ناجح للإدارة</b>\n👤 المسؤول: <code>{req.username}</code>\n✅ الحالة: تم تسجيل الدخول بنجاح"
        asyncio.create_task(send_telegram_alert(good_alert))
        
        return {
            "access_token": access_token, 
            "token_type": "bearer", 
            "username": user["username"], 
            "role": user["role"]
        }
    else:
        raise HTTPException(status_code=400, detail="كود Google Authenticator غير صحيح!")

@app.post("/api/register")
async def register_user(req: RegisterRequest):
    uname = req.username.lower().strip()
    db = load_db()
    
    for u in db:
        if u["username"] == uname:
            raise HTTPException(status_code=400, detail="Nom d'utilisateur déjà pris")
            
    hashed_pwd = hash_password(req.password)
    new_secret_key = pyotp.random_base32()
    new_id = max([int(u.get("id", 0)) for u in db]) + 1 if db else 1
    
    new_user = {
        "id": new_id,
        "username": uname, 
        "password": hashed_pwd, 
        "role": req.role, 
        "balance": 0.00,
        "rtp": 50, 
        "is_blocked": 0, 
        "created_by": req.created_by, 
        "last_spin_date": "", 
        "daily_deposits": 0.0,
        "two_factor_secret": new_secret_key,
        "phone": req.phone
    }
    
    db.append(new_user)
    save_db(db)
    
    return {"status": "success", "message": "Compte créé", "secret_key": new_secret_key, "user_id": new_id}

@app.get("/api/admin/fix-user-ids")
async def fix_missing_user_ids():
    try:
        db = load_db()
        current_max_id = 0
        for u in db:
            user_id = u.get("id")
            if user_id is not None and str(user_id).isdigit():
                current_max_id = max(current_max_id, int(user_id))
                
        updated_count = 0
        for u in db:
            if "id" not in u or u.get("id") is None or u.get("id") == "":
                current_max_id += 1
                u["id"] = current_max_id
                updated_count += 1
                
        if updated_count > 0:
            save_db(db)
            
        return {"status": "success", "message": f"عملية ناجحة! تم منح ID جديد لـ {updated_count} حساب/حسابات قديمة."}
    except Exception as e:
        return {"status": "error", "message": f"حدث خطأ: {str(e)}"}

@app.get("/api/admin/users")
async def get_all_network_users(admin_username: Optional[str] = None):
    return load_db()

@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest, current_user: str = Depends(get_admin_user)):
    target, admin, amount = req.target_username.lower().strip(), req.admin_username.lower().strip(), float(req.amount)
    db = load_db()
    target_user = next((u for u in db if u.get("username") == target), None)
    admin_user = next((u for u in db if u.get("username") == admin), None)

    if not target_user: raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if req.action == "charge":
        if admin != "system" and admin_user.get("role") != "owner" and admin != "fethi":
            if not admin_user: raise HTTPException(status_code=404, detail="القائم بالعملية غير موجود")
            if admin_user.get("balance", 0) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant chez l'admin")
            admin_user["balance"] -= amount
        
        target_user["balance"] = target_user.get("balance", 0) + amount
        if admin != "system": target_user["daily_deposits"] = target_user.get("daily_deposits", 0) + amount

    elif req.action == "withdraw":
        if target_user.get("balance", 0) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant")
        target_user["balance"] -= amount
        if admin != "system" and admin_user.get("role") != "owner":
            admin_user["balance"] = admin_user.get("balance", 0) + amount

    db_session = SessionLocal()
    new_tx = Transaction(admin_username=admin, target_username=target, action=req.action, amount=amount, date=datetime.now().strftime("%Y-%m-%d %H:%M"))
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
        txs = db_session.query(Transaction).filter((Transaction.admin_username == uname) | (Transaction.target_username == uname)).order_by(Transaction.id.desc()).all()
    result = [{"id": t.id, "admin_username": t.admin_username, "target_username": t.target_username, "action": t.action, "amount": t.amount, "date": t.date} for t in txs]
    db_session.close()
    return result

@app.post("/api/admin/request-transaction")
async def request_transaction(request: Request):
    db_session = SessionLocal()
    try:
        import uuid
        import os
        import shutil
        from starlette.datastructures import UploadFile

        # 1. استخراج البيانات بذكاء من المتصفح
        form = await request.form()
        target_username = form.get("target_username")
        action = form.get("action")
        amount = float(form.get("amount", 0))
        tx_id = form.get("tx_id", str(uuid.uuid4()))

        # 2. معالجة الصورة (إن وجدت، مثل حالات الإيداع)
        file_path = ""
        file = form.get("file")
        if file and isinstance(file, UploadFile) and file.filename:
            os.makedirs("uploads", exist_ok=True)
            file_name = f"{tx_id}_{os.path.splitext(file.filename)[1]}"
            file_path = os.path.join("uploads", file_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

        # 3. تجهيز بيانات المعاملة
        new_tx_args = {
            "admin_username": "PENDING",
            "target_username": target_username,
            "action": action,
            "amount": amount
        }

        # 🛡️ حماية ذكية: إذا لم تكن خانة tx_id موجودة في قاعدة بياناتك، سنقوم بدمج تفاصيل الدفع مع خانة action كي لا تضيع!
        if hasattr(Transaction, "tx_id"):
            new_tx_args["tx_id"] = tx_id
        else:
            new_tx_args["action"] = f"{action} | Details: {tx_id}"

        # 🛡️ حماية ذكية للصورة
        if file_path:
            if hasattr(Transaction, "image_path"):
                new_tx_args["image_path"] = file_path
            elif hasattr(Transaction, "receipt_image"):
                new_tx_args["receipt_image"] = file_path

        # 4. الحفظ النهائي في قاعدة البيانات
        new_tx = Transaction(**new_tx_args)
        db_session.add(new_tx)
        db_session.commit()

        return {"status": "success", "message": "طلبك قيد المراجعة"}

    except Exception as e:
        db_session.rollback()
        from fastapi.responses import JSONResponse
        # طباعة الخطأ لمعرفته إذا تكرر، وإرجاعه بشكل آمن
        print(f"Transaction Error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally:
        db_session.close()

@app.post("/api/admin/handle-request")
async def handle_pending_request(req: HandleRequestModel):
    db_session = SessionLocal()
    tx = db_session.query(Transaction).filter(Transaction.id == req.transaction_id).first()
    if not tx or tx.admin_username != "PENDING":
        db_session.close()
        raise HTTPException(status_code=404, detail="Demande introuvable ou déjà traitée")

    if req.decision == "reject":
        db_session.delete(tx)
        db_session.commit()
        db_session.close()
        return {"status": "success", "message": "Demande rejetée"}

    db = load_db()
    target_user = next((u for u in db if u["username"] == tx.target_username), None)
    if not target_user:
        db_session.close()
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if tx.action == "deposit_request":
        target_user["balance"] = float(target_user.get("balance", 0)) + tx.amount
        tx.action = "charge"
    elif tx.action == "withdraw_request":
        if target_user.get("balance", 0) < tx.amount:
            db_session.close()
            raise HTTPException(status_code=400, detail="Solde insuffisant pour le retrait")
        target_user["balance"] = float(target_user.get("balance", 0)) - tx.amount
        tx.action = "withdraw"

    tx.admin_username = req.admin_username
    db_session.commit()
    db_session.close()
    save_db(db)
    return {"status": "success", "message": "Demande approuvée avec succès"}

@app.post("/api/admin/change-player-password")
async def change_player_password(req: ChangePlayerPasswordRequest):
    db = load_db()
    for u in db:
        if u["username"] == req.target_username.lower().strip():
            u["password"] = hash_password(req.new_password)
            save_db(db)
            return {"status": "success", "message": "Mot de passe modifié avec succès"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.post("/api/admin/configure-account")
async def configure_account(req: ConfigureAccountRequest):
    db = load_db()
    for u in db:
        if u["username"] == req.target_username.lower().strip():
            u["rtp"] = req.rtp; u["is_blocked"] = req.is_blocked
            save_db(db)
            return {"status": "success", "message": "Configuration enregistrée"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.delete("/api/admin/delete-account")
async def delete_account(req: DeleteAccountRequest):
    db = load_db()
    target = req.target_username.lower().strip()
    new_db = [u for u in db if u.get("username", "").lower().strip() != target]
    if len(new_db) == len(db): raise HTTPException(status_code=404, detail="Non trouvé")
    save_db(new_db)
    return {"status": "success", "message": "Supprimé"}

@app.post("/api/user/change-password")
async def change_my_password(req: ChangeMyPasswordRequest):
    db = load_db()
    for u in db:
        if u["username"] == req.username.lower().strip():
            u["password"] = hash_password(req.new_password)
            save_db(db)
            return {"status": "success", "message": "Mot de passe modifié avec succès"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.get("/api/admin/get-player-tickets")
async def get_player_tickets(username: str, current_user: str = Depends(get_admin_user)):
    tickets_db = load_tickets_db()
    player_tickets = [t for t in tickets_db if t.get("username") == username.lower().strip()]
    return player_tickets

# ==========================================
# دمج مزود الألعاب الحقيقي (NexusGGR API)
# ==========================================
@app.get("/api/get-providers")
async def get_real_providers():
    payload = {
        "method": "provider_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=15)
            return response.json()
        except Exception as e:
            print(f"⚠️ خطأ في جلب المزودين: {e}")
            return {"status": 0, "msg": "Error connecting to provider"}

GAMES_CACHE = {}
CACHE_TIME_LIMIT = 3600  

@app.post("/api/get-providers")
async def get_real_games(request: ProviderRequest):
    provider_code = request.provider_code
    current_time = time.time()
    
    if provider_code in GAMES_CACHE and (current_time - GAMES_CACHE[provider_code]['time']) < CACHE_TIME_LIMIT:
        return GAMES_CACHE[provider_code]['data']

    payload = {
        "method": "game_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "provider_code": provider_code
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=20)
            response_data = response.json()
            
            if response_data.get("status") == 1 or "games" in response_data:
                GAMES_CACHE[provider_code] = {'time': current_time, 'data': response_data}
                
            return response_data
            
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال بالمزود: {e}")
            if provider_code in GAMES_CACHE: 
                return GAMES_CACHE[provider_code]['data']
            return {"status": 0, "msg": "Error connecting to games API"}

@app.get("/api/provider/get-games-paged")
async def get_games_paged(provider: str = "PRAGMATIC", page: int = 1, limit: int = 50):
    current_time = time.time()
    if provider in GAMES_CACHE and (current_time - GAMES_CACHE[provider]['time']) < CACHE_TIME_LIMIT:
        return GAMES_CACHE[provider]['data']

    payload = {
        "method": "game_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "provider_code": provider
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=20)
            response_data = response.json()
            if response_data.get("status") == 1 or "games" in response_data:
                GAMES_CACHE[provider] = {'time': current_time, 'data': response_data}
            return response_data
        except Exception as e:
            return {"status": 0, "msg": "Error"}

@app.post("/api/provider/launch-sportsbook")
async def launch_sportsbook(request: Request):
    try:
        data = await request.json()
        payload = {
            "method": "game_launch",
            "agent_code": AGENT_CODE,
            "agent_token": AGENT_TOKEN,
            "provider_code": str(data.get("provider_code")), 
            "game_code": str(data.get("game_code")),
            "user_code": str(data.get("user_code", "test_user")),
            "lang": "fr",
            "lobby_url": "https://alphabet216.com/"
        }
        
        headers = {"Content-Type": "application/json"}
        endpoint = PROVIDER_ENDPOINT.rstrip('/')
        response = requests.post(endpoint, json=payload, headers=headers)
        
        try:
            response_data = response.json()
        except Exception:
            return {"error": "المزود لم يرْسل رد JSON صالح", "details": response.text}
            
        game_url = response_data.get("url") or response_data.get("launch_url") or (response_data.get("data", {}).get("url"))
        
        if game_url:
            return {"launch_url": game_url}
        else:
            return {"error": "المزود رفض الطلب", "details": response_data}
            
    except Exception as e:
        return {"error": str(e)}
    
@app.post("/api/provider/launch-casino")
async def launch_casino(request: Request):
    try:
        data = await request.json()
        payload = {
            "method": "game_launch",
            "agent_code": AGENT_CODE,      
            "agent_token": AGENT_TOKEN,    
            "user_code": data.get("user_code", "test_user"),
            "provider_code": data.get("provider_code"),
            "game_code": data.get("game_code"),
            "lang": "fr",
            "lobby_url": "https://alphabet216.com/#casino"
        }
        headers = {"Content-Type": "application/json"}
        endpoint = PROVIDER_ENDPOINT.rstrip('/')
        response = requests.post(endpoint, json=payload, headers=headers)
        
        try:
            response_data = response.json()
        except Exception:
            return {"error": "المزود لم يرْسل رد JSON صالح", "details": response.text}
            
        if response.status_code == 200:
            game_url = response_data.get("url") or response_data.get("launch_url") or (response_data.get("data", {}).get("url"))
            if game_url:
                return {"launch_url": game_url}
            else:
                return {"error": "لم يتم العثور على رابط اللعبة", "details": response_data}
        else:
            return {"error": "المزود رفض الطلب", "details": response_data}
            
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# الرياضة الوهمية
# ==========================================
cache = {"last_update": 0, "matches": []}
@app.get("/api/sports/get-live-matches")
async def get_sports():
    current_time = time.time()
    if current_time - cache["last_update"] > 900: 
        leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league"]
        all_matches = []
        for league in leagues:
            try:
                response = requests.get(f"https://api.the-odds-api.eu/v4/sports/{league}/odds?apiKey={API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal", timeout=5) 
                if response.status_code == 200: all_matches.extend(response.json())
            except: pass
        cache["matches"], cache["last_update"] = all_matches, current_time
    return cache["matches"]

# ==========================================
# الجدار الأمني الثاني: حماية لوحة المالك
# ==========================================
@app.get("/owner-login", response_class=HTMLResponse)
async def show_login_page():
    return """
    <html>
        <body style="text-align:center; margin-top:100px; font-family:Arial; background-color:#1e1e2f; color:white;">
            <h2>تسجيل الدخول للإدارة</h2>
            <form action="/owner-login" method="post" style="background:#2a2a40; padding:20px; width:300px; margin:auto; border-radius:10px;">
                <input type="text" name="username" placeholder="اسم المستخدم" required style="width:90%; padding:10px; margin-bottom:15px; border-radius:5px; border:none;"><br>
                <input type="password" name="password" placeholder="كلمة المرور" required style="width:90%; padding:10px; margin-bottom:15px; border-radius:5px; border:none;"><br>
                <button type="submit" style="width:95%; padding:10px; background-color:#4CAF50; color:white; border:none; border-radius:5px; cursor:pointer; font-size:16px;">دخول</button>
            </form>
        </body>
    </html>
    """

@app.post("/owner-login")
@limiter.limit("5/minute")
async def process_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        request.session["is_admin"] = True
        return RedirectResponse(url="/secure-owner", status_code=303)
    return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>بيانات خاطئة!</h3><div style='text-align:center;'><a href='/owner-login'>العودة للمحاولة</a></div>")

@app.get("/secure-owner")
async def open_owner_panel(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/owner-login")
    return FileResponse("owner.html")

@app.get("/owner-logout")
async def logout_owner(request: Request):
    request.session.clear()
    return RedirectResponse(url="/owner-login")

# ==========================================
# نظام التوجيه الذكي والروابط النظيفة للإدارة
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    role = request.session.get("role")
    if role == "owner": return RedirectResponse(url="/panel/owner", status_code=303)
    elif role == "super_admin": return RedirectResponse(url="/panel/super_admin", status_code=303)
    elif role == "admin": return RedirectResponse(url="/panel/admin", status_code=303)
    elif role == "shop": return RedirectResponse(url="/panel/shop", status_code=303)
    
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/login-router")
@limiter.limit("5/minute")
async def process_login_router(request: Request, username: str = Form(...), password: str = Form(...)):
    uname = username.lower().strip()
    db = load_db()
    user = next((u for u in db if u["username"] == uname), None)

    if not user or not verify_password(password, user.get("password", "")):
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>اسم المستخدم أو كلمة المرور غير صحيحة!</h3><div style='text-align:center;'><a href='/' style='color:blue;'>العودة</a></div>")

    if user.get("is_blocked") == 1:
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>هذا الحساب محظور!</h3>")

    role = user.get("role")
    if role in ["owner", "super_admin", "admin"]:
        request.session["pending_user"] = uname
        request.session["pending_role"] = role
        
        html_form = """
        <html dir="rtl">
        <head><title>التحقق الثنائي</title></head>
        <body style="background-color: #1a1a1a; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; font-family: Tahoma, sans-serif;">
            <div style="background-color: #2d2d2d; padding: 40px; border-radius: 10px; text-align: center; border: 1px solid #444;">
                <h2 style="color: #00d2ff;">التحقق الثنائي (2FA) 🔐</h2>
                <p style="color: #ccc;">أدخل الكود من تطبيق Google Authenticator</p>
                <form action="/verify-2fa" method="post">
                    <input type="text" name="totp_code" placeholder="أدخل 6 أرقام" required style="padding: 10px; font-size: 20px; text-align: center; letter-spacing: 5px; border-radius: 5px; border: none; outline: none; margin-bottom: 20px; font-weight: bold;"><br>
                    <button type="submit" style="padding: 10px 30px; background-color: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold;">دخول آمن</button>
                </form>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_form)

    request.session["username"] = user["username"]
    request.session["role"] = user["role"]
    
    if role == "shop": return RedirectResponse(url="/panel/shop", status_code=303)
    else: return HTMLResponse("<h3 style='text-align:center; color:orange;'>ليس لديك صلاحية.</h3>")

@app.post("/verify-2fa")
async def verify_2fa(request: Request, totp_code: str = Form(...)):
    uname = request.session.get("pending_user")
    role = request.session.get("pending_role")
    
    if not uname:
        return HTMLResponse("<h3 style='text-align:center; color:red; margin-top:50px;'>انتهت الجلسة، يرجى تسجيل الدخول مجدداً. <a href='/'>العودة</a></h3>")

    db = load_db()
    user = next((u for u in db if u["username"] == uname), None)
    secret = user.get("two_factor_secret") 
    
    if not secret:
        return HTMLResponse("<h3 style='text-align:center; color:red; margin-top:50px;'>لم يتم إعداد التحقق الثنائي لهذا الحساب بعد!</h3>")

    totp = pyotp.TOTP(secret)
    if totp.verify(totp_code):
        request.session["username"] = uname
        request.session["role"] = role
        request.session.pop("pending_user", None)
        request.session.pop("pending_role", None)
        
        if role == "owner": return RedirectResponse(url="/panel/owner", status_code=303)
        elif role == "super_admin": return RedirectResponse(url="/panel/super-admin", status_code=303)
        elif role == "admin": return RedirectResponse(url="/panel/admin", status_code=303)
    else:
        return HTMLResponse("<h3 style='text-align:center; color:red; margin-top:50px;'>الكود السداسي غير صحيح! <a href='/'>حاول مرة أخرى</a></h3>")

@app.get("/setup-2fa/{username}")
async def setup_2fa(username: str):
    db = load_db()
    user = next((u for u in db if u["username"] == username), None)
    if not user: return HTMLResponse("<h3 style='text-align:center; color:red;'>المستخدم غير موجود!</h3>")
    
    secret = pyotp.random_base32()
    user["two_factor_secret"] = secret
    
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Alpha Casino")
    
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return StreamingResponse(buf, media_type="image/png")

# ==========================================
# دمج نظام BSW Aggregator Callbacks
# ==========================================
SALT_TOKEN = os.getenv("SALT_TOKEN", "NEXUS_SECRET_KEY")

def verify_hash(data: dict, received_hash: str) -> bool:
  filtered_data = {k: v for k, v in data.items() if k != "hash" and v is not None}
  sorted_params = sorted(filtered_data.items())
  query_string = urllib.parse.urlencode(sorted_params)
  string_to_hash = query_string + SALT_TOKEN
  calculated_hash = hashlib.md5(string_to_hash.encode("utf-8")).hexdigest()
  return calculated_hash == received_hash

@app.get("/player_balance")
async def player_balance(
    request: Request = Depends(verify_nexus_ip),
    project_name: str = Query(...),
    provider: str = Query(...),
    user_id: int = Query(...),
    timestamp: int = Query(...),
    hash: str = Query(...)
):
  incoming_data = {"project_name": project_name, "provider": provider, "user_id": user_id, "timestamp": timestamp, "hash": hash}
  if not verify_hash(incoming_data, hash):
    return {"result": {}, "status": 18, "error_message": "Hash does not match"}

  db = load_db()
  target_user = next((u for u in db if str(u.get("id")) == str(user_id) or u.get("username") == str(user_id)), None)
  if not target_user: return {"result": {}, "status": 14, "error_message": "User Does Not exist"}
  if target_user.get("is_blocked", 0) == 1: return {"result": {}, "status": 15, "error_message": "User is banned"}

  return {"result": {"balance": round(float(target_user.get("balance", 0.0)), 2)}, "status": 0, "error_message": "OK"}

@app.post("/change_balance")
async def change_balance(payload: dict = Body(...), request: Request = Depends(verify_nexus_ip)):
  received_hash = payload.get("hash", "")
  if not verify_hash(payload, received_hash): return {"result": {}, "status": 18, "error_message": "Hash does not match"}

  user_id = payload.get("user_id")
  transaction_type = payload.get("transaction_type")
  amount = float(payload.get("amount", 0.0))
  
  db = load_db()
  target_user = next((u for u in db if str(u.get("id")) == str(user_id) or u.get("username") == str(user_id)), None)
  
  if not target_user: return {"result": {}, "status": 14, "error_message": "User Does Not exist"}
  if target_user.get("is_blocked", 0) == 1: return {"result": {}, "status": 15, "error_message": "User is banned"}

  current_balance = float(target_user.get("balance", 0.0))

  if transaction_type == "debit":
    if current_balance < amount: return {"result": {}, "status": 13, "error_message": "Insufficient Balance"}
    new_balance = current_balance - amount
  elif transaction_type in ["credit", "cancel_debit"]:
    new_balance = current_balance + amount
  elif transaction_type in ["cancel_credit"]:
    new_balance = current_balance - amount
  else:
    new_balance = current_balance

  target_user["balance"] = round(new_balance, 2)
  save_db(db)

  return {"result": {"balance": round(new_balance, 2), "txn_id": random.randint(1, 1000000)}, "status": 0, "error_message": "OK"}

@app.post("/change_balance/batch")
async def change_balance_batch(payload: dict = Body(...), request: Request = Depends(verify_nexus_ip)):
  received_hash = payload.get("hash", "")
  if not verify_hash(payload, received_hash): return {"result": {}, "status": 18, "error_message": "Hash does not match"}

  user_id = payload.get("user_id")
  transactions = payload.get("transactions", [])
  db = load_db()
  target_user = next((u for u in db if str(u.get("id")) == str(user_id) or u.get("username") == str(user_id)), None)

  if not target_user: return {"result": {}, "status": 14, "error_message": "User Does Not exist"}
  if target_user.get("is_blocked", 0) == 1: return {"result": {}, "status": 15, "error_message": "User is banned"}

  current_balance = float(target_user.get("balance", 0.0))
  transactions_result = []

  for tx in transactions:
    tx_type = tx.get("transaction_type")
    amount = float(tx.get("amount", 0.0))
    if tx_type == "debit":
      if current_balance < amount: return {"result": {}, "status": 13, "error_message": "Insufficient Balance"}
      current_balance -= amount
    elif tx_type in ["credit", "cancel_debit"]:
      current_balance += amount
    elif tx_type in ["cancel_credit"]:
      current_balance -= amount
    transactions_result.append({"txn_id": random.randint(1, 1000000)})

  target_user["balance"] = round(current_balance, 2)
  save_db(db)
  return {"result": {"balance": round(current_balance, 2), "transactions_result": transactions_result}, "status": 0, "error_message": "OK"} 

class ShopWithdrawRequest(BaseModel): admin_username: str; shop_username: str; amount: float
class HandleShopWithdrawModel(BaseModel): request_id: int; decision: str; shop_username: str
class AdminWithdrawRequest(BaseModel): admin_username: str; amount: float

@app.post("/api/admin/request-shop-withdrawal")
async def request_shop_withdrawal(req: AdminWithdrawRequest):
    try:
        db = load_db()
        admin_username = req.admin_username.lower()
        amount = float(req.amount)

        # 🛠️ السر هنا: تحويل قاعدة البيانات القديمة (List) إلى هيكل جديد (Dictionary)
        if isinstance(db, list):
            db = {"users": db, "shop_withdrawals": []}
            
        users_list = db.get("users", [])

        # البحث عن الأدمين
        admin = next((u for u in users_list if u.get("username") == admin_username), None)
        if not admin: 
            raise HTTPException(status_code=404, detail="Admin non trouvé")

        # البحث عن الشوب
        shop_username = admin.get("created_by")
        shop = next((u for u in users_list if u.get("username") == shop_username and u.get("role") == "shop"), None)
        if not shop:
            shop = next((u for u in users_list if u.get("role") == "shop"), None)
        if not shop: 
            raise HTTPException(status_code=404, detail="Aucun Shop trouvé")

        # التأكد من وجود قسم السحوبات
        if "shop_withdrawals" not in db: 
            db["shop_withdrawals"] = []
        
        from datetime import datetime
        new_req = {
            "id": int(datetime.now().timestamp()),
            "admin_username": admin_username,
            "shop_username": shop.get("username"),
            "amount": amount,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending"
        }
        
        db["shop_withdrawals"].append(new_req)
        save_db(db)
        
        return {"status": "success", "message": "Demande envoyée avec succès"}
        
    except Exception as e:
        # 🛡️ في حالة وجود أي خطأ برمجي آخر، سيتم إرساله ليظهر أمامك في الواجهة
        raise HTTPException(status_code=500, detail=f"Erreur interne: {str(e)}")
    
    
@app.get("/api/shop/pending-withdrawals")
async def get_pending_withdrawals(username: str):
    db = load_db()
    withdrawals = db.get("shop_withdrawals", [])
    return [w for w in withdrawals if w.get("shop_username") == username.lower() and w.get("status") == "pending"]

@app.get("/api/shop/withdraw-requests")
async def get_shop_withdraw_requests(current_user: str = Depends(get_current_user)):
    db = load_db()
    if not isinstance(db, dict): db = {}
    all_requests = db.get("shop_withdrawals")
    if not all_requests: all_requests = []
    elif isinstance(all_requests, dict): all_requests = list(all_requests.values())
    
    shop_username = current_user.lower()
    my_requests = [req for req in all_requests if isinstance(req, dict) and str(req.get("shop_username", "")).lower() == shop_username]
    my_requests.reverse()
    return my_requests

@app.post("/api/shop/handle-withdrawal")
async def handle_shop_withdrawal(req: HandleShopWithdrawModel):
    db = load_db()
    withdrawals = db.get("shop_withdrawals", [])
    target_req = next((w for w in withdrawals if w.get("id") == req.request_id), None)
    
    if not target_req: raise HTTPException(status_code=404, detail="Demande non trouvée")
    
    if req.decision == "accept":
        shop = next((u for u in db if u.get("username") == target_req["shop_username"]), None)
        admin = next((u for u in db if u.get("username") == target_req["admin_username"]), None)
        if not shop or not admin: raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        amount = target_req["amount"]
        if shop.get("balance", 0) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant chez le shop")
        
        shop["balance"] = float(shop.get("balance", 0)) - amount
        admin["balance"] = float(admin.get("balance", 0)) + amount
        target_req["status"] = "accepted"
    else:
        target_req["status"] = "rejected"
        
    save_db(db)
    return {"status": "success", "message": "Traité avec succès"}

@app.get("/api/admin/my-withdrawal-requests")
async def get_my_withdrawal_requests(username: str):
    db = load_db()
    withdrawals = db.get("shop_withdrawals", [])
    return [w for w in withdrawals if w.get("admin_username") == username.lower()]

@app.get("/api/get-server-ip")
async def get_server_ip():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.ipify.org")
            return {"server_ip": response.text}
    except Exception as e:
        return {"error": str(e)}
    
# ==========================================
# محفظة اللاعب (Seamless Wallet - API) الحقيقية
# ==========================================
@app.post("/gold_api")
@app.post("/gold_api/gold_api")
async def seamless_wallet(request: Request):
    try:
        data = await request.json()
        print(f"🚀 [GOLD API REQUEST]: {data}")
        method = data.get("method")
        user_code = data.get("user_code")
        
        db = load_db()
        target_user = next((u for u in db if u.get("username") == user_code), None)
        if not target_user: return {"status": 0, "msg": "USER_NOT_FOUND"}
            
        current_balance = float(target_user.get("balance", 0.0))

        if method == "user_balance":
            return {"status": 1, "user_balance": round(current_balance, 2)}
        elif method == "transaction":
            game_type = data.get("game_type")
            game_data = data.get(game_type, {})
            bet_money = float(game_data.get("bet_money", 0.0))
            win_money = float(game_data.get("win_money", 0.0))
            
            if bet_money > 0 and current_balance < bet_money:
                return {"status": 0, "msg": "INSUFFICIENT_FUNDS"}
            
            new_balance = current_balance - bet_money + win_money
            target_user["balance"] = round(new_balance, 2)
            save_db(db)
            
            return {"status": 1, "user_balance": round(new_balance, 2)}
        else:
            return {"status": 0, "msg": "UNKNOWN_METHOD"}
    except Exception as e:
        print(f"Wallet Error: {e}")
        return {"status": 0, "msg": "INTERNAL_ERROR"}

# ==========================================
# 1. دالة التشفير الأساسية للتشغيل (MD5) حسب وثائقهم
def generate_euro_signature(payload, app_key):
    sorted_keys = sorted(payload.keys())
    hashkey_parts = []
    for key in sorted_keys:
        value = payload[key]
        hashkey_parts.append(f"{key}={value}")
    hashkey = "&".join(hashkey_parts)
    final_string = hashkey + str(app_key)
    final_hash = hashlib.md5(final_string.encode('utf-8')).hexdigest()
    return final_hash

import json
import hashlib
import time

# ==========================================
# 🔐 خوارزمية التشفير الرسمية من EuroVirtuals (Kelvin)
# ==========================================
def sort_nested_array(array):
    if isinstance(array, dict):
        sorted_array = {}
        for key in sorted(array.keys()):
            value = array[key]
            if isinstance(value, (dict, list)):
                sorted_array[key] = sort_nested_array(value)
            else:
                sorted_array[key] = value
        return sorted_array
    elif isinstance(array, list):
        return [sort_nested_array(item) if isinstance(item, (dict, list)) else item for item in array]
    else:
        return array

def hash_create(request_data, token_key):
    hashkey = ''
    if isinstance(request_data, dict):
        request_data = sort_nested_array(request_data)
        for key, value in sorted(request_data.items()):
            if isinstance(value, dict):
                for key_val, val in value.items():
                    hashkey += f"&{key_val}={hashlib.md5(json.dumps(val, sort_keys=True).encode('utf-8')).hexdigest()}"
                continue
            hashkey += f"&{key}={value}"

    hashkey = hashkey.lstrip('&')
    return hashlib.md5((hashkey + token_key).encode('utf-8')).hexdigest()

# ==========================================
# 📡 دالة الهيدر المحدثة
# ==========================================
def get_eurovirtuals_headers(payload=None):
    if payload is None:
        payload = {
            "currency": "TND",
            
        }
    timestamp = str(int(time.time()))
    # 👈 استخدام دالة كلفن (hash_create) مع الـ App Key السري
    signature = hash_create(payload, EURO_APP_KEY)
    
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": EURO_API_KEY,  # 👈 المفتاح الطويل كما طلب
        "x-signature": signature,
        "x-timestamp": timestamp
    }
   

# 3. دالة جلب قائمة الألعاب وعرضها في المنصة
@app.api_route("/api/get-eurovirtuals-games", methods=["GET"])
async def get_virtual_games():
    try:
        payload = {}
        timestamp = str(int(time.time()))
        signature = hash_create(payload, EURO_APP_KEY)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": EURO_API_KEY,
            "x-signature-key": signature,
            "x-timestamp": timestamp
        }
        
        base_url_clean = str(EURO_BASE_URL).rstrip('/')
        games_endpoint = f"{base_url_clean}/v1/games"
        
        try:
            response = requests.get(games_endpoint, headers=headers, timeout=20)
            data = response.json()
        except Exception:
            return {"status": "error", "error": "Invalid JSON response", "details": response.text}
        
        if response.status_code == 200 and data.get("status_code") == 200:
            games_list = data.get("data", {}).get("data", [])
            
            # === التعديل السحري لربط الصور بالواجهة ===
            for game in games_list:
                if "thumbnail" in game:
                    game["image"] = game["thumbnail"]
                    game["img"] = game["thumbnail"]
            # ==========================================
            
            return {"status": "success", "games": games_list}
        else:
            return {"status": "error", "error": data.get("status_description", "Unknown Error"), "full_data": data}

    except Exception as e:
        return {"status": "error", "error": str(e)}
# 4. دالة تشغيل الألعاب
@app.post("/api/provider/launch-eurovirtuals")
async def launch_eurovirtuals(request: Request):
    try:
        data = await request.json()
        game_uuid = data.get("game_uuid", "lobby")
        user_code = str(data.get("user_code", "test_user"))
        timestamp = str(int(time.time()))

        payload = {
            "player_id": user_code,
            "player_name": user_code,
            "player_token": "tok_" + user_code,
            "currency": "TND",
            "demo": 0,
            "game_uuid": game_uuid, # إضافة معرف اللعبة لكي لا يفتح الـ Lobby دائماً
            "callback_url": "https://alpha-backend-server.onrender.com/api/eurovirtuals/callback" 
        
        }

        signature = hash_create(payload, EURO_APP_KEY)

        headers = {
            "x-api-key": EURO_API_KEY,
            "x-signature-key": signature,
            "x-timestamp": timestamp,
            "Content-Type": "application/json"
        }

        base_url_clean = str(EURO_BASE_URL).rstrip('/')
        launch_endpoint = f"{base_url_clean}/v1/launch"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(launch_endpoint, json=payload, headers=headers, timeout=20)
            print("🔍 LAUNCH STATUS:", response.status_code)
            print("🔍 LAUNCH RESPONSE TEXT:", response.text)
            try:
                response_data = response.json()
            except Exception:
                return {"error": "المزود لم يرسل رد JSON صالح", "details": response.text}

            if response_data.get("status_code") == 200:
                game_url = response_data.get("data", {}).get("url")
                if game_url and game_url.startswith("/"):
                    game_url = f"https://staging.betkraft.co.uk{game_url}"
                return {"launch_url": game_url}
            else:
                return {
                    "error": response_data.get("status_description", "المزود رفض الطلب"), 
                    "details": response_data
                }

    except Exception as e:
        return {"error": str(e)}

@app.api_route("/api/eurovirtuals/callback/player_info", methods=["GET", "POST"])
async def eurovirtuals_player_info(request: Request):
    print("🚨🚨🚨 [EUROVIRTUALS] CALLBACK HIT: player_info 🚨🚨🚨")
    try:
        # فخ لالتقاط البيانات سواء أرسلوها كـ POST أو GET
        if request.method == "POST":
            payload = await request.json()
        else:
            payload = dict(request.query_params)
            
        print(f"📦 [PAYLOAD RECEIVED]: {payload}")
        
        # التقاط اسم اللاعب بأي صيغة يرسلونها
        player_id = payload.get("player_id") or payload.get("user_code") or payload.get("player_name")
        print(f"👤 [PLAYER DETECTED]: {player_id}")
        
        db = load_db()
        target_user = next((u for u in db if str(u.get("username")) == str(player_id)), None)
        
        if not target_user or target_user.get("is_blocked") == 1:
            print("❌ [ERROR]: Player not found or blocked!")
            return {"status_code": 500, "status_description": "Player not found or blocked"}
            
        current_balance = float(target_user.get("balance", 0.0))
        print(f"💰 [BALANCE SENT]: {current_balance} TND")
        
        return {
            "status_code": 200,
            "status_description": "Success",
            "data": {
                "balance": round(current_balance, 2),
                "currency": "TND",
                "reference_id": str(uuid.uuid4()), 
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    except Exception as e:
        print(f"❌ [CRITICAL ERROR in Player Info]: {e}")
        return {"status_code": 500, "status_description": "Internal Server Error"}
    
@app.post("/api/eurovirtuals/callback/bet")
async def eurovirtuals_bet(request: Request):
    try:
        payload = await request.json()
        player_id = payload.get("player_id")
        amount = float(payload.get("amount", 0.0))
        transaction_id = payload.get("transaction_id")
        
        db = load_db()
        target_user = next((u for u in db if str(u.get("username")) == str(player_id)), None)
        
        if not target_user or target_user.get("is_blocked") == 1:
            return {"status_code": 500, "status_description": "Player not found or blocked"}
            
        current_balance = float(target_user.get("balance", 0.0))
        
        if current_balance < amount:
            return {"status_code": 500, "status_description": "Insufficient Balance"}
            
        new_balance = current_balance - amount
        target_user["balance"] = round(new_balance, 2)
        save_db(db)
        
        return {
            "status_code": 200,
            "status_description": "Success",
            "data": {
                "balance": round(new_balance, 2),
                "currency": "TND",
                "reference_id": transaction_id, 
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    except Exception as e:
        print(f"EuroVirtuals Bet Error: {e}")
        return {"status_code": 500, "status_description": "Internal Server Error"}
        
@app.post("/api/eurovirtuals/callback/win")
async def eurovirtuals_win(request: Request):
    try:
        payload = await request.json()
        player_id = payload.get("player_id")
        payout_amount = float(payload.get("payout_amount", 0.0))
        transaction_id = payload.get("transaction_id")
        
        db = load_db()
        target_user = next((u for u in db if str(u.get("username")) == str(player_id)), None)
        
        if not target_user or target_user.get("is_blocked") == 1:
            return {"status_code": 500, "status_description": "Player not found or blocked"}
            
        current_balance = float(target_user.get("balance", 0.0))
        new_balance = current_balance + payout_amount
        target_user["balance"] = round(new_balance, 2)
        save_db(db)
        
        return {
            "status_code": 200,
            "status_description": "Success",
            "data": {
                "balance": round(new_balance, 2),
                "currency": "TND",
                "reference_id": transaction_id, 
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
    except Exception as e:
        print(f"EuroVirtuals Win Error: {e}")
        return {"status_code": 500, "status_description": "Internal Server Error"}

def verify_callback_token(app_key: str, timestamp: str, received_token: str) -> bool:
    concatenated = str(app_key) + str(timestamp)
    sha1_hex = hashlib.sha1(concatenated.encode('utf-8')).hexdigest()
    expected_token = hashlib.md5(sha1_hex.encode('utf-8')).hexdigest()
    return expected_token == received_token

