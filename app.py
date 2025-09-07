import random
import json
import threading
import time
import requests
from flask import Flask

app = Flask(__name__)

url = "https://www.ttboost.app/api/coins/check"

responses = []
headers = {
  'User-Agent': "ktor-client",
  'Accept': "application/json",
  'Accept-Encoding': "gzip",
  'Content-Type': "application/json",
  'authorization': "Bearer 637808|TDGqqu8tmBafB9B6ayFZv7RDdXZDtb9MpDQHdQHO6d135f9e",
  'accept-charset': "UTF-8"
}

def background_worker():
    while True:
        payload = {
        "order_id": random.randint(1000000, 1899999),
        "username": "jdhddjdjhdn",
        "likes_incremented": None,
        "profile_username": "777mrmydohe",
        "sec_uid":"MS4wLjABAAAA4o1aH5nuax0I6UMEhAnb2bxT8FOwqaooWdieB1sSV1ZA0b5w7Yd_FIDn0rruKzv7"
        }
        try:
            res = requests.post(url, data=json.dumps(payload), headers=headers).json()
            if res["success"]==True:
                responses.append("done add coins : "+str(res["data"]["coins_earned"]))
            else:
                responses.append("error add")
        except Exception as e:
            responses.append({"error": str(e)})
@app.route("/")
def home():
    if responses:
        # رجّع آخر رد
        return f"<h3>آخر رد:</h3><pre>{json.dumps(responses[-1], indent=2)}</pre>"
    else:
        return "مافيش ردود لسه ⏳"

if __name__ == "__main__":
    background_worker()
    app.run(host="0.0.0.0", port=8080)
