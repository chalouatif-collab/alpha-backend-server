import firebase_admin
from firebase_admin import credentials, db
from passlib.context import CryptContext

# تشفير كلمة السر الجديدة
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
new_password = "123456"
hashed_password = pwd_context.hash(new_password)

# الاتصال بقاعدة البيانات
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://alphabet-7d14c-default-rtdb.firebaseio.com/'
})

ref = db.reference('/')
data = ref.get()
users = data.get("users", [])

# البحث عن حساب فتي وتغيير كلمة السر
found = False
for i, user in enumerate(users):
    if user and user.get("username") == "fethi":
        # تحديث كلمة السر في السحابة
        db.reference(f'/users/{i}').update({"password": hashed_password})
        print(f"✅ تم تغيير كلمة السر لحساب fethi بنجاح إلى: {new_password}")
        found = True
        break

if not found:
    print("❌ لم يتم العثور على حساب fethi في قاعدة البيانات.")