import datetime
from flask import Flask, request, jsonify
import json
import requests
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
import gspread

app = Flask(__name__)

# Verification token from Slack
SLACK_VERIFICATION_TOKEN = "5"
SLACK_BOT_TOKEN = "xoxb-5-5-5"
GOOGLE_AI_API_KEY = "5"
GOOGLE_AI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key="
SERVICE_ACCOUNT_FILE = './service-account.json'

current_slack_url = ""

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
client = gspread.authorize(creds)
drive_service = build('drive', 'v3', credentials=creds)

def send_text_to_generative_ai(text, prompt, format_return, function_rule, few_shot):
    headers = {
      "Content-Type": "application/json",
    }
    data = {
      "contents": [{
        "parts":[
          {
            "text": prompt
          },
          {
            "text": format_return
          },
          {
            "text": function_rule 
          },
          {
            "text": few_shot
          },
          {
            "text": text
          }
        ]
      }]
    }
    response = requests.post(GOOGLE_AI_API_URL + GOOGLE_AI_API_KEY, headers=headers, json=data)
    if response.status_code == 200:
      return response.json()
    else:
      return response.json(), response.status_code

def get_user_info(user_id):
    url = "https://slack.com/api/users.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"user": user_id}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        user_info = response.json()
        if user_info.get("ok"):
            user_info = user_info["user"]
            return {
                "real_name": user_info["real_name"],
                "display_name": user_info.get("profile", {}).get("display_name", "")
            }
    return None

def get_message_permalink(channel_id, message_ts):
  url = "https://slack.com/api/chat.getPermalink"
  headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
  params = {"channel": channel_id, "message_ts": message_ts}
  response = requests.get(url, headers=headers, params=params)
  if response.status_code == 200:
    permalink_info = response.json()
    if permalink_info.get("ok"):
        return permalink_info["permalink"]
  return None

def get_spreadsheet_id(name):
  query = f"name='{name}' and mimeType='application/vnd.google-apps.spreadsheet'"
  results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
  files = results.get('files', [])
  if files:
    spreadsheet = client.open_by_key(files[0]['id'])
  else:
    spreadsheet = client.create(name)
    spreadsheet.share('', perm_type='anyone', role='writer')
  return spreadsheet.id

def get_spreadsheet_sheet_id(spreadsheet_id, sheet_name):
  spreadsheet = client.open_by_key(spreadsheet_id)
  try:
    sheet = spreadsheet.worksheet(sheet_name)
  except gspread.exceptions.WorksheetNotFound:
    sheet = spreadsheet.add_worksheet(title=sheet_name, rows=9000, cols=20)
    sheet.append_row([
      "วันที่แจ้ง (time stamp)",
      "user_id",
      "ชื่อ-นามสกุล",
      "ชื่อเล่น",
      "ประเภทลา",
      "วันที่ลา",
      "ลาเต็มวันหรือครึ่งวัน",
      "รายละเอียด",
      "slack"
    ])
  return sheet.id

def store_leave_lists(spreadsheet_name, sheet_name, leave_lists):
  spreadsheet_id = get_spreadsheet_id(spreadsheet_name)
  get_spreadsheet_sheet_id(spreadsheet_id, sheet_name)
  sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
  for leave_list in leave_lists:
    sheet.append_row([
      leave_list["date_request"],
      leave_list["user_id"],
      leave_list["user_real_name"],
      leave_list["user_display_name"],
      leave_list["leave_type"],
      leave_list["date"],
      leave_list["is_full_leave"],
      leave_list["detail"],
      leave_list["slack_url"]
    ])
  return spreadsheet_id

def search_by_user_id(spreadsheet_name, user_id):
  spreadsheet_id = get_spreadsheet_id(spreadsheet_name)
  sheet = client.open_by_key(spreadsheet_id)
  
  current_year = datetime.datetime.now().year + 543
  previous_year = current_year - 1
  next_year = current_year + 1
  
  user_records = []
  for year in [previous_year, current_year, next_year]:
    try:
      ws = sheet.worksheet(str(year))
      records = ws.get_all_records()
      user_records.extend([record for record in records if record['user_id'] == user_id])
    except gspread.exceptions.WorksheetNotFound:
      continue
  return user_records

def exclude_exist_date_in_request(leave_lists, exist_lists, channel_id, thread_ts):
    for leave_list in leave_lists:
      for exist_list in exist_lists:
        if leave_list["date"] == exist_list["วันที่ลา"]:
          leave_lists.remove(leave_list)
          reply_to_thread(channel_id, thread_ts, f"ไม่สามารถแจ้งวันที่ลาไปแล้วได้:"+exist_list["วันที่ลา"])
          break
    return leave_lists

def find_row_by_user_id_and_date(sheet, user_id, date):
  current_year = datetime.datetime.now().year + 543
  next_year = current_year + 1
  for year in [current_year, next_year]:
    try:
      ws = sheet.worksheet(str(year))
    except gspread.exceptions.WorksheetNotFound:
      continue
    records = ws.get_all_records()
    for idx, record in enumerate(records):
      if record['user_id'] == user_id and record['วันที่ลา'] == date:
        return idx + 1, ws.id
    return None


def remove_row(spreadsheet_name, user_id, date):
  spreadsheet_id = get_spreadsheet_id(spreadsheet_name)
  sheet = client.open_by_key(spreadsheet_id)
  row, sheet_id = find_row_by_user_id_and_date(sheet, user_id, date)
  if row:
    body = {
      "requests": [
        {
          "deleteDimension": {
            "range": {
              "sheetId": sheet_id,
              "dimension": "ROWS",
              "startIndex": row,
              'endIndex': row + 1
            }
          }
        }
      ]
    }
    sheet.batch_update(body)

def exclude_before_today(leave_lists, channel_id, thread_ts):
    today = datetime.date.today()
    buddhist_year = today.year + 543
    today_buddhist_date = today.strftime(f"%d/%m/{buddhist_year}")
    today_date = datetime.datetime.strptime(today_buddhist_date, "%d/%m/%Y").date()
    filtered_leaves = []
    for leave in leave_lists:
      leave_date_str = leave["date"]
      leave_date = datetime.datetime.strptime(leave_date_str, "%d/%m/%Y").date()
      if leave_date >= today_date:
        filtered_leaves.append(leave)
      else: 
        reply_to_thread(channel_id, thread_ts, f"ไม่สามารถแจ้งวันที่เลยไปแล้ว")
    return filtered_leaves
def reply_to_thread(channel_id, thread_ts, message):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    data = {
        "channel": channel_id,
        "text": message,
        "thread_ts": thread_ts
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
        print(f"Error posting message: {response.status_code}, {response.text}")

@app.route("/slack/events", methods=["POST"])
def slack_events():
    global current_slack_url
    data = request.json
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})
    slack_url = get_message_permalink(data["event"]["channel"], data["event"]["ts"])
    if current_slack_url == slack_url:
      return jsonify({"status": "ok", "message": "duplicate detected"})
    else :
      current_slack_url = slack_url

    if data.get("token") != SLACK_VERIFICATION_TOKEN:
        return jsonify({"error": "Invalid request token"}), 403
    
    if data.get("event") and data["event"]["type"] == "message" and "subtype" not in data["event"]:
        
        thread_ts = data["event"].get("thread_ts", data["event"]["ts"])
        channel_id = data["event"]["channel"]
        user_id = data["event"]["user"]
        text = data["event"]["text"]

        if "/leave-condition" in text:
          return jsonify({"status": "ok"})
        
        user_info = get_user_info(user_id)
        if user_info["display_name"] == "" :
          print("user not found")
          return jsonify({"status": "ok", "message": "user not found"})
        prompt = "check leave 1.is really leave, cancel or change 2.what leave type(ลากิจ, ลาป่วย, ลาพักร้อน, ลาคลอด) 3.which days they request to leave"
        format_return = "if really request leave return as json in this format '[{command: \"add or cancel\",leave_type : \"leave type\", date : [\"DD/MM/YYYY ครึ่งวัน or เต็มวัน\", etc..]},...etc]' \n if not really request leave return {[]} dont forget return json in 1 line .\n if input dont have date in text, date should be in format date: ['to_day ครึ่งวัน or เต็มวัน'] and if they give wrong date or that date not exist give date: ['wrong date'] instead , pls remember dont response in another"
        function_rule = "if they give cancel leave give them command: cancel,if user give change the leave then return command: add for the new and cancel for the old\n the words that mean change ['สลับ', 'เปลี่ยน', 'เปลี่ยนเป็น', etc..]"
        few_shot = 'example user: 24/07/2024 มิกซ์ขอยกเลิกลาพักร้อน 24-26/07/24 (3 วัน) ครับผม เนื่องจาก ฮอดบ้านแล้วครับ answer:[ {"command":  "cancel", "leave_type" : "ลาพักร้อน","date" : ["24/07/2567 เต็มวัน", "25/07/2567 เต็มวัน", "26/07/2567 เต็มวัน"]}] \n user: แจ้งสลับวันหยุดวันที่ 02/08 หยุดวันที่ 27/07 ค่ะ แจ้งหัวหน้าเรียบร้อยค่ะ answer:[ {"command":  "cancel", "leave_type" : "ลากิจ","date" : ["02/08/2567 เต็มวัน", ]}, {"command":  "add", "leave_type" : "ลากิจ","date" : ["27/07/2567 เต็มวัน"]}]'
        ai_res = send_text_to_generative_ai("\n this is text to check: " + text + "", prompt, format_return, function_rule, few_shot)
        ai_res_text = ai_res["candidates"][0]["content"]["parts"][0]["text"]
        ai_res_text = ai_res_text.strip('\n')
        ai_res_text = ai_res_text.strip('```')
        ai_res_text = ai_res_text.strip('json')
        ai_res_json = json.loads(ai_res_text)
        if ai_res_json == {}:
          return jsonify({"status": "not really request leave"})
        leave_lists = []
        remove_lists = []
        for each_ai_res_json in ai_res_json: 
          if each_ai_res_json["command"] == "add":
            for i in range(len(each_ai_res_json['date'])):
              date_fill = each_ai_res_json['date'][i].split(' ')[0]
              is_day_leave_full = each_ai_res_json['date'][i].split(' ')[1]
              if (each_ai_res_json['date'][i].split(' ')[0] in ["to_day", "today", "วันนี้"]):
                today = datetime.date.today()
                buddhist_year = today.year + 543
                buddhist_date = today.strftime(f"%d/%m/{buddhist_year}")
                date_fill = buddhist_date
              elif (each_ai_res_json['date'][i].split(' ')[0] != "wrong"): 
                date_fill = each_ai_res_json['date'][i].split(' ')[0]
              else:
                leave_lists = []
                break
              leave_lists.append({
                "date_request": (datetime.datetime.fromtimestamp(float(data["event"]["ts"]))).strftime("%d/%m/%Y %H:%M:%S"),
                "user_id": user_id,
                "user_real_name": user_info["real_name"],
                "user_display_name" : user_info["display_name"],
                "leave_type": each_ai_res_json['leave_type'],
                "date": date_fill,
                "is_full_leave": is_day_leave_full,
                "detail" : text,
                "slack_url": slack_url
              })
          elif each_ai_res_json["command"] == "cancel":
            for i in range(len(each_ai_res_json['date'])):
              date_fill = each_ai_res_json['date'][i].split(' ')[0]
              if (each_ai_res_json['date'][i].split(' ')[0] in ["to_day", "today", "วันนี้"]):
                today = datetime.date.today()
                buddhist_year = today.year + 543
                buddhist_date = today.strftime(f"%d/%m/{buddhist_year}")
                date_fill = buddhist_date
              elif (each_ai_res_json['date'][i].split(' ')[0] != "wrong"): 
                date_fill = each_ai_res_json['date'][i].split(' ')[0]
              else:
                break
              today = datetime.date.today()
              buddhist_year = today.year + 543
              today_buddhist_date = today.strftime(f"%d/%m/{buddhist_year}")
              today_date = datetime.datetime.strptime(today_buddhist_date, "%d/%m/%Y").date()

              if datetime.datetime.strptime(date_fill, "%d/%m/%Y").date() > today_date:
                remove_lists.append({
                  "date": date_fill
                })
                try:
                  remove_row("พนักงานลาประจำเดือน", user_id, date_fill)
                except:
                  reply_to_thread(channel_id, thread_ts, "คําขอล้มเหลว")
        leave_lists = exclude_exist_date_in_request(leave_lists, search_by_user_id("พนักงานลาประจำเดือน", user_id), channel_id, thread_ts)
        leave_lists = exclude_before_today(leave_lists, channel_id, thread_ts)
        year = int(date_fill.split('/')[2])
        if leave_lists != []:
          date_leave = ""
          for leave_list in leave_lists:
            date_leave = date_leave + ", "+ leave_list["date"]
          reply_to_thread(channel_id, thread_ts, "คุณได้ส่งคําขอลาในวันที่ "+ date_leave)
          try:
            print("https://docs.google.com/spreadsheets/d/"+store_leave_lists("พนักงานลาประจำเดือน", str(year), leave_lists))
          except:
            reply_to_thread(channel_id, thread_ts, "คําขอล้มเหลว")
        if remove_lists != []:
          date_remove = ""
          for remove_list in remove_lists:
            date_remove = date_remove + ", "+ remove_list["date"]
          reply_to_thread(channel_id, thread_ts, "คุณได้ส่งคํายกเลิกลาในวันที่ "+ date_remove)

    return jsonify({"status": "ok"})

@app.route("/slack/commands/leave-condition", methods=["POST"])
def leave_condition():
    if request.form.get("token") != SLACK_VERIFICATION_TOKEN:
        return jsonify({"error": "Invalid request token"}), 403

    response_text = f"""             เงื่อนไขการลาหยุด
   ลาป่วยได้ 30 วัน/ปี :pill: 
    *มากกว่า 2 วันขึ้นไป ต้องมีใบรับรองแพทย์
   ลากิจ        5 วัน/ปี :luggage: [ผ่านโปรเท่านั้น] 
    *ต้องแจ้งล่วงหน้าอย่างน้อย 2 วันขึ้นไป
   ลาพักร้อน  6 วัน/ปี :airplane: 
    *เมื่อทำงานครบ 2 เดือน สามารถลาได้ 1 วัน
      [ไม่ผ่านโปร] แจ้งล่วงหน้า 45 วัน
      [  ผ่านโปร  ] แจ้งล่วงหน้า 30 วัน
    """ 
    return jsonify({
        "response_type": "in_channel",  
        "text": response_text
    })

if __name__ == '__main__':
    app.run(port=3000)
