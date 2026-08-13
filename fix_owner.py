import sqlite3

HASHED_PASSWORD = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQqiRQYq"

print("جاري الاتصال بقاعدة البيانات...")
try:
    conn = sqlite3.connect('local_test.db')
    cursor = conn.cursor()

    # تحديث كلمة السر في جدول alpha_users للحساب fethi
    cursor.execute("UPDATE alpha_users SET password = ? WHERE username = 'fethi'", (HASHED_PASSWORD,))
    conn.commit()

    if cursor.rowcount > 0:
        print("✅ تم بنجاح! كلمة السر الجديدة لحساب fethi هي: 123456")
    else:
        print("❌ خطأ: لم يتم العثور على الحساب fethi في جدول alpha_users.")

except Exception as e:
    print(f"حدث خطأ أثناء الاتصال: {e}")
finally:
    conn.close()