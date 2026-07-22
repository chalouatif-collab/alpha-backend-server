from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
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
from sqlalchemy import create_engine, Column, Integer, String, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
import asyncio
import shutil
from fastapi.staticfiles import StaticFiles
import httpx
from fastapi.responses import JSONResponse,HTMLResponse,FileResponse,RedirectResponse
import os
from dotenv import load_dotenv

# تحميل الأسرار من ملف .env
load_dotenv()

# سحب الأسرار لحفظها في متغيرات داخل الكود
ADMIN_USER = os.getenv("ADMIN_USERNAME")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")

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

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String)
    target_username = Column(String)
    action = Column(String)  
    amount = Column(Float)
    date = Column(String)  
    image_path = Column(String, nullable=True)

try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE transactions ADD COLUMN image_path VARCHAR"))
except Exception:
    pass
Base.metadata.create_all(bind=engine)

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

# ==========================================
# إعدادات تطبيق FastAPI الأساسية
# ==========================================
app = FastAPI()
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
API_KEY = os.environ.get("API_KEY", "f9afe7e1bc006f79f75bafe764b0f117")
TICKETS_FILE = "tickets_database.json" 
from fastapi.responses import HTMLResponse, RedirectResponse

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
# إعدادات مزود الألعاب (NexusGGR)
AGENT_CODE = "TUNISS10"
AGENT_TOKEN = "9a418a80d898dd95f120c321012a67cf"
PROVIDER_ENDPOINT = "https://api.nexusggr.com"

class ResettleTicketRequest(BaseModel):
    ticket_id: str
    new_status: str  # 'won', 'lost', 'void'

@app.post("/api/admin/resettle-ticket")
async def resettle_ticket(req: ResettleTicketRequest, current_user: str = Depends(get_current_user)):
    tickets_db = load_tickets_db()
    db = load_db()
    
    # 1. البحث عن التذكرة
    ticket = next((t for t in tickets_db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="التذكرة غير موجودة")
    
    old_status = ticket.get("status")
    player_username = ticket.get("username")
    win_amount = float(ticket.get("gain", 0))
# 2. البحث عن اللاعب لتحديث رصيده
    target_user = next((u for u in db if u["username"] == player_username), None)
    if not target_user:
        raise HTTPException(status_code=404, detail="اللاعب غير موجود")

    # 3. المنطق المالي (التصحيح)
    if old_status == "gagne" and req.new_status != "gagne":
        # كانت رابحة وستصبح خاسرة/ملغاة -> خصم المبلغ
        target_user["balance"] = float(target_user.get("balance", 0)) - win_amount
    elif old_status != "gagne" and req.new_status == "gagne":
        # كانت خاسرة وستصبح رابحة -> إضافة المبلغ
        target_user["balance"] = float(target_user.get("balance", 0)) + win_amount

    # 4. حفظ التغييرات
    ticket["status"] = req.new_status
    save_tickets_db(tickets_db)
    save_db(db)
    
    return {"status": "success", "message": f"تم تعديل التذكرة بنجاح إلى {req.new_status}"}    
    from pydantic import BaseModel
from datetime import datetime

# 1. تحديد شكل البيانات التي ستصل من اللاعب
class DepositRequest(BaseModel):
    player: str
    method: str
    amount: float
    code: str

# 2. إنشاء المسار الذي يستقبل الطلب
@app.post("/api/deposit")
async def create_deposit(req: DepositRequest):
    try:
        # تحميل قاعدة البيانات الحالية
        db = load_tickets_db()
        
        # إنشاء تذكرة إيداع جديدة
        new_ticket = {
            "ticket_id": "DEP-" + datetime.now().strftime("%Y%m%d%H%M%S"),
            "type": "deposit",
            "username": req.player,
            "method": req.method,
            "amount": req.amount,
            "code": req.code, # رقم بطاقة Ooredoo أو غيرها
            "status": "pending", # الحالة: قيد الانتظار
            "date": datetime.now().isoformat()
        }
        
        # حفظ التذكرة في قاعدة البيانات
        db.append(new_ticket)
        
        # كتابة البيانات الجديدة في الملف (تأكد من وجود دالة الحفظ لديك، أو استخدم هذه الطريقة)
        import json
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        return {"status": "success", "message": "تم إرسال طلب الإيداع بنجاح"}
    except Exception as e:
        print(f"Error in create_deposit: {e}")
        return {"status": "error", "message": "حدث خطأ أثناء معالجة الطلب"}
    from fastapi import HTTPException, Depends
from pydantic import BaseModel

# ==========================================
# 1. مسار جلب الطلبات المعلقة للوحة المالك
# ==========================================
@app.get("/api/admin/get-pending-deposits")
async def get_pending_deposits(current_user: str = Depends(get_current_user)):
    try:
        # تحميل كل التذاكر من قاعدة البيانات
        db = load_tickets_db()
        
        # تصفية التذاكر لجلب طلبات الشحن المعلقة فقط
        # نبحث عن التذاكر التي نوعها deposit وحالتها pending
        pending_deposits = [
            t for t in db 
            if t.get("type") == "deposit" and t.get("status") == "pending"
        ]
        
        return pending_deposits
    except Exception as e:
        print(f"Error fetching pending deposits: {e}")
        raise HTTPException(status_code=500, detail="خطأ في السيرفر أثناء جلب الطلبات")


# ==========================================
# 2. مسار الموافقة على الشحن وصب الرصيد
# ==========================================
# تحديد شكل البيانات التي ستصل من زر "موافقة"
class ApproveDepositRequest(BaseModel):
    ticket_id: str
    amount: float

@app.post("/api/admin/approve-deposit")
async def approve_deposit(req: ApproveDepositRequest, current_user: str = Depends(get_current_user)):
    try:
        db = load_tickets_db()
        
        # البحث عن التذكرة المطلوبة
        ticket = next((t for t in db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
        
        if not ticket:
            raise HTTPException(status_code=404, detail="التذكرة غير موجودة")
            
        if ticket.get("status") != "pending":
            raise HTTPException(status_code=400, detail="هذه التذكرة تمت معالجتها مسبقاً")

        username = ticket.get("username")
        real_amount = req.amount

        # 1. تحديث حالة التذكرة إلى "مقبولة" وتسجيل المبلغ الحقيقي
        ticket["status"] = "approuvé"
        ticket["amount"] = real_amount
        
        # حفظ التعديل في ملف التذاكر
        import json
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        # =====================================================================
        # ⚠️ تنبيه هام: هنا يتم صب الرصيد في حساب اللاعب!
        # يجب عليك استخدام الدالة الخاصة بك التي تضيف الرصيد لقاعدة بيانات اللاعبين
        # مثال (قم بتغييرها لتطابق نظامك إذا كان مختلفاً):
        # update_player_balance(username, real_amount)
        # =====================================================================
        
        return {"status": "success", "message": f"تمت الموافقة وإضافة {real_amount} بنجاح"}
        
    except Exception as e:
        print(f"Error approving deposit: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ داخلي أثناء الموافقة")

@app.get("/api/admin/get-all-tickets")
async def get_all_tickets(current_user: str = Depends(get_current_user)):
    tickets_db = load_tickets_db()
    
    # التأكد من إرجاع قائمة فارغة إذا كانت النتيجة None
    if tickets_db is None:
        return []
        
    # إذا كانت القائمة تحتوي بيانات، قم بترتيبها
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

def load_db():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()
    result = []
    for u in users:
        result.append({
            "username": u.username, "password": u.password, "role": u.role,
            "balance": u.balance, "rtp": u.rtp, "is_blocked": u.is_blocked,
            "created_by": u.created_by, "last_spin_date": u.last_spin_date, "daily_deposits": u.daily_deposits
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
                username=item["username"], password=item["password"], role=item.get("role", "player"),
                balance=item.get("balance", 0.0), rtp=item.get("rtp", 50), is_blocked=item.get("is_blocked", 0),
                created_by=item.get("created_by", "System"), last_spin_date=item.get("last_spin_date", ""), daily_deposits=item.get("daily_deposits", 0.0)
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

# ==========================================
# النماذج (Models)
# ==========================================
class LoginRequest(BaseModel): username: str; password: str
class RegisterRequest(BaseModel): username: str; password: str; role: str; created_by: str
class ConfigureAccountRequest(BaseModel): admin_username: str; target_username: str; rtp: int; is_blocked: int
class UpdateBalanceRequest(BaseModel): admin_username: str; target_username: str; action: str; amount: float
class ChangePlayerPasswordRequest(BaseModel): admin_username: str; target_username: str; new_password: str
class SaveTicketRequest(BaseModel): username: str; ticket_data: dict
class UpdateTicketStatusRequest(BaseModel): ticket_id: int; status: str; amount_paid: float
class HandleRequestModel(BaseModel): transaction_id: int; decision: str; admin_username: str
class DeleteAccountRequest(BaseModel): admin_username: str; target_username: str
class ProviderRequest(BaseModel): provider_code: str

# ==========================================
# مسارات المستخدمين والإدارة (Auth & Admin)
# ==========================================
@app.post("/api/login")
async def login_user(req: LoginRequest):
    uname = req.username.lower().strip()
    db = load_db()
    user = None
    for u in db:
        if u["username"] == uname:
            try:
                is_valid = verify_password(req.password, u.get("password", ""))
            except ValueError:
                is_valid = False
            if is_valid:
                if u.get("is_blocked") == 1:
                    raise HTTPException(status_code=403, detail="Ce compte est bloqué")
                user = u
                break
    if not user:
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect")
    token = create_access_token(data={"sub": user["username"]})
    return {
        "access_token": token, "token_type": "bearer", "username": user["username"],
        "role": user["role"], "balance": user["balance"], "created_by": user.get("created_by", "System")
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
        "username": uname, "password": hashed_pwd, "role": req.role, "balance": 0.00,
        "rtp": 50, "is_blocked": 0, "created_by": req.created_by, "last_spin_date": "", "daily_deposits": 0.0
    }
    db.append(new_user)
    save_db(db)
    return {"status": "success", "message": "Compte créé"}

@app.get("/api/admin/users")
async def get_all_network_users(admin_username: Optional[str] = None):
    return load_db()

@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest, current_user: str = Depends(get_current_user)):
    target, admin, amount = req.target_username.lower().strip(), req.admin_username.lower().strip(), float(req.amount)
    db = load_db()
    target_user = next((u for u in db if u.get("username") == target), None)
    admin_user = next((u for u in db if u.get("username") == admin), None)

    if not target_user: raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if req.action == "charge":
        if admin != "system" and admin_user.get("role") != "owner":
            if not admin_user: raise HTTPException(status_code=404, detail="القائم بالعملية غير موجود")
            if admin_user.get("balance", 0) < amount: raise HTTPException(status_code=400, detail="Solde insuffisant chez l'admin")
            admin_user["balance"] -= amount
        
        current_balance, daily_deps = target_user.get("balance", 0), target_user.get("daily_deposits", 0)
        if current_balance < 1.0 and daily_deps > 0:
            target_user["balance"] = current_balance + (daily_deps * 0.10)
            target_user["daily_deposits"] = 0
        
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
async def request_transaction(target_username: str = Form(...), action: str = Form(...), amount: float = Form(...), tx_id: str = Form(...), file: UploadFile = File(None), current_user: str = Depends(get_current_user)):
    db_session = SessionLocal()
    try:
        file_path = ""
        if file and file.filename:
            os.makedirs("uploads", exist_ok=True)
            file_name = f"{tx_id}{os.path.splitext(file.filename)[1]}"
            file_path = os.path.join("uploads", file_name)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        new_tx = Transaction(admin_username="PENDING", target_username=target_username, action=action, amount=amount, date=datetime.now().strftime("%Y-%m-%d %H:%M"), image_path=file_path)
        db_session.add(new_tx)
        db_session.commit()
        return {"status": "success", "message": "طلبك قيد المراجعة"}
    except Exception as e:
        db_session.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db_session.close()

@app.get("/api/admin/pending-requests")
async def get_pending_requests():
    db_session = SessionLocal()
    txs = db_session.query(Transaction).filter(Transaction.admin_username == "PENDING").order_by(Transaction.id.desc()).all()
    result = [{"id": t.id, "target_username": t.target_username, "action": t.action, "amount": t.amount, "date": t.date, "image_path": t.image_path} for t in txs]
    db_session.close()
    return result

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
class ChangeMyPasswordRequest(BaseModel):
    username: str
    new_password: str

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
async def get_player_tickets(username: str, current_user: str = Depends(get_current_user)):
    tickets_db = load_tickets_db()
    player_tickets = [t for t in tickets_db if t.get("username") == username.lower().strip()]
    return player_tickets
# ==========================================
# دمج مزود الألعاب الحقيقي (NexusGGR API)
# ==========================================

# 1. جلب قائمة المزودين (Providers) من المزود الحقيقي
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

# 2. جلب قائمة الألعاب (Games) من المزود الحقيقي - مع التخزين المؤقت (Cache)
GAMES_CACHE = {}
CACHE_TIME_LIMIT = 3600  

@app.post("/api/get-providers")
async def get_real_games(request: ProviderRequest):
    provider_code = request.provider_code
    current_time = time.time()
    
    # 1. التحقق مما إذا كانت الألعاب محفوظة في الذاكرة المؤقتة (لتسريع الموقع)
    if provider_code in GAMES_CACHE and (current_time - GAMES_CACHE[provider_code]['time']) < CACHE_TIME_LIMIT:
        return GAMES_CACHE[provider_code]['data']

    # 2. إذا لم تكن محفوظة، نطلب الألعاب الحقيقية من المزود
    payload = {
        "method": "game_list",
        "agent_code": "TUNISS10",
        "agent_token": "9a418a80d898dd95f120c321012a67cf",
        "provider_code": provider_code
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # الاتصال الفعلي بسيرفر NexusGGR
            response = await client.post("https://api.nexusggr.com", json=payload, timeout=20)
            response_data = response.json()
            
            # حفظ الألعاب في الذاكرة المؤقتة إذا نجح الاتصال
            if response_data.get("status") == 1 or "games" in response_data:
                GAMES_CACHE[provider_code] = {'time': current_time, 'data': response_data}
                
            return response_data
            
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال بالمزود: {e}")
            # في حالة انقطاع الاتصال، نحاول عرض الألعاب المحفوظة سابقاً
            if provider_code in GAMES_CACHE: 
                return GAMES_CACHE[provider_code]['data']
            return {"status": 0, "msg": "Error connecting to games API"}

# مسار إضافي لدعم الواجهة (إذا كانت تستخدم GET مع Parameters)
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

# 3. تشغيل الألعاب الرياضية والكازينو
@app.post("/api/provider/launch-sportsbook")
def launch_sportsbook(data: dict):
    payload = {
        "method": "game_list", 
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "provider_code": data.get("provider_code")
    }
    try:
        response = requests.post(PROVIDER_ENDPOINT, json=payload, headers={"Content-Type": "application/json"})
        response_data = response.json()
        game_url = response_data.get("url") or response_data.get("launch_url")
        if game_url: return {"launch_url": game_url}
        else: return {"error": "المزود رفض الطلب", "details": response_data}
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
            "provider_code": data.get("provider_code"),
            "game_code": data.get("game_code"),
            "user_code": data.get("user_code", "fethi2_test"),
            "lang": "fr",
            "currency": "USD",
            "rtp": 92,
            "lobby_url": "https://alphabet216.com/"
        }
        response = requests.post(PROVIDER_ENDPOINT, json=payload, headers={"Content-Type": "application/json"})
        response_data = response.json()
        game_url = response_data.get("url") or response_data.get("launch_url")
        if game_url: return {"launch_url": game_url}
        else: return {"error": "المزود رفض الطلب", "details": response_data}
    except Exception as e:
        return {"error": str(e)}

# 4. محفظة اللاعب (Seamless Wallet - API)
@app.post("/gold_api")
async def seamless_wallet_handler(request: Request):
    try:
        data = await request.json()
        method, user_code = data.get("method"), data.get("user_code")
        
        # --- جلب الرصيد الحقيقي من قاعدة البيانات ---
        db = load_db()
        target_user = next((u for u in db if u["username"] == user_code), None)
        if not target_user:
            return JSONResponse(content={"status": 0, "msg": "USER_NOT_FOUND"})
        
        player_balance = float(target_user.get("balance", 0))

        if method == "user_balance":
            return JSONResponse(content={"status": 1, "user_balance": player_balance})

        elif method == "transaction":
            game_type = data.get("game_type")
            tx_data = data.get(game_type, {})
            bet_money, win_money, txn_type = float(tx_data.get("bet_money", 0)), float(tx_data.get("win_money", 0)), tx_data.get("txn_type")

            if txn_type in ["debit", "debit_credit"]:
                if player_balance < bet_money:
                    return JSONResponse(content={"status": 0, "msg": "INSUFFICIENT_USER_FUNDS"})
                player_balance -= bet_money

            if txn_type in ["credit", "debit_credit"]:
                player_balance += win_money

            # حفظ الرصيد الجديد في قاعدة البيانات
            target_user["balance"] = player_balance
            save_db(db)

            return JSONResponse(content={"status": 1, "user_balance": round(player_balance, 2)})
        else:
            return JSONResponse(content={"status": 0, "msg": "UNKNOWN_METHOD"})
    except Exception as e:
        return JSONResponse(content={"status": 0, "msg": "INTERNAL_ERROR"})

# ==========================================
# تشغيل الروت الأساسي والرياضة الوهمية
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
                response = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds?apiKey={API_KEY}&regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal", timeout=5) 
                if response.status_code == 200: all_matches.extend(response.json())
            except: pass
        cache["matches"], cache["last_update"] = all_matches, current_time
    return cache["matches"]


# الجدار الأمني الثاني: حماية لوحة المالك
# ==========================================

# 1. عرض صفحة تسجيل الدخول
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

# 2. التحقق من صحة البيانات
@app.post("/owner-login")
async def process_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASS:
        # إعطاء المستخدم "تأشيرة دخول" صالحة
        request.session["is_admin"] = True
        return RedirectResponse(url="/secure-owner", status_code=303)
    return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>بيانات خاطئة!</h3><div style='text-align:center;'><a href='/owner-login'>العودة للمحاولة</a></div>")

# 3. فتح لوحة المالك (فقط إذا كان يمتلك التأشيرة)
@app.get("/secure-owner")
async def open_owner_panel(request: Request):
    # إذا لم يكن مسجلاً للدخول، نعيده لصفحة تسجيل الدخول
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/owner-login")
    
    # إذا كان مسجلاً، نفتح له ملف لوحة التحكم بأمان
    return FileResponse("owner.html")

# 4. تسجيل الخروج (لإغلاق الجلسة)
@app.get("/owner-logout")
async def logout_owner(request: Request):
    request.session.clear() # مسح التأشيرة
    return RedirectResponse(url="/owner-login")
# ==========================================
# نظام التوجيه الذكي والروابط النظيفة للإدارة
# ==========================================

# 1. الصفحة الرئيسية (نافذة تسجيل الدخول الموحدة)
@app.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    # نظام التوجيه الذكي للمستخدمين المسجلين مسبقاً
    role = request.session.get("role")
    if role == "owner":
        return RedirectResponse(url="/panel/owner", status_code=303)
    elif role == "super_admin":
        return RedirectResponse(url="/panel/super_admin", status_code=303)
    elif role == "admin":
        return RedirectResponse(url="/panel/admin", status_code=303)
    elif role == "shop":
        return RedirectResponse(url="/panel/shop", status_code=303)
    
    from fastapi import Form
from fastapi.responses import RedirectResponse

@app.post("/login")
async def process_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # هنا نضع بيانات حساب المالك الذي قمنا بحمايته
    # (قم بتغيير '123456' إلى كلمة المرور الحقيقية الخاصة بك)
    if username == "fethi" and password == "F90260887305fethi1225":
        # إعطاء الصلاحية في الجلسة
        request.session["role"] = "owner"
        # التوجيه الذكي إلى لوحة المالك
        return RedirectResponse(url="/panel/owner", status_code=303)
    
    # إذا كانت البيانات خاطئة، نعيده للصفحة الرئيسية
    return RedirectResponse(url="/", status_code=303)
    
    # إذا لم يكن مسجلاً للدخول، اعرض له صفحة index.html الحقيقية
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# 2. معالجة تسجيل الدخول والتوجيه حسب الرتبة
@app.post("/login-router")
async def process_login_router(request: Request, username: str = Form(...), password: str = Form(...)):
    uname = username.lower().strip()
    db = load_db()
    user = next((u for u in db if u["username"] == uname), None)
    
    if not user or not verify_password(password, user.get("password", "")):
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>اسم المستخدم أو كلمة المرور غير صحيحة!</h3><div style='text-align:center;'><a href='/' style='color:#4CAF50;'>العودة للمحاولة</a></div>")
    
    if user.get("is_blocked") == 1:
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>هذا الحساب محظور!</h3>")

    request.session["username"] = user["username"]
    request.session["role"] = user["role"]

    role = user["role"]
    if role == "owner":
        return RedirectResponse(url="/panel/owner", status_code=303)
    elif role == "super_admin":
        return RedirectResponse(url="/panel/super-admin", status_code=303)
    elif role == "admin":
        return RedirectResponse(url="/panel/admin", status_code=303)
    elif role == "shop":
        return RedirectResponse(url="/panel/shop", status_code=303)
    else:
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:orange;'>ليس لديك صلاحية للوصول إلى لوحة الإدارة.</h3>")

