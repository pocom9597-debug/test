import asyncio
import threading
from flask import Flask

app = Flask(__name__)

# ---- background task ----
async def background_task():
    while True:
        print("✅ Worker is running in background...")
        await asyncio.sleep(5)

def start_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(background_task())

# ---- web endpoint ----
@app.route("/")
def home():
    return "Hello from Flask + Background Worker ✅"

# ---- start both ----
if __name__ == "__main__":
    # شغل الـ worker في thread منفصل
    t = threading.Thread(target=start_worker, daemon=True)
    t.start()

    # شغل Flask
    app.run(host="0.0.0.0", port=8080)
