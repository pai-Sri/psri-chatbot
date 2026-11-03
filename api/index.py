import os
import json
import requests
from flask import Flask, request, jsonify
from supabase import create_client, Client
import google.generativeai as genai
from datetime import datetime

app = Flask(__name__)

# ดึงค่า API Keys จากระบบของ Vercel
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ตั้งค่าการเชื่อมต่อฐานข้อมูลและ AI
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_API_KEY)

# ใช้ Gemini 1.5 Flash และให้ตอบกลับเป็นรูปแบบ JSON เสมอ
model = genai.GenerativeModel(
    'gemini-1.5-flash',
    generation_config={"response_mime_type": "application/json"}
)

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

def process_with_ai(user_text):
    prompt = f"""
    แยกข้อมูลรายรับรายจ่ายจากข้อความนี้: "{user_text}"
    ให้อยู่ในรูปแบบ JSON โดยมีโครงสร้างคีย์ดังนี้:
    {{
        "date": "YYYY-MM-DD", (ใช้วันที่ปัจจุบันคือ {datetime.now().strftime('%Y-%m-%d')} ยกเว้นผู้ใช้ระบุเป็นอย่างอื่น)
        "description": "ชื่อรายการ (สั้นๆ)",
        "type": "รายรับ" หรือ "รายจ่าย",
        "category": "หมวดหมู่ (เช่น อาหาร, เดินทาง, ของใช้, รายได้, อื่นๆ)",
        "amount": ตัวเลขจำนวนเงิน (ตัวเลขเท่านั้น),
        "payment_method": "เงินสด" หรือ "โอน" หรือ "ไม่ระบุ"
    }}
    """
    response = model.generate_content(prompt)
    return json.loads(response.text)

def get_monthly_summary(chat_id):
    current_month = datetime.now().strftime('%Y-%m')
    
    # ดึงข้อมูลจากฐานข้อมูลของเดือนปัจจุบันและเฉพาะแชทนี้
    db_response = supabase.table("transactions").select('*').eq('chat_id', chat_id).execute()
    data = db_response.data

    # PostgREST normally returns a list of row dictionaries. Also accept JSON
    # strings in case the result (or an individual row) was serialized upstream.
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Unexpected transactions response format")
    data = [json.loads(item) if isinstance(item, str) else item for item in data]
    if any(not isinstance(item, dict) for item in data):
        raise ValueError("Transaction rows must be JSON objects")
    
    if not data:
        return "ยังไม่มีรายการในเดือนนี้ครับ"
        
    total_income = sum(float(item['amount']) for item in data if item['type'] == 'รายรับ' and item['date'].startswith(current_month))
    total_expense = sum(float(item['amount']) for item in data if item['type'] == 'รายจ่าย' and item['date'].startswith(current_month))
    balance = total_income - total_expense
    
    return f"📊 สรุปยอดเดือน {current_month}\nรายรับรวม: {total_income:,.2f} บาท\nรายจ่ายรวม: {total_expense:,.2f} บาท\nคงเหลือ: {balance:,.2f} บาท"

@app.route('/', methods=['POST'])
def webhook():
    data = request.get_json()
    
    # ตรวจสอบว่ามีข้อความส่งมาหรือไม่
    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]
        
        # ถ้าผู้ใช้พิมพ์คำว่า สรุป
        if "สรุป" in text:
            summary = get_monthly_summary(chat_id)
            send_telegram_message(chat_id, summary)
            return jsonify({"status": "ok"})
            
        # ถ้าเป็นประโยคทั่วไป ให้ AI แยกข้อมูลและบันทึก
        try:
            parsed_data = process_with_ai(text)
            
            supabase.table("transactions").insert({
                "chat_id": chat_id,
                "date": parsed_data["date"],
                "description": parsed_data["description"],
                "type": parsed_data["type"],
                "category": parsed_data["category"],
                "amount": parsed_data["amount"],
                "payment_method": parsed_data["payment_method"]
            }).execute()
            
            reply_msg = f"✅ บันทึก{parsed_data['type']}เรียบร้อย\n📝 {parsed_data['description']} ({parsed_data['category']})\n💰 {parsed_data['amount']} บาท"
            send_telegram_message(chat_id, reply_msg)
            
        except Exception as e:
            send_telegram_message(chat_id, "❌ P.Sri ไม่เข้าใจข้อมูลครับ ลองพิมพ์ใหม่ เช่น 'กินข้าว 50 บาท'")
            
    return jsonify({"status": "ok"})
