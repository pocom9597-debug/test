import random
import json
import threading
import time
import requests
import asyncio
import aiohttp
from flask import Flask

app = Flask(__name__)

url = "https://www.ttboost.app/api/coins/check"

headers = {
  'User-Agent': "ktor-client",
  'Accept': "application/json",
  'Accept-Encoding': "gzip",
  'Content-Type': "application/json",
  'authorization': "Bearer 637808|TDGqqu8tmBafB9B6ayFZv7RDdXZDtb9MpDQHdQHO6d135f9e",
  'accept-charset': "UTF-8"
}

# Define the number of requests to send concurrently (at the same time)
# You can increase this number for higher speeds, but be mindful of the server's limits.
BATCH_SIZE = 1000

async def send_single_request(session):
    """
    Sends a single asynchronous POST request.
    This function is designed to be run concurrently with others.
    """
    payload = {
        "order_id": random.randint(1000000, 1899999),
        "username": "fhbidudddhd",
        "likes_incremented": None,
        "profile_username": "mhmdanor327",
        "sec_uid": "MS4wLjABAAAA4o1aH5nuax0I6UMEhAnb2bxT8FOwqaooWdieB1sSV1ZA0b5w7Yd_FIDn0rruKzv7"
    }

    try:
        async with session.post(url, json=payload, headers=headers) as response:
            res = await response.json()
            if res.get("success"):
                coins_earned = res.get("data", {}).get("coins_earned")
                if coins_earned is not None:
                    print(f"done add coins: {coins_earned}")
                else:
                    print("done add coins: No coins earned data")
            else:
                print("error add")
    except (aiohttp.ClientError, json.JSONDecodeError) as e:
        print(f"Request failed: {e}")

async def main():
    """
    The main asynchronous loop to continuously send batches of requests.
    """
    async with aiohttp.ClientSession() as session:
        while True:
            # Create a list of tasks to be executed concurrently
            tasks = [send_single_request(session) for _ in range(BATCH_SIZE)]
            
            # Use asyncio.gather() to run all tasks in parallel
            await asyncio.gather(*tasks)
            
            # Optional: Add a small delay between batches to manage server load
            # await asyncio.sleep(0.01)

@app.route("/")
def home():
    asyncio.run(main())
    if responses:
        # رجّع آخر رد
        return f"<h3>آخر رد:</h3><pre>{json.dumps(responses[-1], indent=2)}</pre>"
    else:
        return "مافيش ردود لسه ⏳"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
