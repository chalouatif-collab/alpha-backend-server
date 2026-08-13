import sqlite3
import bcrypt  # مكتبة التشفير التي يستخدمها نظامك
# اطلب كلمة السر الجديدة مباشرة في واجهة الأوامر
new_password = input("ZPFWxnr2613MLO@3.12FRSKL15")

# تشفير الكلمة بطريقة مطابقة لقاعدة بياناتك
hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

print("جاري الاتصال بقاعدة البيانات...")
try:
    conn = sqlite3.connect('local_test.db')
    cursor = conn.cursor()

    # تحديث الكلمة المشفرة في الجدول
    cursor.execute("UPDATE alpha_users SET password = ? WHERE username = 'fethi'", (hashed_password,))
    conn.commit()

    if cursor.rowcount > 0:
        print(f"✅ تم التحديث بنجاح! كلمة السر الجديدة هي: {new_password}")
    else:
        print("❌ خطأ: لم يتم العثور على الحساب.")

except Exception as e:
    print(f"حدث خطأ أثناء الاتصال: {e}")
finally:
    conn.close()