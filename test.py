import requests

URL = "https://api.nexusggr.com"

# هذا الكود سيقوم بتجربة فتح اللعبة مباشرة لمعرفة سبب الرفض
payload = {
    "method": "game_launch",
    "agent_code": "TUNISS10",
    "agent_token": "640155e57fcb46b910e23fafd9e858e1",
    "user_code": "fethi_test", # وضعنا اسم لاعب تجريبي
    "provider_code": "SPORTSBOOK",
    "game_code": "Nexustrike",
    "lang": "en"
}

headers = {"Content-Type": "application/json"}

try:
    response = requests.post(URL, json=payload, headers=headers)
    print("=== رد المزود عند محاولة فتح اللعبة ===")
    print(response.json())
except Exception as e:
    print("حدث خطأ:", e)