import json
import hashlib

# دالة تشفير كلمة السر الخاصة بنظامك
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# اسم حساب الأونر الخاص بك وكلمة السر الجديدة التي تريدها
OWNER_USERNAME = "fethi"  # ضع اسم حساب الأونر هنا إن لم يكن fethi
NEW_PASSWORD = "Fethi1987123456"      # ضع كلمة السر الجديدة السهلة مؤقتاً

try:
    # قراءة قاعدة البيانات الحالية (تأكد من اسم الملف لديك مثل tickets_database.json أو local_test.db)
    with open("tickets_database.json", "r", encoding="utf-8") as f:
        db = json.load(f)
    
    found = False
    for user in db:
        if user.get("username") == OWNER_USERNAME:
            user["password"] = hash_password(NEW_PASSWORD)
            found = True
            break
            
    if found:
        with open("tickets_database.json", "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
        print(f"✅ تم تحديث كلمة سر الأونر [{OWNER_USERNAME}] بنجاح إلى: {NEW_PASSWORD}")
    else:
        print(f"❌ لم يتم العثور على المستخدم {OWNER_USERNAME} في قاعدة البيانات.")

except Exception as e:
    print(f"❌ حدث خطأ: {e}")