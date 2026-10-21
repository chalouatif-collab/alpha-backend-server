import asyncio
import hashlib
import hmac
import html
import io
import json
import os
import random
import shutil
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import firebase_admin
import httpx
import pyotp
import qrcode
from dotenv import load_dotenv
from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from firebase_admin import credentials, db
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import Column, Float, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.sessions import SessionMiddleware

# ==========================================
# 1. ENVIRONMENT & GLOBAL CONFIGURATION
# ==========================================
load_dotenv()

AGENT_CODE = os.getenv("AGENT_CODE", "Alphabet1")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")
NEXUS_SECRET_KEY = os.getenv("NEXUS_SECRET_KEY", "")
PROVIDER_ENDPOINT = os.getenv("PROVIDER_ENDPOINT", "https://api.nexusggr.eu")
GOLD_API_URL = PROVIDER_ENDPOINT

EURO_APP_KEY = os.getenv("EURO_APP_KEY", "")
EURO_API_KEY = os.getenv("EURO_API_KEY", "")
EURO_BASE_URL = os.getenv("EURO_BASE_URL", "https://api.betkraft.co.uk")

ADMIN_USER = os.getenv("ADMIN_USERNAME")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY", "alpha-secure-key-2026")
SALT_TOKEN = os.getenv("SALT_TOKEN", "NEXUS_SECRET_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8879806026:AAEB64RCPW4KzsUXUlDeztP_PzjtxkJv_4g")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7700782611")

DB_FILE = "tickets_database.json"
TICKETS_FILE = "tickets_database.json"
PROMO_FILE = "promo_config.json"

PROCESSED_TRANSACTIONS = set()
GAMES_CACHE = {}
CACHE_TIME_LIMIT = 3600
db_lock = asyncio.Lock()

# ==========================================
# 2. DATABASE INITIALIZATION (FIREBASE & SQL)
# ==========================================
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(
        cred, {"databaseURL": "https://alphabet-7d14c-default-rtdb.firebaseio.com/"}
    )


def load_db():
    ref = db.reference("/")
    data = ref.get()

    if data is None:
        return {"users": [], "shop_withdrawals": [], "tickets": [], "notifications": []}

    users = data.get("users", [])
    if isinstance(users, dict):
        users = list(users.values())

    class MagicDB(list):
        def __init__(self, users_list, full_data):
            super().__init__(users_list)
            self.full_data = full_data
            if "shop_withdrawals" not in self.full_data:
                self.full_data["shop_withdrawals"] = []
            if "notifications" not in self.full_data:
                self.full_data["notifications"] = []

        def get(self, key, default=None):
            return self.full_data.get(key, default)

        def __contains__(self, key):
            return key in self.full_data

        def __setitem__(self, key, value):
            self.full_data[key] = value

    return MagicDB(users, data)


def save_db(data):
    ref = db.reference("/")
    if hasattr(data, "full_data"):
        data.full_data["users"] = list(data)
        ref.set(data.full_data)
    elif isinstance(data, list):
        ref.child("users").set(list(data))
    else:
        ref.set(data)


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
    site_id = Column(String, default="alphabet216", index=True)


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String)
    target_username = Column(String)
    action = Column(String)
    amount = Column(Float)
    date = Column(String)
    image_path = Column(String, nullable=True)
    tx_id = Column(String, nullable=True, index=True)
    site_id = Column(String, default="alphabet216", index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    admin_username = Column(String, index=True)
    action_type = Column(String)
    details = Column(String)
    date = Column(String, default=lambda: str(datetime.now()))


for alter_stmt in [
    "ALTER TABLE transactions ADD COLUMN image_path VARCHAR",
    "ALTER TABLE transactions ADD COLUMN tx_id VARCHAR",
    "ALTER TABLE transactions ADD COLUMN site_id VARCHAR DEFAULT 'alphabet216'",
    "ALTER TABLE alpha_users ADD COLUMN site_id VARCHAR DEFAULT 'alphabet216'",
]:
    try:
        with engine.begin() as conn:
            conn.execute(text(alter_stmt))
    except Exception:
        pass

Base.metadata.create_all(bind=engine)

# ==========================================
# 3. MULTI-TENANT & SECURITY HELPERS
# ==========================================
def get_site_id(request: Request) -> str:
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    url_str = (origin or host).lower()

    if "coutabet" in url_str:
        return "coutabet"
    if "tounsibet" in url_str:
        return "tounsibet"
    if "xdanous" in url_str:
        return "xdanous"
    return "alphabet216"


def get_dynamic_url(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") + "/"
    host = request.headers.get("host", "alphabet216.com")
    protocol = "https" if "localhost" not in host and "127.0.0.1" not in host else "http"
    return f"{protocol}://{host}/"


def get_site_prefix(site_id: str) -> str:
    prefixes = {
        "coutabet": "ct",
        "tounsibet": "tn",
        "xdanous": "xd",
        "alphabet216": "al",
    }
    return prefixes.get(site_id, "al")


def parse_prefixed_user(raw_user_code: str):
    code = str(raw_user_code or "").lower().strip()
    prefix_map = {
        "ct": "coutabet",
        "tn": "tounsibet",
        "xd": "xdanous",
        "al": "alphabet216",
    }
    if "_" in code:
        prefix, actual_user = code.split("_", 1)
        if prefix in prefix_map:
            return actual_user, prefix_map[prefix]
    return code, None


def find_isolated_user(db_list: list, raw_user_code: str):
    actual_user, site_id = parse_prefixed_user(raw_user_code)
    if site_id:
        user = next(
            (
                u
                for u in db_list
                if str(u.get("username", "")).lower().strip() == actual_user
                and u.get("site_id", "alphabet216") == site_id
            ),
            None,
        )
        if user:
            return user, actual_user, site_id

    user = next(
        (u for u in db_list if str(u.get("username", "")).lower().strip() == actual_user),
        None,
    )
    user_site = user.get("site_id", "alphabet216") if user else "alphabet216"
    return user, actual_user, user_site


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str):
    return pwd_context.hash(password)


def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


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
    except Exception:
        raise HTTPException(status_code=401, detail="Jeton d'authentification invalide")


async def get_admin_user(current_user: str = Depends(get_current_user)):
    db_data = load_db()
    user = next((u for u in db_data if u.get("username") == current_user), None)

    if not user or user.get("role") not in ["owner", "manager", "super_admin", "admin", "shop"]:
        raise HTTPException(status_code=403, detail="Accès refusé : privilèges administrateur requis")

    return current_user


def log_admin_action(admin_username: str, action_type: str, details: str):
    db_session = SessionLocal()
    try:
        log_entry = AuditLog(
            admin_username=admin_username,
            action_type=action_type,
            details=details,
            date=str(datetime.now()),
        )
        db_session.add(log_entry)
        db_session.commit()
    except Exception as e:
        print(f"Audit Log Error: {e}")
    finally:
        db_session.close()


def send_whatsapp_2fa(phone_number: str, username: str, password: str, secret_key: str):
    instance_id = "instance185867"
    token = "76jnhy79la7a5bxx"

    message = f"""*Bienvenue sur le système Alpha Core 🔐*

Votre compte administrateur a été créé avec succès.

👤 *Identifiant :* {username}
🔑 *Mot de passe :* {password}

🛡️ *Étapes d'activation de la sécurité (Google Authenticator) :*
1️⃣ Ouvrez l'application Google Authenticator.
2️⃣ Choisissez (Saisir une clé de configuration).
3️⃣ Nom du compte : AlphaCore - {username}
4️⃣ Clé secrète :
*{secret_key}*

⚠️ _Veuillez supprimer ce message après l'activation pour préserver la confidentialité de vos accès._"""

    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    url = f"https://api.ultramsg.com/{instance_id}/messages/chat"
    payload = {"token": token, "to": phone_number, "body": message}
    headers = {"content-type": "application/x-www-form-urlencoded"}

    try:
        response = httpx.post(url, data=payload, headers=headers)
        if response.status_code == 200:
            print(f"✅ Message WhatsApp envoyé avec succès au : {phone_number}")
        else:
            print(f"❌ Erreur d'envoi WhatsApp : {response.text}")
    except Exception as e:
        print(f"❌ Erreur de connexion WhatsApp : {e}")


async def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Alert Error: {e}")


def verify_nexus_ip(request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


# ==========================================
# 4. FASTAPI APP & MIDDLEWARE SETUP
# ==========================================
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://alphabet216.com",
        "https://alpha-player-frontend.onrender.com",
        "https://www.admin-alphabets.com",
        "https://admin-alphabets.com",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://app.alphabet216.com",
        "http://app.alphabet216.com",
        "https://coutabet.com",
        "https://www.coutabet.com",
        "https://tounsibet.com",
        "https://www.tounsibet.com",
        "https://xdanous.net",
        "https://www.xdanous.net",
        "file://",
        "null",
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

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

if os.path.exists("coutabet-player/static"):
    app.mount("/coutabet-static", StaticFiles(directory="coutabet-player/static"), name="coutabet-static")

# ==========================================
# 5. PYDANTIC MODELS
# ==========================================
class SportsLaunchRequest(BaseModel):
    provider_code: str
    game_code: str
    user_code: str


class ResettleTicketRequest(BaseModel):
    ticket_id: str
    new_status: str


class DepositRequest(BaseModel):
    player: str
    method: str
    amount: float
    code: str
    receipt_image: Optional[str] = None


class ApproveDepositRequest(BaseModel):
    ticket_id: str
    amount: float


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "player"
    created_by: str = "self"
    phone: str = ""
    is_self_registered: bool = True
    site_id: Optional[str] = None


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


class HandleRequestModel(BaseModel):
    transaction_id: int
    decision: str
    admin_username: str


class DeleteAccountRequest(BaseModel):
    admin_username: str
    target_username: str


class ProviderRequest(BaseModel):
    provider_code: str


class ChangeMyPasswordRequest(BaseModel):
    username: str
    new_password: str


class Verify2FARequest(BaseModel):
    username: str
    totp_code: str = "000000"


class Reset2FARequest(BaseModel):
    admin_username: str
    target_username: str


class HandleHugeWinRequest(BaseModel):
    transaction_id: Optional[int] = None
    tx_id: Optional[int] = None
    decision: str


class ShopWithdrawRequest(BaseModel):
    admin_username: str
    shop_username: str
    amount: float


class HandleShopWithdrawModel(BaseModel):
    request_id: int
    decision: str
    shop_username: str


class AdminWithdrawRequest(BaseModel):
    admin_username: str
    amount: float


class FreeSpinRequest(BaseModel):
    target_username: str
    provider_code: str
    game_code: str
    spin_count: int
    amount: float
    bet_amount: float = 1.0
    bet_level: int = 1


class CancelTourRequest(BaseModel):
    tour_id: str


class PromoModel(BaseModel):
    is_active: bool
    day_of_week: str
    min_amount: float
    spins: int
    game: str
    provider: str
    max_win: float


class NotificationModel(BaseModel):
    target_user: str
    title: str
    message: str
    icon: str = "fa-bell"


class MarkReadModel(BaseModel):
    notif_id: str


class DeleteNotifModel(BaseModel):
    notif_id: str


# ==========================================
# 6. ROUTING & PANEL VIEWS
# ==========================================
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


@app.get("/panel/manager", response_class=HTMLResponse)
@app.get("/panel/manager/", response_class=HTMLResponse)
async def get_manager_panel():
    with open("panel/manager/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/owner-login", response_class=HTMLResponse)
async def show_login_page():
    return """
    <html>
        <body style="text-align:center; margin-top:100px; font-family:Arial; background-color:#1e1e2f; color:white;">
            <h2>Connexion Administration</h2>
            <form action="/owner-login" method="post" style="background:#2a2a40; padding:20px; width:300px; margin:auto; border-radius:10px;">
                <input type="text" name="username" placeholder="Nom d'utilisateur" required style="width:90%; padding:10px; margin-bottom:15px; border-radius:5px; border:none;"><br>
                <input type="password" name="password" placeholder="Mot de passe" required style="width:90%; padding:10px; margin-bottom:15px; border-radius:5px; border:none;"><br>
                <button type="submit" style="width:95%; padding:10px; background-color:#4CAF50; color:white; border:none; border-radius:5px; cursor:pointer; font-size:16px;">Connexion</button>
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
    return HTMLResponse(
        "<h3 style='text-align:center; margin-top:100px; color:red;'>Identifiants incorrects !</h3>"
        "<div style='text-align:center;'><a href='/owner-login'>Réessayer</a></div>"
    )


@app.get("/secure-owner")
async def open_owner_panel(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/owner-login")
    return FileResponse("owner.html")


@app.get("/owner-logout")
async def logout_owner(request: Request):
    request.session.clear()
    return RedirectResponse(url="/owner-login")


@app.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    role = request.session.get("role")
    if role == "owner":
        return RedirectResponse(url="/panel/owner", status_code=303)
    elif role == "manager":
        return RedirectResponse(url="/panel/manager", status_code=303)
    elif role == "super_admin":
        return RedirectResponse(url="/panel/super_admin", status_code=303)
    elif role == "admin":
        return RedirectResponse(url="/panel/admin", status_code=303)
    elif role == "shop":
        return RedirectResponse(url="/panel/shop", status_code=303)

    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/login-router")
@limiter.limit("5/minute")
async def process_login_router(request: Request, username: str = Form(...), password: str = Form(...)):
    uname = username.lower().strip()

    db_data = load_db()
    user = next((u for u in db_data if u.get("username") == uname), None)

    if not user or not verify_password(password, user.get("password", "")):
        return HTMLResponse(
            "<h3 style='text-align:center; margin-top:100px; color:red;'>Nom d'utilisateur ou mot de passe incorrect !</h3>"
            "<div style='text-align:center;'><a href='/' style='color:blue;'>Retour</a></div>"
        )

    if user.get("is_blocked") == 1:
        return HTMLResponse("<h3 style='text-align:center; margin-top:100px; color:red;'>Ce compte est suspendu !</h3>")

    role = user.get("role")

    if role in ["owner", "super_admin", "admin"]:
        request.session["pending_user"] = uname
        request.session["pending_role"] = role

        html_form = """
        <html dir="ltr">
        <head><title>Authentification 2FA</title></head>
        <body style="background-color: #1a1a1a; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; font-family: Tahoma, sans-serif;">
            <div style="background-color: #2d2d2d; padding: 40px; border-radius: 10px; text-align: center; border: 1px solid #444;">
                <h2 style="color: #00d2ff;">Vérification en deux étapes (2FA) 🔐</h2>
                <p style="color: #ccc;">Entrez le code généré par Google Authenticator</p>
                <form action="/verify-2fa" method="post">
                    <input type="text" name="totp_code" placeholder="Code à 6 chiffres" required style="padding: 10px; font-size: 20px; text-align: center; letter-spacing: 5px; border-radius: 5px; border: none; outline: none; margin-bottom: 20px; font-weight: bold;"><br>
                    <button type="submit" style="padding: 10px 30px; background-color: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold;">Connexion sécurisée</button>
                </form>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_form)

    request.session["username"] = user["username"]
    request.session["role"] = user["role"]

    if role == "shop":
        return RedirectResponse(url="/panel/shop", status_code=303)
    return HTMLResponse("<h3 style='text-align:center; color:orange;'>Accès non autorisé.</h3>")


# ==========================================
# 7. AUTHENTICATION & USER MANAGEMENT
# ==========================================
@app.post("/api/register")
@limiter.limit("10/minute")
async def register_user(request: Request, req: RegisterRequest):
    uname = req.username.lower().strip()

    if uname in ["fethi", "admin", "owner", "system", "boss", "super_admin"]:
        raise HTTPException(status_code=400, detail="Ce nom d'utilisateur est réservé au système !")

    async with db_lock:
        db_data = load_db()

        if getattr(req, "site_id", None) and req.site_id.strip() != "":
            current_site = req.site_id.lower().strip()
        else:
            creator_obj = next(
                (u for u in db_data if u.get("username") == req.created_by.lower().strip()),
                None,
            )
            if creator_obj and creator_obj.get("role") != "owner" and creator_obj.get("site_id"):
                current_site = creator_obj.get("site_id")
            else:
                current_site = get_site_id(request)

        for u in db_data:
            if u.get("username") == uname:
                if (
                    u.get("site_id", "alphabet216") == current_site
                    or req.role in ["owner", "manager"]
                    or u.get("role") in ["owner", "manager"]
                ):
                    raise HTTPException(status_code=400, detail="Nom d'utilisateur déjà pris sur ce site")

        hashed_pwd = hash_password(req.password)
        new_secret_key = pyotp.random_base32()
        new_id = max([int(u.get("id", 0)) for u in db_data]) + 1 if db_data else 1

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
            "phone": req.phone,
            "is_self_registered": req.is_self_registered,
            "site_id": current_site,
        }

        db_data.append(new_user)
        save_db(db_data)

    log_admin_action(req.created_by, "CREATE_USER", f"Created {uname} with role {req.role} on site {current_site}")

    return {
        "status": "success",
        "message": "Compte créé avec succès",
        "secret_key": new_secret_key,
        "user_id": new_id,
        "site": current_site,
    }


@app.post("/api/login")
@limiter.limit("5/minute")
async def login_user(request: Request, req: LoginRequest):
    try:
        uname = html.escape(req.username.lower().strip())
        current_site = get_site_id(request)

        db_data = load_db()
        user = next(
            (
                u
                for u in db_data
                if u.get("username") == uname
                and (
                    u.get("site_id", "alphabet216") == current_site
                    or u.get("role") in ["owner", "manager", "super_admin", "admin", "shop"]
                )
            ),
            None,
        )
        if not user:
            user = next((u for u in db_data if u.get("username") == uname), None)

        if not user or not verify_password(req.password, user.get("password", "")):
            bad_alert = (
                f"⚠️ <b>Tentative de connexion échouée !</b>\n"
                f"👤 Utilisateur : <code>{req.username}</code>\n"
                f"❌ Motif : Mot de passe incorrect"
            )
            asyncio.create_task(send_telegram_alert(bad_alert))
            return JSONResponse(
                status_code=401,
                content={"detail": "Nom d'utilisateur ou mot de passe incorrect"},
            )

        if user.get("is_blocked") == 1:
            return JSONResponse(status_code=403, content={"detail": "Ce compte a été suspendu."})

        user["last_ip"] = verify_nexus_ip(request)
        save_db(db_data)
        access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})

        return JSONResponse(
            status_code=200,
            content={
                "message": "success",
                "username": user["username"],
                "role": user["role"],
                "access_token": access_token,
                "balance": float(user.get("balance", 0.0)),
                "created_by": str(user.get("created_by", "")),
                "is_self_registered": user.get("is_self_registered", False),
                "site_id": user.get("site_id", "alphabet216"),
            },
        )
    except Exception as e:
        print(f"Login Crash: {e}")
        return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {str(e)}"})


@app.post("/api/verify-2fa")
@limiter.limit("5/minute")
async def verify_2fa_api(request: Request, req: Verify2FARequest):
    db_data = load_db()
    user = next((u for u in db_data if u.get("username") == req.username.lower().strip()), None)

    if not user:
        raise HTTPException(status_code=401, detail="Nom d'utilisateur incorrect")

    secret = user.get("two_factor_secret")
    if not secret:
        raise HTTPException(status_code=400, detail="L'authentification à deux facteurs (2FA) n'est pas activée !")

    totp = pyotp.TOTP(secret)
    if totp.verify(req.totp_code):
        access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})

        return JSONResponse(
            status_code=200,
            content={
                "message": "success",
                "username": user["username"],
                "role": user["role"],
                "access_token": access_token,
                "balance": float(user.get("balance", 0.0)),
                "created_by": str(user.get("created_by", "")),
                "is_self_registered": user.get("is_self_registered", False),
                "site_id": user.get("site_id", "alphabet216"),
            },
        )
    raise HTTPException(status_code=400, detail="Code Google Authenticator invalide !")


@app.get("/setup-2fa/{username}")
async def setup_2fa(username: str):
    db_data = load_db()
    user = next((u for u in db_data if u.get("username") == username.lower().strip()), None)
    if not user:
        return HTMLResponse("<h3 style='text-align:center; color:red;'>Utilisateur introuvable !</h3>")

    secret = pyotp.random_base32()
    user["two_factor_secret"] = secret
    save_db(db_data)

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Alpha Casino")

    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")


@app.post("/api/user/change-password")
async def change_my_password(req: ChangeMyPasswordRequest, current_user: str = Depends(get_current_user)):
    target_username = req.username.lower().strip()

    if current_user != target_username and current_user not in [
        "fethi",
        "manager",
        "admin",
        "owner",
        "super_admin",
        "shop",
    ]:
        raise HTTPException(
            status_code=403,
            detail="Non autorisé : vous ne pouvez pas modifier le mot de passe d'un autre utilisateur",
        )

    async with db_lock:
        db_data = load_db()
        for u in db_data:
            if u.get("username") == target_username:
                u["password"] = hash_password(req.new_password)
                save_db(db_data)
                return {"status": "success", "message": "Mot de passe modifié avec succès"}

    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


# ==========================================
# 8. ADMIN & NETWORK OPERATIONS
# ==========================================
@app.get("/api/admin/fix-user-ids")
async def fix_missing_user_ids(current_user: str = Depends(get_admin_user)):
    try:
        async with db_lock:
            db_data = load_db()
            current_max_id = 0
            for u in db_data:
                user_id = u.get("id")
                if user_id is not None and str(user_id).isdigit():
                    current_max_id = max(current_max_id, int(user_id))

            updated_count = 0
            for u in db_data:
                if "id" not in u or u.get("id") is None or u.get("id") == "":
                    current_max_id += 1
                    u["id"] = current_max_id
                    updated_count += 1

            if updated_count > 0:
                save_db(db_data)

        return {
            "status": "success",
            "message": f"Opération réussie ! Nouveaux ID attribués à {updated_count} compte(s).",
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur : {str(e)}"}


@app.get("/api/admin/users")
async def get_all_network_users(current_user: str = Depends(get_admin_user)):
    db_data = load_db()

    current_admin = next((u for u in db_data if u.get("username") == current_user), None)
    current_role = current_admin.get("role", "player") if current_admin else "player"

    if current_role in ["owner", "system"]:
        allowed_users = {u["username"] for u in db_data}
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
        if u.get("username") not in allowed_users:
            continue

        safe_user = dict(u)
        safe_user.pop("password", None)
        if current_role != "owner":
            safe_user.pop("two_factor_secret", None)
        safe_users.append(safe_user)

    return safe_users


@app.post("/api/admin/update-balance")
async def update_balance(req: UpdateBalanceRequest, current_user: str = Depends(get_admin_user)):
    target = req.target_username.lower().strip()
    amount = float(req.amount)

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Montant invalide : doit être supérieur à zéro")

    async with db_lock:
        db_data = load_db()

        target_user = next((u for u in db_data if str(u.get("username", "")).lower().strip() == target), None)
        admin_user = next(
            (u for u in db_data if str(u.get("username", "")).lower().strip() == current_user.lower().strip()),
            None,
        )

        if not target_user:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
        if not admin_user:
            raise HTTPException(status_code=404, detail="Compte administrateur introuvable")

        current_role = admin_user.get("role", "")
        is_global_admin = current_user.lower() == "system" or current_role == "owner"
        admin = current_user.lower().strip()

        safe_creator = str(target_user.get("created_by", "")).lower().strip()
        if not is_global_admin and safe_creator != admin:
            raise HTTPException(status_code=403, detail="Accès refusé. Ce compte ne vous appartient pas.")

        if req.action == "charge":
            if not is_global_admin:
                if float(admin_user.get("balance", 0)) < amount:
                    raise HTTPException(status_code=400, detail="Solde insuffisant sur votre compte")
                admin_user["balance"] = round(float(admin_user.get("balance", 0)) - amount, 2)

            target_user["balance"] = round(float(target_user.get("balance", 0)) + amount, 2)

            if current_user.lower() != "system":
                target_user["daily_deposits"] = float(target_user.get("daily_deposits", 0)) + amount

        elif req.action == "withdraw":
            if float(target_user.get("balance", 0)) < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant chez l'utilisateur")

            target_user["balance"] = round(float(target_user.get("balance", 0)) - amount, 2)
            target_user["daily_deposits"] = max(0.0, float(target_user.get("daily_deposits", 0)) - amount)

            if not is_global_admin:
                admin_user["balance"] = round(float(admin_user.get("balance", 0)) + amount, 2)

        user_site = target_user.get("site_id", "alphabet216")

        db_session = SessionLocal()
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record_action = "charge" if req.action == "charge" else "withdraw"

            new_tx = Transaction(
                admin_username=admin,
                target_username=target,
                action=record_action,
                amount=amount,
                date=current_time,
                tx_id=str(uuid.uuid4()),
                site_id=user_site,
            )
            db_session.add(new_tx)
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            print(f"Error saving transaction history: {e}")
            raise HTTPException(
                status_code=500,
                detail="Erreur base de données. L'opération a été annulée de manière sécurisée.",
            )
        finally:
            db_session.close()

        save_db(db_data)

    log_admin_action(current_user, "BALANCE_UPDATE", f"Target: {target}, Action: {req.action}, Amount: {amount}")

    return {"status": "success", "message": "Opération réussie et enregistrée"}


@app.post("/api/admin/change-player-password")
async def change_player_password(req: ChangePlayerPasswordRequest, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        db_data = load_db()
        for u in db_data:
            if u.get("username") == req.target_username.lower().strip():
                u["password"] = hash_password(req.new_password)
                save_db(db_data)
                log_admin_action(current_user, "CHANGE_PASSWORD", f"Changed password for {req.target_username}")
                return {"status": "success", "message": "Mot de passe modifié avec succès"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@app.post("/api/admin/reset-2fa")
async def reset_2fa(req: Reset2FARequest, current_user: str = Depends(get_admin_user)):
    target = req.target_username.lower().strip()
    async with db_lock:
        db_data = load_db()
        target_user = next((u for u in db_data if str(u.get("username", "")).lower() == target), None)
        if not target_user:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        new_secret = pyotp.random_base32()
        target_user["two_factor_secret"] = new_secret
        save_db(db_data)

    log_admin_action(current_user, "RESET_2FA", f"Reset 2FA for {target}")
    return {"status": "success", "message": "2FA réinitialisé avec succès", "new_secret": new_secret}


@app.post("/api/admin/configure-account")
async def configure_account(req: ConfigureAccountRequest, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        db_data = load_db()
        for u in db_data:
            if u.get("username") == req.target_username.lower().strip():
                u["rtp"] = req.rtp
                u["is_blocked"] = req.is_blocked
                save_db(db_data)
                log_admin_action(
                    current_user,
                    "CONFIG_ACCOUNT",
                    f"Target: {req.target_username}, RTP: {req.rtp}, Blocked: {req.is_blocked}",
                )
                return {"status": "success", "message": "Configuration enregistrée"}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@app.delete("/api/admin/delete-account")
async def delete_account(req: DeleteAccountRequest, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        db_data = load_db()
        target = req.target_username.lower().strip()
        new_db = [u for u in db_data if u.get("username", "").lower().strip() != target]
        if len(new_db) == len(db_data):
            raise HTTPException(status_code=404, detail="Compte introuvable")
        if hasattr(db_data, "full_data"):
            db_data.full_data["users"] = new_db
            save_db(db_data.full_data)
        else:
            save_db(new_db)
    log_admin_action(current_user, "DELETE_ACCOUNT", f"Deleted account {target}")
    return {"status": "success", "message": "Compte supprimé avec succès"}


# ==========================================
# 9. DEPOSITS, WITHDRAWALS & TICKETS
# ==========================================
def load_tickets_db():
    if not os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    try:
        with open(TICKETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_tickets_db(data):
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


@app.post("/api/admin/resettle-ticket")
async def resettle_ticket(req: ResettleTicketRequest, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        tickets_db = load_tickets_db()
        db_data = load_db()

        ticket = next((t for t in tickets_db if str(t.get("ticket_id")) == str(req.ticket_id)), None)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket introuvable")

        old_status = ticket.get("status")
        player_username = ticket.get("username")
        win_amount = float(ticket.get("gain", 0))

        target_user = next((u for u in db_data if u.get("username") == player_username), None)
        if not target_user:
            raise HTTPException(status_code=404, detail="Joueur introuvable")

        if old_status == "gagne" and req.new_status != "gagne":
            target_user["balance"] = round(float(target_user.get("balance", 0)) - win_amount, 2)
        elif old_status != "gagne" and req.new_status == "gagne":
            target_user["balance"] = round(float(target_user.get("balance", 0)) + win_amount, 2)

        ticket["status"] = req.new_status
        save_tickets_db(tickets_db)
        save_db(db_data)

    log_admin_action(current_user, "RESET_TICKET", f"Ticket ID {req.ticket_id} changed to {req.new_status}")
    return {"status": "success", "message": f"Ticket mis à jour avec succès vers le statut : {req.new_status}"}


@app.post("/api/deposit")
@limiter.limit("1/minute")
async def create_deposit(request: Request, req: DepositRequest):
    try:
        current_site = get_site_id(request)
        tickets_db = load_tickets_db()
        new_ticket = {
            "ticket_id": "DEP-" + datetime.now().strftime("%Y%m%d%H%M%S"),
            "type": "deposit",
            "username": html.escape(req.player.strip()),
            "method": html.escape(req.method.strip()),
            "amount": req.amount,
            "code": html.escape(req.code.strip()) if hasattr(req, "code") and req.code else "",
            "receipt_image": getattr(req, "receipt_image", None),
            "status": "pending",
            "date": datetime.now().isoformat(),
            "site_id": current_site,
        }
        tickets_db.append(new_ticket)

        alert_msg = (
            f"🚨 <b>Nouvelle demande de dépôt !</b>\n"
            f"🌐 Site : <b>{current_site.upper()}</b>\n"
            f"👤 Joueur : <code>{new_ticket['username']}</code>\n"
            f"💰 Montant : <b>{new_ticket['amount']} TND</b>\n"
            f"💳 Méthode : {new_ticket['method']}"
        )
        asyncio.create_task(send_telegram_alert(alert_msg))

        save_tickets_db(tickets_db)
        return {"status": "success", "message": "Demande de dépôt envoyée avec succès"}
    except Exception as e:
        print(f"Error in create_deposit: {e}")
        return {"status": "error", "message": "Erreur lors du traitement de la demande"}


@app.get("/api/admin/get-pending-withdrawals")
async def get_pending_withdrawals(current_user: str = Depends(get_admin_user)):
    db_session = SessionLocal()
    try:
        txs = (
            db_session.query(Transaction)
            .filter(Transaction.admin_username == "PENDING", Transaction.action.ilike("%withdraw%"))
            .order_by(Transaction.id.desc())
            .all()
        )
        result = []
        for t in txs:
            tx_id_val = getattr(t, "tx_id", None)
            if not tx_id_val and t.action and "Details:" in t.action:
                tx_id_val = t.action.split("Details: ")[-1]
            elif not tx_id_val:
                tx_id_val = str(t.id)

            result.append(
                {
                    "id": t.id,
                    "tx_id": tx_id_val,
                    "target_username": t.target_username,
                    "amount": float(t.amount or 0),
                    "action": "withdraw_request",
                    "date": str(t.date),
                    "site_id": getattr(t, "site_id", "alphabet216"),
                }
            )
        return result
    except Exception as e:
        print(f"Error GET withdrawals: {e}")
        return []
    finally:
        db_session.close()


@app.post("/api/admin/process-withdrawal")
async def process_withdrawal(request: Request, current_user: str = Depends(get_admin_user)):
    data = await request.json()
    request_id = data.get("request_id")
    action_type = data.get("action")

    db_session = SessionLocal()
    try:
        tx = (
            db_session.query(Transaction)
            .filter((Transaction.id == request_id) | (Transaction.tx_id == str(request_id)))
            .first()
        )

        if not tx:
            return JSONResponse(status_code=404, content={"detail": "Demande introuvable"})

        if tx.admin_username != "PENDING":
            return JSONResponse(status_code=400, content={"detail": "Cette demande a déjà été traitée"})

        if action_type == "approve":
            async with db_lock:
                db_data = load_db()
                target_user = next(
                    (u for u in db_data if str(u.get("username", "")).lower() == str(tx.target_username).lower()),
                    None,
                )
                if not target_user:
                    return JSONResponse(status_code=404, content={"detail": "Joueur introuvable"})
                if float(target_user.get("balance", 0)) < float(tx.amount or 0):
                    return JSONResponse(status_code=400, content={"detail": "Solde insuffisant chez le joueur"})

                target_user["balance"] = round(float(target_user.get("balance", 0)) - float(tx.amount or 0), 2)
                target_user["daily_deposits"] = max(
                    0.0, float(target_user.get("daily_deposits", 0)) - float(tx.amount or 0)
                )
                save_db(db_data)

            tx.admin_username = "APPROVED"
            tx.action = "withdraw"

        elif action_type == "reject":
            tx.admin_username = "REJECTED"

        db_session.commit()
        return {"status": "success", "message": "Traitement réussi"}

    except Exception as e:
        db_session.rollback()
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally:
        db_session.close()


@app.get("/api/admin/get-pending-deposits")
async def get_pending_deposits(current_user: str = Depends(get_admin_user)):
    db_session = SessionLocal()
    try:
        sql_deposits = (
            db_session.query(Transaction)
            .filter(Transaction.admin_username == "PENDING", Transaction.action == "deposit_request")
            .order_by(Transaction.id.desc())
            .all()
        )

        result = []
        for t in sql_deposits:
            tx_parts = str(t.tx_id).split("-") if t.tx_id else ["N/A", "N/A"]
            method_name = tx_parts[0]
            code_val = "-".join(tx_parts[1:]) if len(tx_parts) > 1 else "N/A"

            result.append(
                {
                    "ticket_id": t.id,
                    "username": t.target_username,
                    "method": method_name,
                    "amount": float(t.amount or 0),
                    "code": code_val if code_val != "FILE" else "Image jointe",
                    "receipt_image": t.image_path,
                    "status": "pending",
                    "date": str(t.date),
                    "site_id": getattr(t, "site_id", "alphabet216"),
                }
            )
        return result
    finally:
        db_session.close()


@app.post("/api/admin/approve-deposit")
async def approve_deposit(req: ApproveDepositRequest, current_user: str = Depends(get_admin_user)):
    try:
        async with db_lock:
            tickets_db = load_tickets_db()
            ticket = next((t for t in tickets_db if str(t.get("ticket_id")) == str(req.ticket_id)), None)

            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket introuvable")

            if ticket.get("status") != "pending":
                raise HTTPException(status_code=400, detail="Ce ticket a déjà été traité")

            real_amount = req.amount
            ticket["status"] = "approuvé"
            ticket["amount"] = real_amount

            db_users = load_db()
            target_username = ticket.get("username") or ticket.get("player") or ""
            target_user = next(
                (u for u in db_users if str(u.get("username", "")).lower() == str(target_username).lower()),
                None,
            )

            if target_user:
                target_user["balance"] = round(float(target_user.get("balance", 0)) + real_amount, 2)
                target_user["daily_deposits"] = float(target_user.get("daily_deposits", 0)) + real_amount
                save_db(db_users)

            save_tickets_db(tickets_db)

        promo = load_promo()
        current_day = datetime.now().strftime("%A")

        if (
            promo.get("is_active")
            and real_amount >= promo.get("min_amount", 50)
            and current_day == promo.get("day_of_week")
        ):
            if target_user:
                expiration = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                tour_id = f"auto_fs_{uuid.uuid4().hex[:8]}"
                user_site = target_user.get("site_id", "alphabet216")
                prefixed_user = f"{get_site_prefix(user_site)}_{target_user['username']}"
                fs_payload = {
                    "method": "tour_create",
                    "agent_code": AGENT_CODE,
                    "agent_token": AGENT_TOKEN,
                    "user_code": prefixed_user,
                    "provider_code": promo.get("provider"),
                    "game_code": promo.get("game"),
                    "bet_amount": 1,
                    "spin_count": promo.get("spins"),
                    "amount": promo.get("max_win", 10000),
                    "expiration_time": expiration,
                    "tour_id": tour_id,
                }

                async def send_auto_fs():
                    async with httpx.AsyncClient() as client:
                        try:
                            await client.post(GOLD_API_URL, json=fs_payload, timeout=10.0)
                        except Exception:
                            pass

                asyncio.create_task(send_auto_fs())

        return {"status": "success", "message": f"Dépôt approuvé et {real_amount} TND ajoutés avec succès"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error approving deposit: {e}")
        raise HTTPException(status_code=500, detail="Erreur interne lors de l'approbation")


@app.get("/api/admin/get-all-tickets")
async def get_all_tickets_api(current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    current_admin = next((u for u in db_data if u.get("username") == current_user), None)
    current_role = current_admin.get("role", "player") if current_admin else "player"

    if current_role in ["owner", "system"]:
        allowed_users = {u["username"] for u in db_data}
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

    tickets_db = load_tickets_db()
    if not tickets_db:
        return []

    allowed_tickets = []
    for t in tickets_db:
        t_user = t.get("username") or t.get("user")
        if t_user in allowed_users:
            allowed_tickets.append(t)

    allowed_tickets.reverse()
    return allowed_tickets


@app.get("/api/admin/get-player-tickets")
async def get_player_tickets_api(username: str, current_user: str = Depends(get_admin_user)):
    tickets_db = load_tickets_db()
    if not tickets_db:
        return []
    uname = username.lower().strip()
    user_tickets = [
        t for t in tickets_db if str(t.get("username") or t.get("user") or "").lower().strip() == uname
    ]
    user_tickets.reverse()
    return user_tickets


@app.post("/api/admin/request-transaction")
async def request_transaction(request: Request):
    db_session = SessionLocal()
    try:
        form = await request.form()
        target_username = str(form.get("target_username", "")).lower().strip()
        action = str(form.get("action", ""))
        amount = float(form.get("amount", 0))
        tx_id = str(form.get("tx_id", str(uuid.uuid4())))
        current_site = get_site_id(request)

        if amount <= 0:
            return JSONResponse(status_code=400, content={"detail": "Le montant doit être supérieur à zéro"})

        file_path = ""
        file = form.get("file")
        if file and isinstance(file, StarletteUploadFile) and file.filename:
            allowed_extensions = [".png", ".jpg", ".jpeg", ".webp"]
            allowed_mimes = ["image/png", "image/jpeg", "image/webp"]

            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext not in allowed_extensions or file.content_type not in allowed_mimes:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Format non autorisé. Seules les images sont acceptées."},
                )
            os.makedirs("uploads", exist_ok=True)
            safe_filename = f"{uuid.uuid4().hex}{file_ext}"
            file_path = os.path.join("uploads", safe_filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

        new_tx = Transaction(
            admin_username="PENDING",
            target_username=target_username,
            action=action,
            amount=amount,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tx_id=tx_id,
            image_path=file_path if file_path else None,
            site_id=current_site,
        )
        db_session.add(new_tx)
        db_session.commit()

        return {"status": "success", "message": "Votre demande est en cours de vérification"}

    except Exception as e:
        db_session.rollback()
        print(f"Transaction Error: {str(e)}")
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally:
        db_session.close()


@app.post("/api/admin/handle-request")
async def handle_pending_request(req: HandleRequestModel, current_user: str = Depends(get_admin_user)):
    db_session = SessionLocal()
    try:
        tx = db_session.query(Transaction).filter(Transaction.id == req.transaction_id).first()
        if not tx or tx.admin_username != "PENDING":
            raise HTTPException(status_code=404, detail="Demande introuvable ou déjà traitée")

        if req.decision == "reject":
            db_session.delete(tx)
            db_session.commit()
            return {"status": "success", "message": "Demande rejetée"}

        async with db_lock:
            db_data = load_db()
            target_user = next((u for u in db_data if u.get("username") == tx.target_username), None)

            if not target_user:
                raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

            if tx.action == "deposit_request":
                target_user["balance"] = round(float(target_user.get("balance", 0)) + tx.amount, 2)
                tx.action = "charge"
            elif tx.action == "withdraw_request":
                if float(target_user.get("balance", 0)) < tx.amount:
                    raise HTTPException(status_code=400, detail="Solde insuffisant pour le retrait")
                target_user["balance"] = round(float(target_user.get("balance", 0)) - tx.amount, 2)
                target_user["daily_deposits"] = max(0.0, float(target_user.get("daily_deposits", 0)) - tx.amount)
                tx.action = "withdraw"

            tx.admin_username = req.admin_username
            db_session.commit()
            save_db(db_data)

        return {"status": "success", "message": "Demande approuvée avec succès"}
    finally:
        db_session.close()


# ==========================================
# 10. SHOP WITHDRAWALS
# ==========================================
@app.post("/api/admin/request-shop-withdrawal")
async def request_shop_withdrawal(req: ShopWithdrawRequest):
    try:
        async with db_lock:
            db_data = load_db()
            admin_username = req.admin_username.lower().strip()
            shop_username = req.shop_username.strip().lower()
            amount = float(req.amount)
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Montant invalide")

            shop = next(
                (
                    u
                    for u in db_data
                    if str(u.get("username", "")).strip().lower() == shop_username and u.get("role") == "shop"
                ),
                None,
            )
            if not shop:
                raise HTTPException(status_code=404, detail="Shop non trouvé")

            withdrawals = db_data.get("shop_withdrawals", [])
            new_req = {
                "id": int(datetime.now().timestamp()),
                "admin_username": admin_username,
                "shop_username": shop_username,
                "amount": amount,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "pending",
            }
            withdrawals.append(new_req)
            db_data["shop_withdrawals"] = withdrawals
            save_db(db_data)

        return {"status": "success", "message": "Demande envoyée avec succès"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur interne : {str(e)}")


@app.get("/api/shop/pending-withdrawals")
async def get_shop_pending_withdrawals(username: str, current_user: str = Depends(get_current_user)):
    db_data = load_db()
    withdrawals = db_data.get("shop_withdrawals", [])
    pending_reqs = [
        w
        for w in withdrawals
        if str(w.get("shop_username", "")).lower() == username.lower() and w.get("status") == "pending"
    ]
    pending_reqs.reverse()
    return pending_reqs


@app.get("/api/shop/withdraw-requests")
async def get_shop_withdraw_requests(username: str, current_user: str = Depends(get_current_user)):
    db_data = load_db()
    withdrawals = db_data.get("shop_withdrawals", [])
    target_shop = username.strip().lower()
    all_my_reqs = [w for w in withdrawals if str(w.get("shop_username", "")).strip().lower() == target_shop]
    all_my_reqs.reverse()
    return all_my_reqs


@app.post("/api/admin/handle-shop-withdrawal")
async def handle_shop_withdrawal(req: HandleShopWithdrawModel, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        db_data = load_db()
        withdrawals = db_data.get("shop_withdrawals", [])
        target_req = next((w for w in withdrawals if w.get("id") == req.request_id), None)

        if not target_req:
            raise HTTPException(status_code=404, detail="Demande non trouvée")

        if req.decision == "accept":
            shop = next((u for u in db_data if u.get("username") == target_req["shop_username"]), None)
            admin = next((u for u in db_data if u.get("username") == target_req["admin_username"]), None)
            if not shop or not admin:
                raise HTTPException(status_code=404, detail="Utilisateur introuvable")
            amount = float(target_req["amount"])
            if float(shop.get("balance", 0)) < amount:
                raise HTTPException(status_code=400, detail="Solde insuffisant chez le shop")

            shop["balance"] = round(float(shop.get("balance", 0)) - amount, 2)
            admin["balance"] = round(float(admin.get("balance", 0)) + amount, 2)
            target_req["status"] = "accepted"
        else:
            target_req["status"] = "rejected"

        save_db(db_data)
    return {"status": "success", "message": "Demande traitée avec succès"}


@app.get("/api/admin/my-withdrawal-requests")
async def get_my_withdrawal_requests(username: str):
    db_data = load_db()
    withdrawals = db_data.get("shop_withdrawals", [])
    return [w for w in withdrawals if str(w.get("admin_username", "")).lower() == username.lower()]


# ==========================================
# 11. TRANSACTIONS, AUDIT & ANALYTICS
# ==========================================
@app.get("/api/admin/transactions-history")
async def get_tx_history(username: str = None, current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    current_admin = next((u for u in db_data if u.get("username") == current_user), None)
    current_role = current_admin.get("role", "player") if current_admin else "player"

    if current_role in ["owner", "system"]:
        allowed_users = {u["username"] for u in db_data}
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

    db_session = SessionLocal()
    try:
        txs = db_session.query(Transaction).all()
        result = []
        for t in txs:
            target = getattr(t, "target_username", None)
            admin_uname = getattr(t, "admin_username", None)
            if target in allowed_users or admin_uname in allowed_users:
                result.append(
                    {
                        "id": t.id,
                        "action": t.action,
                        "amount": t.amount,
                        "target_username": target,
                        "date": str(t.date),
                        "image_path": t.image_path,
                        "site_id": getattr(t, "site_id", "alphabet216"),
                    }
                )
        result.reverse()
        return result
    finally:
        db_session.close()


@app.get("/api/user/transactions-history")
async def get_user_transactions(current_user: str = Depends(get_current_user)):
    history = []

    tickets = load_tickets_db()
    user_deposits = [
        t
        for t in tickets
        if t.get("type") == "deposit" and str(t.get("username", "")).lower() == current_user.lower()
    ]

    for d in user_deposits:
        status_map = {"pending": "En attente", "approuvé": "Approuvé", "rejected": "Refusé"}
        raw_status = str(d.get("status", "pending")).lower()
        try:
            dt_obj = datetime.fromisoformat(d.get("date", ""))
            formatted_date = dt_obj.strftime("%Y-%m-%d %H:%M")
        except Exception:
            formatted_date = str(d.get("date", ""))[:16]

        history.append(
            {
                "date": formatted_date,
                "type": "Dépôt",
                "method": str(d.get("method", "N/A")).capitalize(),
                "amount": float(d.get("amount", 0)),
                "status": status_map.get(raw_status, "En attente"),
                "timestamp": d.get("date", ""),
            }
        )

    db_session = SessionLocal()
    try:
        sql_txs = (
            db_session.query(Transaction)
            .filter(Transaction.target_username == current_user.lower())
            .all()
        )

        for w in sql_txs:
            action_lower = str(w.action).lower()

            if action_lower in ["bet", "win", "rollback", "adjustment"]:
                continue

            tx_type = "Retrait"
            if "dépôt" in action_lower or "charge" in action_lower or "deposit" in action_lower:
                tx_type = "Dépôt"

            method = "Virement"
            if "d17" in action_lower or (w.tx_id and "d17" in str(w.tx_id).lower()):
                method = "D17"
            elif "mandat" in action_lower or (w.tx_id and "mandat" in str(w.tx_id).lower()):
                method = "Mandat"
            else:
                method = "Agent/Shop"

            admin_status = str(w.admin_username or "").lower()
            if admin_status == "pending":
                status = "En attente"
            elif admin_status == "rejected":
                status = "Refusé"
            else:
                status = "Approuvé"

            history.append(
                {
                    "date": str(w.date)[:16],
                    "type": tx_type,
                    "method": method,
                    "amount": float(w.amount or 0),
                    "status": status,
                    "timestamp": str(w.date),
                }
            )
    except Exception as e:
        print(f"Error fetching user SQL transactions: {e}")
    finally:
        db_session.close()

    history.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"status": "success", "data": history}


@app.get("/api/user/bet-history")
async def get_user_bet_history(current_user: str = Depends(get_current_user)):
    db_session = SessionLocal()
    try:
        txs = (
            db_session.query(Transaction)
            .filter(
                Transaction.target_username == current_user.lower(),
                Transaction.action.in_(["bet", "win"]),
            )
            .order_by(Transaction.id.desc())
            .limit(50)
            .all()
        )

        history = []
        for t in txs:
            is_win = t.action == "win"
            provider_name = (
                t.admin_username
                if t.admin_username and t.admin_username not in ["EUROVIRTUALS_API", "PENDING_HUGE_WIN"]
                else "Casino"
            )
            if t.admin_username == "EUROVIRTUALS_API":
                provider_name = "Jeux Virtuels"

            history.append(
                {
                    "date": str(t.date)[:16],
                    "game": "Jeu de Casino",
                    "provider": provider_name,
                    "amount": float(t.amount or 0),
                    "type": "win" if is_win else "bet",
                }
            )

        return {"status": "success", "data": history}
    except Exception:
        return {"status": "error", "data": []}
    finally:
        db_session.close()


@app.get("/api/admin/casino-history")
async def get_casino_history(current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    current_admin = next((u for u in db_data if u.get("username") == current_user), None)
    current_role = current_admin.get("role", "player") if current_admin else "player"

    if current_role in ["owner", "system"]:
        allowed_users = {u["username"] for u in db_data}
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

    db_session = SessionLocal()
    try:
        txs = (
            db_session.query(Transaction)
            .filter(Transaction.action.in_(["bet", "win", "rollback"]))
            .all()
        )

        result = []
        for t in txs:
            username = getattr(t, "target_username", None)
            if username in allowed_users:
                result.append(
                    {
                        "id": t.id,
                        "username": username,
                        "action": t.action,
                        "amount": t.amount,
                        "date": str(t.date),
                        "site_id": getattr(t, "site_id", "alphabet216"),
                    }
                )

        result.reverse()
        return result[:500]
    finally:
        db_session.close()


@app.get("/api/admin/audit-logs")
async def get_audit_logs(current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    current_admin = next((u for u in db_data if u.get("username") == current_user), None)
    current_role = current_admin.get("role", "player") if current_admin else "player"

    if current_role in ["owner", "system"]:
        allowed_users = {u["username"] for u in db_data}
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

    db_session = SessionLocal()
    try:
        logs = db_session.query(AuditLog).order_by(AuditLog.id.desc()).all()
        result = []
        for l in logs:
            if l.admin_username in allowed_users:
                result.append(
                    {
                        "id": l.id,
                        "admin_username": l.admin_username,
                        "action_type": l.action_type,
                        "details": l.details,
                        "date": l.date,
                    }
                )
        return result[:300]
    finally:
        db_session.close()


@app.get("/api/admin/fraud-detection")
async def fraud_detection(current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    ip_map = {}
    phone_map = {}

    for u in db_data:
        ip = u.get("last_ip") or u.get("ip")
        phone = u.get("phone")
        uname = u.get("username")

        if ip:
            ip_map.setdefault(ip, []).append(uname)
        if phone and phone != "00000000" and phone != "":
            phone_map.setdefault(phone, []).append(uname)

    suspicious_ips = {ip: users for ip, users in ip_map.items() if len(users) > 1}
    suspicious_phones = {phone: users for phone, users in phone_map.items() if len(users) > 1}

    return {
        "status": "success",
        "shared_ips": suspicious_ips,
        "shared_phones": suspicious_phones,
    }


@app.get("/api/admin/analytics/ggr")
async def get_ggr_analytics(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    site_id: Optional[str] = Query(None),
    current_user: str = Depends(get_admin_user),
):
    db_session = SessionLocal()
    try:
        query = db_session.query(Transaction).filter(Transaction.action.in_(["bet", "win", "rollback"]))
        if site_id and site_id.upper() != "ALL":
            query = query.filter(Transaction.site_id == site_id.lower().strip())

        txs = query.all()

        filtered_txs = []
        for t in txs:
            if not t.date:
                continue
            try:
                date_part = str(t.date).split(" ")[0]
                parts = date_part.split("-")
                if len(parts) >= 2:
                    t_year = int(parts[0])
                    t_month = int(parts[1])

                    if year and t_year != year:
                        continue
                    if month and t_month != month:
                        continue
            except Exception:
                pass
            filtered_txs.append(t)

        total_bets = sum(float(t.amount or 0) for t in filtered_txs if t.action == "bet")
        total_wins = sum(float(t.amount or 0) for t in filtered_txs if t.action == "win")

        ggr = total_bets - total_wins
        actual_rtp = (total_wins / total_bets * 100) if total_bets > 0 else 0.0

        return {
            "status": "success",
            "total_bets": round(total_bets, 2),
            "total_wins": round(total_wins, 2),
            "ggr": round(ggr, 2),
            "actual_rtp": round(actual_rtp, 2),
        }
    except Exception as e:
        print(f"Analytics Error: {e}")
        return {"status": "error", "total_bets": 0, "total_wins": 0, "ggr": 0, "actual_rtp": 0}
    finally:
        db_session.close()


# ==========================================
# 12. HUGE WINS SECURITY WALL (30,000+ TND)
# ==========================================
@app.get("/api/admin/pending-huge-wins")
async def get_pending_huge_wins(current_user: str = Depends(get_admin_user)):
    db_session = SessionLocal()
    try:
        txs = (
            db_session.query(Transaction)
            .filter(Transaction.admin_username == "PENDING_HUGE_WIN")
            .order_by(Transaction.id.desc())
            .all()
        )
        return [
            {
                "id": t.id,
                "target_username": t.target_username,
                "amount": float(t.amount or 0),
                "action": t.action,
                "date": t.date,
                "site_id": getattr(t, "site_id", "alphabet216"),
            }
            for t in txs
        ]
    except Exception as e:
        print(f"Error fetching huge wins: {e}")
        return []
    finally:
        db_session.close()


@app.post("/api/admin/handle-huge-win")
async def handle_huge_win(req: HandleHugeWinRequest, current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    admin = next((u for u in db_data if u.get("username") == current_user), None)
    if not admin or admin.get("role") not in ["owner", "super_admin"]:
        raise HTTPException(status_code=403, detail="Privilèges Owner requis")

    target_tx_id = req.transaction_id if req.transaction_id is not None else req.tx_id
    if target_tx_id is None:
        raise HTTPException(status_code=400, detail="ID de transaction manquant")

    db_session = SessionLocal()
    try:
        tx = db_session.query(Transaction).filter(Transaction.id == target_tx_id).first()
        if not tx or tx.admin_username != "PENDING_HUGE_WIN":
            return JSONResponse(status_code=404, content={"detail": "Demande introuvable ou déjà traitée"})

        if req.decision == "approve":
            async with db_lock:
                fresh_db = load_db()
                target_user = next(
                    (
                        u
                        for u in fresh_db
                        if str(u.get("username", "")).lower() == str(tx.target_username).lower()
                        and u.get("site_id", "alphabet216") == getattr(tx, "site_id", "alphabet216")
                    ),
                    None,
                )
                if not target_user:
                    target_user = next(
                        (u for u in fresh_db if str(u.get("username", "")).lower() == str(tx.target_username).lower()),
                        None,
                    )
                if target_user:
                    target_user["balance"] = round(float(target_user.get("balance", 0)) + float(tx.amount or 0), 2)
                    save_db(fresh_db)
            tx.admin_username = f"APPROVED_BY_{current_user.upper()}"
        else:
            tx.admin_username = f"REJECTED_BY_{current_user.upper()}"

        db_session.commit()
        return {"status": "success", "message": "Gain important traité avec succès"}
    except Exception as e:
        db_session.rollback()
        return JSONResponse(status_code=500, content={"detail": str(e)})
    finally:
        db_session.close()


# ==========================================
# 13. PROMOTIONS, FREE SPINS & CASHBACK
# ==========================================
def load_promo():
    if os.path.exists(PROMO_FILE):
        with open(PROMO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "is_active": False,
        "day_of_week": "Friday",
        "min_amount": 50,
        "spins": 10,
        "game": "vs20doghouse",
        "provider": "PRAGMATIC",
        "max_win": 10000,
    }


@app.get("/api/admin/promo")
async def get_promo(current_user: str = Depends(get_admin_user)):
    return load_promo()


@app.post("/api/admin/promo")
async def set_promo(data: PromoModel, current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    admin = next((u for u in db_data if u.get("username") == current_user), None)
    if not admin or admin.get("role") not in ["owner", "super_admin"]:
        raise HTTPException(status_code=403, detail="Accès refusé")

    with open(PROMO_FILE, "w", encoding="utf-8") as f:
        json.dump(data.dict(), f)
    return {"status": "success", "message": "Configuration de la promotion enregistrée avec succès"}


@app.post("/api/admin/grant-freespins")
async def grant_free_spins(req: FreeSpinRequest, current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    admin = next((u for u in db_data if u.get("username") == current_user), None)
    if not admin or admin.get("role") not in ["owner", "super_admin"]:
        raise HTTPException(status_code=403, detail="Privilèges Owner requis")

    target_user = next(
        (u for u in db_data if str(u.get("username", "")).lower().strip() == req.target_username.lower().strip()),
        None,
    )
    user_site = target_user.get("site_id", "alphabet216") if target_user else "alphabet216"
    prefixed_user = f"{get_site_prefix(user_site)}_{req.target_username.lower().strip()}"

    expiration = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tour_id = f"fs_{uuid.uuid4().hex[:10]}"

    payload = {
        "method": "tour_create",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "user_code": prefixed_user,
        "provider_code": req.provider_code,
        "game_code": req.game_code,
        "bet_amount": req.bet_amount,
        "spin_count": req.spin_count,
        "amount": req.amount,
        "expiration_time": expiration,
        "tour_id": tour_id,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(GOLD_API_URL, json=payload, timeout=15.0)
            res_data = response.json()

            if res_data.get("status") == 1:
                real_win = res_data.get("real_win", 0)
                return {
                    "status": "success",
                    "message": f"{req.spin_count} tours gratuits envoyés avec succès !",
                    "real_win": real_win,
                }
            error_msg = res_data.get("detail") or res_data.get("msg") or "Demande refusée par le fournisseur"
            return JSONResponse(status_code=400, content={"detail": f"Erreur fournisseur : {error_msg}"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": f"Erreur de connexion : {str(e)}"})


@app.post("/api/admin/cancel-freespins")
async def cancel_free_spins(req: CancelTourRequest, current_user: str = Depends(get_admin_user)):
    db_data = load_db()
    admin = next((u for u in db_data if u.get("username") == current_user), None)
    if not admin or admin.get("role") not in ["owner", "super_admin"]:
        raise HTTPException(status_code=403, detail="Privilèges Owner requis")

    payload = {
        "method": "tour_cancel",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "tour_id": req.tour_id,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(GOLD_API_URL, json=payload, timeout=15.0)
            res_data = response.json()

            if res_data.get("status") == 1:
                refund = res_data.get("canceled_money", 0)
                rest = res_data.get("rest_count", 0)
                return {
                    "status": "success",
                    "message": f"Tour annulé ! Remboursement : {refund} TND ({rest} tours restants non joués).",
                }
            error_msg = res_data.get("detail") or res_data.get("msg") or "Demande refusée par le fournisseur"
            return JSONResponse(status_code=400, content={"detail": f"Erreur fournisseur : {error_msg}"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": f"Erreur de connexion : {str(e)}"})


@app.post("/api/user/claim-cashback")
async def claim_vault_cashback(current_user: str = Depends(get_current_user)):
    async with db_lock:
        db_data = load_db()
        target_user = next(
            (u for u in db_data if str(u.get("username", "")).lower().strip() == current_user.lower().strip()),
            None,
        )

        if not target_user:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

        vault_amount = float(target_user.get("available_cashback", 0.0))

        if vault_amount < 10.0:
            raise HTTPException(status_code=400, detail="Le montant minimum pour récupérer est de 10 TND.")

        now = datetime.now()
        last_claim_str = target_user.get("last_cashback_claim", "")
        if last_claim_str:
            try:
                last_claim_date = datetime.strptime(last_claim_str, "%Y-%m-%d %H:%M:%S")
                time_diff = now - last_claim_date
                if time_diff < timedelta(hours=24):
                    remaining = timedelta(hours=24) - time_diff
                    hours, remainder = divmod(remaining.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Veuillez patienter encore {hours}h {minutes}m avant le prochain retrait.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

        target_user["balance"] = round(float(target_user.get("balance", 0.0)) + vault_amount, 2)
        target_user["available_cashback"] = 0.0
        target_user["last_cashback_claim"] = now.strftime("%Y-%m-%d %H:%M:%S")
        user_site = target_user.get("site_id", "alphabet216")

        save_db(db_data)

    db_session = SessionLocal()
    try:
        new_tx = Transaction(
            admin_username="SYSTEM_CASHBACK",
            target_username=target_user["username"],
            action="claim_vault",
            amount=vault_amount,
            date=now.strftime("%Y-%m-%d %H:%M:%S"),
            tx_id=f"vault_{int(time.time())}",
            site_id=user_site,
        )
        db_session.add(new_tx)
        db_session.commit()
    except Exception:
        db_session.rollback()
    finally:
        db_session.close()

    return {
        "status": "success",
        "message": f"Vous avez récupéré {vault_amount} TND avec succès !",
        "new_balance": target_user["balance"],
    }


# ==========================================
# 14. NOTIFICATIONS SYSTEM
# ==========================================
@app.post("/api/admin/send-notification")
async def send_notification(req: NotificationModel, current_user: str = Depends(get_admin_user)):
    async with db_lock:
        db_data = load_db()
        if "notifications" not in db_data.full_data:
            db_data.full_data["notifications"] = []

        new_notif = {
            "id": str(uuid.uuid4())[:8],
            "target": req.target_user.lower().strip(),
            "title": req.title,
            "message": req.message,
            "icon": req.icon,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "read_by": [],
        }
        db_data.full_data["notifications"].append(new_notif)
        save_db(db_data)
    return {"status": "success", "message": "Notification envoyée avec succès"}


@app.get("/api/user/notifications")
async def get_user_notifications(current_user: str = Depends(get_current_user)):
    db_data = load_db()
    notifs = db_data.full_data.get("notifications", [])
    user_notifs = []
    unread_count = 0

    for n in notifs:
        if current_user.lower() in n.get("deleted_by", []):
            continue

        if n.get("target") == "all" or n.get("target") == current_user.lower():
            is_read = current_user.lower() in n.get("read_by", [])
            if not is_read:
                unread_count += 1
            user_notifs.append({**n, "is_read": is_read})

    return {"unread": unread_count, "notifications": user_notifs[::-1][:15]}


@app.post("/api/user/read-notification")
async def mark_notif_read(req: MarkReadModel, current_user: str = Depends(get_current_user)):
    async with db_lock:
        db_data = load_db()
        notifs = db_data.full_data.get("notifications", [])
        for n in notifs:
            if n.get("id") == req.notif_id:
                if current_user.lower() not in n.get("read_by", []):
                    n.setdefault("read_by", []).append(current_user.lower())
                break
        save_db(db_data)
    return {"status": "success"}


@app.post("/api/user/delete-notification")
async def delete_notification(req: DeleteNotifModel, current_user: str = Depends(get_current_user)):
    async with db_lock:
        db_data = load_db()
        notifs = db_data.full_data.get("notifications", [])

        for n in notifs:
            if req.notif_id == "all":
                if n.get("target") in ["all", current_user.lower()]:
                    if current_user.lower() not in n.get("deleted_by", []):
                        n.setdefault("deleted_by", []).append(current_user.lower())
            else:
                if n.get("id") == req.notif_id:
                    if current_user.lower() not in n.get("deleted_by", []):
                        n.setdefault("deleted_by", []).append(current_user.lower())
                    break

        save_db(db_data)
    return {"status": "success"}


# ==========================================
# 15. NEXUSGGR CASINO & SPORTSBOOK INTEGRATION
# ==========================================
@app.get("/api/get-server-ip")
async def get_server_ip():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.ipify.org")
            return {"server_ip": response.text}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/get-providers")
async def get_real_providers():
    payload = {
        "method": "provider_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=15)
            return response.json()
        except Exception as e:
            print(f"⚠️ Erreur lors de la récupération des fournisseurs : {e}")
            return {"status": 0, "msg": "Erreur de connexion au fournisseur"}


@app.post("/api/get-providers")
async def get_real_games(request: ProviderRequest):
    provider_code = request.provider_code
    current_time = time.time()

    if provider_code in GAMES_CACHE and (current_time - GAMES_CACHE[provider_code]["time"]) < CACHE_TIME_LIMIT:
        return GAMES_CACHE[provider_code]["data"]

    payload = {
        "method": "game_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "provider_code": provider_code,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=20)
            response_data = response.json()

            if response_data.get("status") == 1 or "games" in response_data:
                GAMES_CACHE[provider_code] = {"time": current_time, "data": response_data}

            return response_data

        except Exception as e:
            print(f"⚠️ Erreur de connexion au fournisseur : {e}")
            if provider_code in GAMES_CACHE:
                return GAMES_CACHE[provider_code]["data"]
            return {"status": 0, "msg": "Erreur de connexion à l'API des jeux"}


@app.get("/api/provider/get-games-paged")
async def get_games_paged(provider: str = "PRAGMATIC", page: int = 1, limit: int = 50):
    current_time = time.time()
    if provider in GAMES_CACHE and (current_time - GAMES_CACHE[provider]["time"]) < CACHE_TIME_LIMIT:
        return GAMES_CACHE[provider]["data"]

    payload = {
        "method": "game_list",
        "agent_code": AGENT_CODE,
        "agent_token": AGENT_TOKEN,
        "provider_code": provider,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(PROVIDER_ENDPOINT, json=payload, timeout=20)
            response_data = response.json()
            if response_data.get("status") == 1 or "games" in response_data:
                GAMES_CACHE[provider] = {"time": current_time, "data": response_data}
            return response_data
        except Exception:
            return {"status": 0, "msg": "Erreur de connexion"}


@app.post("/api/provider/launch-casino")
async def launch_casino(request: Request):
    try:
        data = await request.json()

        current_site = get_site_id(request)
        prefix = get_site_prefix(current_site)
        dynamic_url = get_dynamic_url(request)

        user_code = str(data.get("user_code", "test_user")).lower().strip()
        prefixed_user = f"{prefix}_{user_code}"

        payload = {
            "method": "game_launch",
            "agent_code": AGENT_CODE,
            "agent_token": AGENT_TOKEN,
            "provider_code": str(data.get("provider_code", "PRAGMATIC")),
            "game_code": str(data.get("game_code", "")),
            "user_code": prefixed_user,
            "lang": "fr",
            "lobby_url": dynamic_url,
        }

        headers = {"Content-Type": "application/json"}
        endpoint = PROVIDER_ENDPOINT.rstrip("/")

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, headers=headers, timeout=20.0)
            res_data = response.json()

            game_url = res_data.get("launch_url") or res_data.get("url") or (res_data.get("data", {}).get("url"))
            if game_url:
                return {"launch_url": game_url}
            return {"error": "Jeu non disponible", "details": res_data}

    except Exception as e:
        print(f"❌ [LAUNCH CASINO ERROR]: {str(e)}")
        return {"error": str(e)}


@app.post("/api/provider/launch-sportsbook")
async def launch_sportsbook(request: Request):
    try:
        data = await request.json()

        current_site = get_site_id(request)
        prefix = get_site_prefix(current_site)
        dynamic_url = get_dynamic_url(request)

        user_code = str(data.get("user_code", "test_user")).lower().strip()
        prefixed_user = f"{prefix}_{user_code}"

        payload = {
            "method": "game_launch",
            "agent_code": AGENT_CODE,
            "agent_token": AGENT_TOKEN,
            "provider_code": str(data.get("provider_code", "SPORTSBOOK")),
            "game_code": str(data.get("game_code", "SPORTSBOOK")),
            "user_code": prefixed_user,
            "lang": "fr",
            "lobby_url": dynamic_url,
        }

        headers = {"Content-Type": "application/json"}
        endpoint = PROVIDER_ENDPOINT.rstrip("/")

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, headers=headers, timeout=20.0)
            try:
                response_data = response.json()
            except Exception:
                return {"error": "Réponse JSON invalide du fournisseur", "details": response.text}

            game_url = (
                response_data.get("url")
                or response_data.get("launch_url")
                or (response_data.get("data", {}).get("url"))
            )

            if game_url:
                return {"launch_url": game_url}
            return {"error": "Demande refusée par le fournisseur", "details": response_data}

    except Exception as e:
        print(f"❌ [CRITICAL ERROR IN LAUNCH SPORTSBOOK]: {str(e)}")
        return {"error": str(e)}


# ==========================================
# 16. NEXUSGGR SEAMLESS WALLET (/gold_api)
# ==========================================
@app.post("/gold_api")
@app.post("/gold_api/gold_api")
async def seamless_wallet_handler(request: Request):
    try:
        data = await request.json()
        method = data.get("method")
        user_code = (
            data.get("user_code")
            or data.get("username")
            or (data.get("slot", {}) if isinstance(data.get("slot"), dict) else {}).get("user_code")
            or ""
        )

        if method == "user_balance":
            db_data = load_db()
            target_user, actual_user_code, user_site = find_isolated_user(db_data, user_code)
            if not target_user:
                return JSONResponse(content={"status": 0, "msg": "USER_NOT_FOUND"})

            player_balance = round(float(target_user.get("balance", 0.0)), 2)
            vault_cashback = round(float(target_user.get("available_cashback", 0.0)), 2)
            return JSONResponse(
                content={
                    "status": 1,
                    "user_balance": player_balance,
                    "available_cashback": vault_cashback,
                }
            )

        elif method == "transaction":
            async with db_lock:
                db_data = load_db()
                target_user, actual_user_code, user_site = find_isolated_user(db_data, user_code)

                if not target_user:
                    return JSONResponse(content={"status": 0, "msg": "USER_NOT_FOUND"})

                player_balance = float(target_user.get("balance", 0.0))
                game_type = data.get("game_type")
                tx_data = data.get(game_type, {}) if game_type and isinstance(data.get(game_type), dict) else {}

                bet_money = float(tx_data.get("bet_money", 0.0))
                win_money = float(tx_data.get("win_money", 0.0))
                txn_type = tx_data.get("txn_type")
                txn_id = str(tx_data.get("txn_id", uuid.uuid4()))
                provider_code = str(tx_data.get("provider_code", "NEXUS_API"))

                if txn_type in ["debit", "debit_credit"]:
                    if player_balance < bet_money:
                        return JSONResponse(content={"status": 0, "msg": "INSUFFICIENT_USER_FUNDS"})

                    player_balance -= bet_money

                    if bet_money > 0:
                        db_session = SessionLocal()
                        try:
                            new_tx = Transaction(
                                admin_username=provider_code,
                                target_username=target_user["username"],
                                action="bet",
                                amount=bet_money,
                                date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                tx_id=txn_id,
                                site_id=user_site,
                            )
                            db_session.add(new_tx)
                            db_session.commit()
                        except Exception:
                            db_session.rollback()
                        finally:
                            db_session.close()

                if txn_type in ["credit", "debit_credit"]:
                    if win_money >= 30000:
                        db_session = SessionLocal()
                        try:
                            new_tx = Transaction(
                                admin_username="PENDING_HUGE_WIN",
                                target_username=target_user["username"],
                                action="huge_win (Nexus)",
                                amount=win_money,
                                date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                tx_id=txn_id,
                                site_id=user_site,
                            )
                            db_session.add(new_tx)
                            db_session.commit()
                        except Exception:
                            db_session.rollback()
                        finally:
                            db_session.close()
                    else:
                        player_balance += win_money
                        if win_money > 0:
                            db_session = SessionLocal()
                            try:
                                new_tx = Transaction(
                                    admin_username=provider_code,
                                    target_username=target_user["username"],
                                    action="win",
                                    amount=win_money,
                                    date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    tx_id=txn_id,
                                    site_id=user_site,
                                )
                                db_session.add(new_tx)
                                db_session.commit()
                            except Exception:
                                db_session.rollback()
                            finally:
                                db_session.close()

                target_user["balance"] = round(player_balance, 2)
                save_db(db_data)
                return JSONResponse(content={"status": 1, "user_balance": round(player_balance, 2)})

        return JSONResponse(content={"status": 0, "msg": "UNKNOWN_METHOD"})

    except Exception as e:
        print(f"🔥 GOLD API EXCEPTION: {e}")
        return JSONResponse(content={"status": 0, "msg": "INTERNAL_ERROR"})


# ==========================================
# 17. EUROVIRTUALS INTEGRATION & CALLBACKS
# ==========================================
def hash_create(request_data: dict, key: str) -> str:
    keys = sorted(request_data.keys())
    hashkey = ""
    for k in keys:
        value = request_data[k]
        if value is None:
            continue

        if isinstance(value, dict):
            nested_keys = sorted(value.keys())
            for nested_key in nested_keys:
                nested_value = value[nested_key]
                if nested_value is None:
                    continue
                serialized = json.dumps(nested_value, separators=(",", ":"), sort_keys=True)
                md5_hash = hashlib.md5(serialized.encode("utf-8")).hexdigest()
                hashkey += f"&{nested_key}={md5_hash}"
        elif isinstance(value, list):
            for index in range(len(value)):
                array_value = value[index]
                if array_value is None:
                    continue
                serialized = json.dumps(array_value, separators=(",", ":"), sort_keys=True)
                md5_hash = hashlib.md5(serialized.encode("utf-8")).hexdigest()
                hashkey += f"&{index}={md5_hash}"
        else:
            val_str = str(value).lower() if isinstance(value, bool) else str(value)
            hashkey += f"&{k}={val_str}"

    hashkey = hashkey.lstrip("&")
    final_string = hashkey + str(key)
    return hashlib.md5(final_string.encode("utf-8")).hexdigest()


def check_eurovirtuals_security(request: Request, payload: dict):
    token = str(request.headers.get("x-token-key") or request.headers.get("x-token") or "").strip()
    signature = str(request.headers.get("x-signature-key") or request.headers.get("x-signature") or "").strip()

    if token == "invalid-token-key":
        return {"status_code": 401, "status_description": "Invalid Token Key"}
    if signature == "invalid-signature-key":
        return {"status_code": 401, "status_description": "Invalid Signature"}
    if not token or not signature:
        return {"status_code": 401, "status_description": "Missing Security Headers"}

    expected_signature = hash_create(payload, token)
    if signature != expected_signature:
        print(f"🚨 [RADAR] SIGNATURE MISMATCH | Expected: {expected_signature} | Received: {signature}")
        return {"status_code": 401, "status_description": "Invalid Signature"}

    return None


@app.post("/api/eurovirtuals/callback/player_info")
async def eurovirtuals_player_info(request: Request):
    try:
        payload = await request.json()

        sec_err = check_eurovirtuals_security(request, payload)
        if sec_err:
            return JSONResponse(content=sec_err, status_code=200)

        raw_player_id = str(payload.get("player_id", "")).strip()
        db_data = load_db()
        target_user, actual_user_code, user_site = find_isolated_user(db_data, raw_player_id)

        if not target_user:
            return JSONResponse(content={"status_code": 500, "status_description": "Player not found"}, status_code=200)

        balance = round(float(target_user.get("balance", 0.0)), 2)
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")

        return JSONResponse(
            content={
                "status_code": 200,
                "status_description": "Success",
                "data": {
                    "balance": balance,
                    "currency": payload.get("currency", "TND"),
                    "player_id": raw_player_id,
                    "date": current_time,
                },
            },
            status_code=200,
        )
    except Exception as e:
        return JSONResponse(content={"status_code": 500, "status_description": str(e)}, status_code=200)


@app.post("/api/eurovirtuals/callback/bet")
async def eurovirtuals_bet(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(content={"status_code": 400, "status_description": "Bad Request"}, status_code=200)

    try:
        sec_err = check_eurovirtuals_security(request, payload)
        if sec_err:
            return JSONResponse(content=sec_err, status_code=200)

        raw_player_id = str(payload.get("player_id") or payload.get("user_code") or "").strip()
        currency = str(payload.get("currency") or "TND").strip()
        transaction_id = str(payload.get("transaction_id") or payload.get("txn_id") or "").strip()
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")

        def safe_float(val):
            try:
                if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
                    return 0.0
                return float(val)
            except Exception:
                return 0.0

        amount = safe_float(payload.get("amount") or payload.get("bet_amount"))

        async with db_lock:
            db_data = load_db()
            target_user, actual_user_code, user_site = find_isolated_user(db_data, raw_player_id)

            if not target_user:
                return JSONResponse(content={"status_code": 500, "status_description": "Player not found"}, status_code=200)

            current_balance = safe_float(target_user.get("balance", 0.0))

            def err_resp(code, desc):
                return JSONResponse(
                    content={
                        "status_code": code,
                        "status_description": desc,
                        "data": {
                            "balance": current_balance,
                            "currency": currency,
                            "reference_id": transaction_id,
                            "date": current_time,
                        },
                    },
                    status_code=200,
                )

            if amount < 0:
                return err_resp(400, "Bad Request")
            if current_balance < amount:
                return err_resp(402, "Insufficient Balance")

            db_session = SessionLocal()
            is_dup = False
            try:
                if transaction_id and db_session.query(Transaction).filter(Transaction.tx_id == transaction_id).first():
                    is_dup = True
            finally:
                db_session.close()

            if is_dup:
                return err_resp(200, "Success")

            new_balance = round(current_balance - amount, 2)
            target_user["balance"] = new_balance

            db_session = SessionLocal()
            try:
                new_tx = Transaction(
                    admin_username="EUROVIRTUALS_API",
                    target_username=target_user["username"],
                    action="bet",
                    amount=amount,
                    date=current_time,
                    tx_id=transaction_id,
                    site_id=user_site,
                )
                db_session.add(new_tx)
                db_session.commit()
            except Exception:
                db_session.rollback()
            finally:
                db_session.close()

            save_db(db_data)

        return JSONResponse(
            content={
                "status_code": 200,
                "status_description": "Success",
                "data": {
                    "balance": new_balance,
                    "currency": currency,
                    "reference_id": transaction_id,
                    "date": current_time,
                },
            },
            status_code=200,
        )
    except Exception as e:
        print(f"EUROVIRTUALS BET ERROR: {e}")
        return JSONResponse(content={"status_code": 500, "status_description": "Internal Server Error"}, status_code=200)


@app.post("/win")
@app.post("/api/eurovirtuals/win")
@app.post("/api/eurovirtuals/callback/win")
async def eurovirtuals_win(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"status_code": 400, "status_description": "Bad Request"}, status_code=200)

    try:
        sec_err = check_eurovirtuals_security(request, data)
        if sec_err:
            return JSONResponse(content=sec_err, status_code=200)

        tx_id = str(data.get("transaction_id") or data.get("txn_id") or "").strip()
        bet_id = str(data.get("bet_id", "")).strip()
        op_ref_id = str(data.get("operator_reference_id", "")).strip()
        action = str(data.get("action", "")).strip()
        currency = str(data.get("currency", "TND")).strip()
        raw_player_id = str(data.get("player_id") or data.get("user_code") or "").strip()
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")

        def safe_float(val):
            try:
                if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
                    return 0.0
                return float(val)
            except Exception:
                return 0.0

        payout_amount = safe_float(data.get("payout_amount") or data.get("amount"))

        async with db_lock:
            db_data = load_db()
            target_user, actual_user_code, user_site = find_isolated_user(db_data, raw_player_id)

            if not target_user:
                return JSONResponse(content={"status_code": 500, "status_description": "Player not found"}, status_code=200)

            curr = safe_float(target_user.get("balance", 0.0))

            def err_resp(code, desc):
                return JSONResponse(
                    content={
                        "status_code": code,
                        "status_description": desc,
                        "data": {
                            "balance": curr,
                            "currency": currency,
                            "reference_id": tx_id,
                            "date": current_time,
                        },
                    },
                    status_code=200,
                )

            if not tx_id or tx_id == "None":
                return err_resp(404, "Not Found")
            if payout_amount < 0:
                return err_resp(400, "Bad Request")

            orig_bet_id = bet_id if bet_id and bet_id != "None" else op_ref_id

            db_session = SessionLocal()
            is_dup = False
            original_bet_exists = False
            try:
                if db_session.query(Transaction).filter(Transaction.tx_id == tx_id).first():
                    is_dup = True

                if action in ["result_bet", "result_lost"] and orig_bet_id and orig_bet_id != "None":
                    if db_session.query(Transaction).filter(Transaction.tx_id == orig_bet_id).first():
                        original_bet_exists = True
            finally:
                db_session.close()

            if is_dup:
                return err_resp(200, "Success")

            if action in ["result_bet", "result_lost"]:
                if not original_bet_exists:
                    return err_resp(404, "Not Found")

                if bet_id and bet_id != "None":
                    file_path = "settled_bets.txt"
                    if os.path.exists(file_path):
                        with open(file_path, "r", encoding="utf-8") as f:
                            if bet_id in f.read().splitlines():
                                return err_resp(400, "Already Settled")

            if payout_amount >= 50.0:
                safe_player = actual_user_code[:4] + "***" if len(actual_user_code) > 4 else actual_user_code + "***"
                win_data = {
                    "player": safe_player,
                    "amount": payout_amount,
                    "game": "Jeux Virtuels (Euro)",
                }
                try:
                    asyncio.create_task(bigwin_manager.broadcast(json.dumps(win_data)))
                except Exception:
                    pass

            new_balance = curr

            if payout_amount >= 30000:
                db_session = SessionLocal()
                try:
                    new_tx = Transaction(
                        admin_username="PENDING_HUGE_WIN",
                        target_username=target_user["username"],
                        action="huge_win (EuroVirtuals)",
                        amount=payout_amount,
                        date=current_time,
                        tx_id=tx_id,
                        site_id=user_site,
                    )
                    db_session.add(new_tx)
                    db_session.commit()
                except Exception:
                    db_session.rollback()
                finally:
                    db_session.close()
            else:
                new_balance = round(curr + payout_amount, 2)
                target_user["balance"] = new_balance

                db_session = SessionLocal()
                try:
                    new_tx = Transaction(
                        admin_username="EUROVIRTUALS_API",
                        target_username=target_user["username"],
                        action="win",
                        amount=payout_amount,
                        date=current_time,
                        tx_id=tx_id,
                        site_id=user_site,
                    )
                    db_session.add(new_tx)
                    db_session.commit()
                except Exception:
                    db_session.rollback()
                finally:
                    db_session.close()

            if action in ["result_bet", "result_lost"] and bet_id and bet_id != "None":
                with open("settled_bets.txt", "a", encoding="utf-8") as f:
                    f.write(bet_id + "\n")

            save_db(db_data)

        return JSONResponse(
            content={
                "status_code": 200,
                "status_description": "Success",
                "data": {
                    "balance": new_balance,
                    "currency": currency,
                    "reference_id": tx_id,
                    "date": current_time,
                },
            },
            status_code=200,
        )
    except Exception as e:
        print(f"EUROVIRTUALS WIN ERROR: {e}")
        return JSONResponse(content={"status_code": 500, "status_description": "Internal Server Error"}, status_code=200)


@app.post("/rollback")
@app.post("/api/eurovirtuals/rollback")
@app.post("/api/eurovirtuals/callback/rollback")
async def eurovirtuals_rollback(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"status_code": 400, "status_description": "Bad Request"}, status_code=200)

    try:
        sec_err = check_eurovirtuals_security(request, data)
        if sec_err:
            return JSONResponse(content=sec_err, status_code=200)

        tx_id = str(data.get("transaction_id") or data.get("txn_id") or "").strip()
        action = str(data.get("action", "")).strip()
        bet_id = str(data.get("bet_id", "")).strip()
        win_tx_id = str(data.get("win_transaction_id", "")).strip()
        op_ref_id = str(data.get("operator_reference_id", "")).strip()
        currency = str(data.get("currency", "TND")).strip()
        raw_player_id = str(data.get("player_id") or data.get("user_code") or "").strip()
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")

        def safe_float(val):
            try:
                if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
                    return 0.0
                return float(val)
            except Exception:
                return 0.0

        payout_amount = safe_float(data.get("amount") or data.get("payout_amount"))
        is_rollback_win = "win" in action.lower() or "win" in str(data.get("type", "")).lower()

        async with db_lock:
            db_data = load_db()
            target_user, actual_user_code, user_site = find_isolated_user(db_data, raw_player_id)

            if not target_user:
                return JSONResponse(content={"status_code": 500, "status_description": "Player not found"}, status_code=200)

            curr = safe_float(target_user.get("balance", 0.0))

            def err_resp(code, desc):
                return JSONResponse(
                    content={
                        "status_code": code,
                        "status_description": desc,
                        "data": {
                            "balance": curr,
                            "currency": currency,
                            "reference_id": tx_id,
                            "date": current_time,
                        },
                    },
                    status_code=200,
                )

            if not tx_id or tx_id == "None":
                return err_resp(404, "Not Found")

            orig_bet_id = bet_id if bet_id and bet_id != "None" else op_ref_id
            target_win_id = win_tx_id if win_tx_id and win_tx_id != "None" else op_ref_id

            db_session = SessionLocal()
            is_dup = False
            target_exists = False
            try:
                if db_session.query(Transaction).filter(Transaction.tx_id == tx_id).first():
                    is_dup = True

                if action == "rollback_bet" and orig_bet_id and orig_bet_id != "None":
                    if db_session.query(Transaction).filter(Transaction.tx_id == orig_bet_id).first():
                        target_exists = True
                elif action == "rollback_win" and target_win_id and target_win_id != "None":
                    if db_session.query(Transaction).filter(Transaction.tx_id == target_win_id).first():
                        target_exists = True
            finally:
                db_session.close()

            if is_dup:
                return err_resp(200, "Success")

            if action in ["rollback_bet", "rollback_win"] and not target_exists:
                return err_resp(404, "Not Found")

            new_balance = round(curr - payout_amount, 2) if is_rollback_win else round(curr + payout_amount, 2)
            target_user["balance"] = new_balance

            db_session = SessionLocal()
            try:
                new_tx = Transaction(
                    admin_username="EUROVIRTUALS_API",
                    target_username=target_user["username"],
                    action="rollback",
                    amount=payout_amount,
                    date=current_time,
                    tx_id=tx_id,
                    site_id=user_site,
                )
                db_session.add(new_tx)
                db_session.commit()
            except Exception:
                db_session.rollback()
            finally:
                db_session.close()

            save_db(db_data)

        return JSONResponse(
            content={
                "status_code": 200,
                "status_description": "Success",
                "data": {
                    "balance": new_balance,
                    "currency": currency,
                    "reference_id": tx_id,
                    "date": current_time,
                },
            },
            status_code=200,
        )
    except Exception as e:
        print(f"EUROVIRTUALS ROLLBACK ERROR: {e}")
        return JSONResponse(content={"status_code": 500, "status_description": "Internal Server Error"}, status_code=200)


@app.post("/api/eurovirtuals/adjustment")
@app.post("/api/eurovirtuals/callback/adjustment")
async def eurovirtuals_adjustment(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(content={"status_code": 400, "status_description": "Bad Request"}, status_code=200)

    try:
        sec_err = check_eurovirtuals_security(request, data)
        if sec_err:
            return JSONResponse(content=sec_err, status_code=200)

        tx_id = str(data.get("transaction_id", "")).strip()
        raw_player_id = str(data.get("player_id") or data.get("user_code") or "").strip()
        amount = abs(float(data.get("amount", 0.0)))
        action = data.get("action", "")
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")

        db_session = SessionLocal()
        is_dup = False
        try:
            if tx_id and db_session.query(Transaction).filter(Transaction.tx_id == tx_id).first():
                is_dup = True
        except Exception:
            pass
        finally:
            db_session.close()

        if is_dup:
            return JSONResponse(content={"status_code": 202, "status_description": "Duplicate request"}, status_code=200)

        new_balance = 0.0
        async with db_lock:
            db_data = load_db()
            target_user, actual_user_code, user_site = find_isolated_user(db_data, raw_player_id)

            if not target_user:
                return JSONResponse(content={"status_code": 500, "status_description": "Player not found"}, status_code=200)

            curr = float(target_user.get("balance", 0.0))
            new_balance = round(curr - amount, 2) if action == "wallet_adjustment_debit" else round(curr + amount, 2)
            target_user["balance"] = new_balance

            db_session = SessionLocal()
            try:
                if tx_id:
                    new_tx = Transaction(
                        admin_username="EUROVIRTUALS_API",
                        target_username=target_user["username"],
                        action="adjustment",
                        amount=amount,
                        date=current_time,
                        tx_id=tx_id,
                        site_id=user_site,
                    )
                    db_session.add(new_tx)
                    db_session.commit()
            except Exception:
                db_session.rollback()
            finally:
                db_session.close()

            save_db(db_data)

        return JSONResponse(
            content={
                "status_code": 200,
                "status_description": "Success",
                "data": {
                    "balance": new_balance,
                    "currency": "TND",
                    "reference_id": tx_id,
                    "date": current_time,
                },
            },
            status_code=200,
        )
    except Exception as e:
        print(f"EUROVIRTUALS ADJUSTMENT ERROR: {e}")
        return JSONResponse(content={"status_code": 500, "status_description": "Internal Server Error"}, status_code=200)


@app.get("/api/eurovirtuals/studios")
async def get_eurovirtuals_studios():
    payload = {}
    timestamp = str(int(time.time()))
    signature = hash_create(payload, EURO_APP_KEY)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": EURO_API_KEY,
        "x-signature-key": signature,
        "x-timestamp": timestamp,
    }

    base_url_clean = str(EURO_BASE_URL).rstrip("/")
    endpoint = f"{base_url_clean}/v1/partners"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(endpoint, headers=headers, timeout=20.0)
            return response.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}


@app.get("/api/eurovirtuals/studio-games/{partner_id}")
async def get_games_by_partner_studio(partner_id: str):
    payload = {}
    timestamp = str(int(time.time()))
    signature = hash_create(payload, EURO_APP_KEY)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": EURO_API_KEY,
        "x-signature-key": signature,
        "x-timestamp": timestamp,
    }

    base_url_clean = str(EURO_BASE_URL).rstrip("/")
    endpoint = f"{base_url_clean}/v1/games?partner_id={partner_id}&limit=5000"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(endpoint, headers=headers, timeout=20.0)
            data = response.json()

            if data.get("status_code") == 200 or response.status_code == 200:
                games_list = (
                    data.get("data", {}).get("data", [])
                    if isinstance(data.get("data"), dict)
                    else data.get("data", [])
                )
                return {"status": "success", "games": games_list}
            return {"status": "error", "details": data}
        except Exception as e:
            return {"status": "error", "error": str(e)}


@app.get("/api/get-eurovirtuals-games")
async def fetch_eurovirtuals_games(partner_id: str = None):
    payload = {}
    timestamp = str(int(time.time()))
    signature = hash_create(payload, EURO_APP_KEY)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-api-key": EURO_API_KEY,
        "x-signature-key": signature,
        "x-timestamp": timestamp,
    }

    base_url_clean = str(EURO_BASE_URL).rstrip("/")
    all_games = []

    async with httpx.AsyncClient() as client:
        try:
            partners_list = []

            if partner_id:
                partners_list.append({"id": partner_id, "name": partner_id})
            else:
                p_resp = await client.get(f"{base_url_clean}/v1/partners", headers=headers, timeout=20.0)
                p_data = p_resp.json()
                if p_data.get("status_code") == 200:
                    partners_list = p_data.get("data", [])
                else:
                    partners_list = [{"id": "", "name": "EuroVirtuals"}]

            for partner in partners_list:
                p_id = partner.get("id")
                p_name = partner.get("name", "Unknown")
                page = 1

                while True:
                    endpoint = f"{base_url_clean}/v1/games?page={page}&per_page=500"
                    if p_id:
                        endpoint += f"&partner_id={p_id}"

                    res = await client.get(endpoint, headers=headers, timeout=20.0)
                    data = res.json()

                    if data.get("status_code") == 200:
                        page_data = data.get("data", {})
                        games_chunk = page_data.get("data", [])

                        if not games_chunk:
                            break

                        for game in games_chunk:
                            img = game.get("logo") or game.get("thumbnail") or ""
                            game["image"] = game["img"] = img
                            game["game_code"] = game.get("uuid") or game.get("id")
                            game["provider"] = p_name

                        all_games.extend(games_chunk)
                        total_available = page_data.get("total", 0)

                        if len(games_chunk) == 0 or (page * 500) >= total_available:
                            break

                        page += 1
                    else:
                        break

            return {"status": "success", "games": all_games}

        except Exception as e:
            return {"status": "error", "error": str(e)}


@app.post("/api/provider/launch-eurovirtuals")
async def launch_eurovirtuals(request: Request):
    try:
        data = await request.json()

        game_uuid = str(data.get("game_uuid") or data.get("game_code") or data.get("id") or "")
        if not game_uuid or game_uuid == "undefined":
            return {"error": "UUID du jeu manquant"}

        user_code = str(data.get("user_code", "test_user")).lower().strip()
        timestamp = str(int(time.time()))

        current_site = get_site_id(request)
        prefix = get_site_prefix(current_site)
        dynamic_url = get_dynamic_url(request)
        prefixed_user = f"{prefix}_{user_code}"

        async with db_lock:
            db_data = load_db()
            target_user, _, _ = find_isolated_user(db_data, prefixed_user)
            if not target_user or target_user.get("is_blocked") == 1:
                return {"error": "Joueur introuvable ou compte suspendu"}
            current_balance = float(target_user.get("balance", 0.0))

        unique_session_token = f"tok_{prefixed_user}_{int(time.time())}"

        payload = {
            "player_id": prefixed_user,
            "player_name": prefixed_user,
            "player_token": unique_session_token,
            "currency": "TND",
            "demo": 0,
            "game_uuid": game_uuid,
            "balance": current_balance,
            "country": "TN",
            "language": "fr",
            "device": "desktop",
            "return_url": dynamic_url,
            "lobby_url": dynamic_url,
        }

        signature = hash_create(payload, EURO_APP_KEY)

        headers = {
            "Accept": "application/json",
            "x-api-key": EURO_API_KEY,
            "x-signature-key": signature,
            "x-timestamp": timestamp,
            "Content-Type": "application/json",
        }

        base_url_clean = str(EURO_BASE_URL).rstrip("/")
        launch_endpoint = f"{base_url_clean}/v1/launch"

        async with httpx.AsyncClient() as client:
            response = await client.post(launch_endpoint, json=payload, headers=headers, timeout=20.0)
            raw_response = response.text

            try:
                response_data = response.json()
            except Exception:
                return {"error": "Réponse JSON invalide du fournisseur", "raw_text": raw_response}

            if response_data.get("status_code") == 200:
                game_url = response_data.get("data", {}).get("url")
                if game_url and game_url.startswith("/"):
                    game_url = f"{base_url_clean}{game_url}"
                return {"launch_url": game_url}

            return {
                "error": response_data.get("status_description", "Lancement refusé par le fournisseur"),
                "provider_details": response_data,
            }

    except Exception as e:
        return {"error": str(e)}


# ==========================================
# 18. JACKPOTS, WEBSOCKETS & BACKGROUND TASKS
# ==========================================
JACKPOTS_CONFIG = {
    "mini": {"start": 30.0, "max": 150.0, "days": 1, "share": 0.40},
    "minor": {"start": 80.0, "max": 300.0, "days": 7, "share": 0.30},
    "major": {"start": 150.0, "max": 500.0, "days": 15, "share": 0.20},
    "grand": {"start": 750.0, "max": 2500.0, "days": 30, "share": 0.10},
}

jackpots_state = {
    "mini": {
        "current_amount": 30.0,
        "drop_threshold": random.uniform(80.0, 150.0),
        "deadline": datetime.now() + timedelta(days=1),
    },
    "minor": {
        "current_amount": 80.0,
        "drop_threshold": random.uniform(150.0, 300.0),
        "deadline": datetime.now() + timedelta(days=7),
    },
    "major": {
        "current_amount": 150.0,
        "drop_threshold": random.uniform(250.0, 500.0),
        "deadline": datetime.now() + timedelta(days=15),
    },
    "grand": {
        "current_amount": 750.0,
        "drop_threshold": random.uniform(1200.0, 2500.0),
        "deadline": datetime.now() + timedelta(days=30),
    },
}


def process_loss_and_check_jackpot(player_id: str, loss_amount: float):
    jackpot_funding = loss_amount * 0.02
    winners = []

    for level, config in JACKPOTS_CONFIG.items():
        state = jackpots_state[level]

        added_amount = jackpot_funding * config["share"]
        if state["current_amount"] + added_amount <= config["max"]:
            state["current_amount"] += added_amount

        if state["current_amount"] >= state["drop_threshold"] or datetime.now() >= state["deadline"]:
            winners.append({"level": level, "amount": state["current_amount"]})
            state["current_amount"] = config["start"]
            state["drop_threshold"] = random.uniform(config["start"] * 1.3, config["max"])
            state["deadline"] = datetime.now() + timedelta(days=config["days"])

    return winners


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


jackpot_manager = ConnectionManager()
bigwin_manager = ConnectionManager()


@app.websocket("/ws/bigwins")
async def websocket_bigwins(websocket: WebSocket):
    await bigwin_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        bigwin_manager.disconnect(websocket)


@app.websocket("/ws/jackpot")
async def websocket_jackpot(websocket: WebSocket):
    await jackpot_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        jackpot_manager.disconnect(websocket)


async def broadcast_jackpots():
    while True:
        # 🌟 زيادة الأرقام تلقائياً لتبدو حية أمام المستخدمين (Auto-Increment Simulator)
        for level, config in JACKPOTS_CONFIG.items():
            state = jackpots_state[level]
            
            # زيادة عشوائية مستمرة (قروش بسيطة)
            if level == "mini": increment = random.uniform(0.01, 0.05)
            elif level == "minor": increment = random.uniform(0.02, 0.08)
            elif level == "major": increment = random.uniform(0.05, 0.15)
            else: increment = random.uniform(0.10, 0.40)
            
            state["current_amount"] += increment
            
            # إذا ضرب الجاكبوت الحد الأقصى أو حد السقوط، يعود لنقطة البداية
            if state["current_amount"] >= state["drop_threshold"]:
                state["current_amount"] = config["start"]
                state["drop_threshold"] = random.uniform(config["start"] * 1.3, config["max"])
                state["deadline"] = datetime.now() + timedelta(days=config["days"])

        live_data = {
            "mini": jackpots_state["mini"]["current_amount"],
            "minor": jackpots_state["minor"]["current_amount"],
            "major": jackpots_state["major"]["current_amount"],
            "grand": jackpots_state["grand"]["current_amount"],
        }
        await jackpot_manager.broadcast(json.dumps(live_data))
        await asyncio.sleep(2.5)


async def auto_settle_tickets():
    await asyncio.sleep(10)
    while True:
        try:
            async with db_lock:
                tickets_db = load_tickets_db()
                db_data = load_db()
                changes_made = False
                pending_tickets = [t for t in tickets_db if t.get("status") == "encours"]

                for ticket in pending_tickets:
                    simulated_result = random.choice(["gagne", "perdu"])
                    ticket["status"] = simulated_result
                    changes_made = True
                    if simulated_result == "gagne":
                        target_username = ticket.get("username")
                        win_amount = float(ticket.get("gain", 0))
                        for u in db_data:
                            if u.get("username") == target_username:
                                u["balance"] = round(float(u.get("balance", 0)) + win_amount, 2)
                                break
                if changes_made:
                    save_tickets_db(tickets_db)
                    save_db(db_data)
        except Exception as e:
            print(f"❌ [Auto-Settler] Error: {e}")
        await asyncio.sleep(60)


async def daily_cashback_system():
    await asyncio.sleep(15)
    while True:
        try:
            now = datetime.now()
            if now.hour == 0 and now.minute < 10:
                async with db_lock:
                    db_data = load_db()
                    changes_made = False

                    for u in db_data:
                        current_balance = float(u.get("balance", 0.0))
                        daily_deps = float(u.get("daily_deposits", 0.0))
                        net_loss = daily_deps - current_balance

                        if daily_deps > 0:
                            if current_balance < 1.0 and net_loss > 0:
                                cashback_amount = daily_deps * 0.10
                                current_vault = float(u.get("available_cashback", 0.0))
                                u["available_cashback"] = round(current_vault + cashback_amount, 2)

                            u["daily_deposits"] = 0.0
                            changes_made = True

                    if changes_made:
                        save_db(db_data)

                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(300)
        except Exception as e:
            print(f"❌ [Cashback] Error: {e}")
            await asyncio.sleep(300)


@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(auto_settle_tickets())
    asyncio.create_task(daily_cashback_system())
    asyncio.create_task(broadcast_jackpots())
