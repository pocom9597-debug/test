"""
Lovense Cam Tipper - Python Control API
========================================
مستخرج من tipper.js - نفس المنطق بالظبط

التدفق:
1. POST /ws/customer/init  → يرجع ws_server_url + userData
2. Socket.IO على ws_server_url (path=/customer)
3. on connect → emit DispannelModelOnlineDTO
4. on modelOnline → emit VibeWithMeConfigDTO + VibeCustomerVibeStatusDTO
5. on showVibeWithMeQrCode → QR Code جاهز للمسح
"""

import hashlib
import time
import requests
import socketio
import threading
from base64 import b64encode, b64decode

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


# ────────────────────────────────────────────────
# الإعدادات - غيّر القيم دي
# ────────────────────────────────────────────────
APP_ID     = "4c2fa85789f62631"   # من tipper.js
APP_SECRET = "3A3F54FB6999345E"   # من tipper.js
BASE_URL   = "https://display.lovense-api.com"

PLATFORM      = "cam"        # pf
MODEL_NAME    = ""           # اسم الموديل (مطلوب)
CUSTOMER_NAME = ""           # اسم العميل (مطلوب)
VERSION       = "0.0.1"


# ────────────────────────────────────────────────
# AES-CBC Encrypt/Decrypt (نفس jsEncrypt في tipper.js)
# ────────────────────────────────────────────────
def js_encrypt(text: str, key: str, iv: str) -> str:
    """AES-CBC encrypt - نفس CryptoJS.AES.encrypt"""
    key_bytes = key.encode("utf-8")
    iv_bytes  = iv.encode("utf-8")
    txt_bytes = text.encode("utf-8")
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    encrypted = cipher.encrypt(pad(txt_bytes, AES.block_size))
    # CryptoJS يرجع Base64
    return b64encode(encrypted).decode("utf-8")


def js_decrypt(ciphertext: str, key: str, iv: str) -> str:
    """AES-CBC decrypt"""
    key_bytes  = key.encode("utf-8")
    iv_bytes   = iv.encode("utf-8")
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    decrypted = cipher.decrypt(b64decode(ciphertext))
    return unpad(decrypted, AES.block_size).decode("utf-8")


# ────────────────────────────────────────────────
# توليد الـ Signature (نفس function ce في tipper.js)
# ────────────────────────────────────────────────
def generate_signature(pf: str, model_name: str, customer_name: str, ver: str = VERSION) -> str:
    """
    من tipper.js:
        e = customerName + "##" + pf + "##" + modelName
        n = md5("displayPanel-display-#-" + ver + "-#-" + e) + "-#-" + Date.now()
        signature = jsEncrypt(n, appId, appSecret)
    """
    combined = f"{customer_name}##{pf}##{model_name}"
    raw = f"displayPanel-display-#-{ver}-#-{combined}"
    md5_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    timestamp = int(time.time() * 1000)
    to_encrypt = f"{md5_hash}-#-{timestamp}"
    return js_encrypt(to_encrypt, APP_ID, APP_SECRET)


# ────────────────────────────────────────────────
# الخطوة 1: REST init → يرجع ws_server_url
# ────────────────────────────────────────────────
def customer_init(pf: str, model_name: str, customer_name: str) -> dict:
    """
    POST /ws/customer/init
    يرجع: { ws_server_url, code, qrCode, ... }
    """
    signature = generate_signature(pf, model_name, customer_name)
    payload = {
        "pf":           pf,
        "modelName":    model_name,
        "customerName": customer_name,
        "ver":          VERSION,
        "signature":    signature,
    }
    resp = requests.post(
        f"{BASE_URL}/ws/customer/init",
        data=payload,
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"Init failed: {data}")
    return data["data"]


# ────────────────────────────────────────────────
# الخطوة 2+: Socket.IO
# ────────────────────────────────────────────────
class LovenseSession:
    def __init__(self, pf: str, model_name: str, customer_name: str):
        self.pf            = pf
        self.model_name    = model_name
        self.customer_name = customer_name
        self.user_data     = {}
        self.sio           = socketio.Client(logger=False, engineio_logger=False)
        self._connected    = False
        self._qr_event     = threading.Event()
        self.qr_code       = None
        self._setup_events()

    def _random_ack_id(self) -> str:
        import random, string
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

    def _setup_events(self):
        sio = self.sio

        @sio.event
        def connect():
            print("✅ Socket متصل")
            self._connected = True
            # emit DispannelModelOnlineDTO
            ack_id = self._random_ack_id()
            sio.emit(
                "DispannelModelOnlineDTO",
                {"ackId": ack_id},
                callback=self._on_model_online
            )

        @sio.event
        def disconnect():
            print("🔌 Socket انقطع")
            self._connected = False

        @sio.event
        def connect_error(data):
            print(f"❌ خطأ في الاتصال: {data}")

        @sio.on("showVibeWithMeQrCode")
        def on_qr_code(data):
            if isinstance(data, str):
                import json
                data = json.loads(data)
            print("🎯 استلمنا QR Code!")
            self.qr_code = data
            self._qr_event.set()

        @sio.on("VibeCloseEventDTO")
        def on_vibe_close(data):
            print("📴 VibeWithMe أُغلق")

        @sio.on("VibeWithMeOnRemoteEvent")
        def on_remote_event(data):
            print(f"📡 Remote Event: {data}")

        @sio.on("tipperjs_notify_exec_tip_tc")
        def on_tip(data):
            if isinstance(data, str):
                import json
                data = json.loads(data)
            print(f"💰 Tip جديد: {data}")

        @sio.on("tipperjs_notify_send_online_heartbeat_tc")
        def on_heartbeat_ack(data):
            # رد على heartbeat request
            sio.emit("tipperjs_viewer_online_heartbeat_ts")

    def _on_model_online(self, response):
        """Callback بعد DispannelModelOnlineDTO"""
        if isinstance(response, str):
            import json
            response = json.loads(response)
        print(f"📊 Model Online Response: {response}")
        if response and response.get("modelOnline"):
            print("✅ الموديل أونلاين - طلب VibeWithMe config...")
            self._request_vibe_config()
        else:
            print("⚠️ الموديل مش أونلاين حالياً")

    def _request_vibe_config(self):
        """emit VibeWithMeConfigDTO + VibeCustomerVibeStatusDTO"""
        self.sio.emit(
            "VibeWithMeConfigDTO",
            {"ackId": self._random_ack_id()},
            callback=lambda r: print(f"VibeWithMeConfig: {r}")
        )
        self.sio.emit(
            "VibeCustomerVibeStatusDTO",
            {"ackId": self._random_ack_id()},
            callback=lambda r: print(f"CustomerVibeStatus: {r}")
        )

    def connect(self, ws_server_url: str):
        """اتصل بالسيرفر"""
        # نفس tipper.js: replace lovense.com → lovense-api.com
        url = ws_server_url.replace("lovense.com", "lovense-api.com")
        print(f"🌐 جاري الاتصال بـ: {url}")
        self.sio.connect(
            url,
            socketio_path="/customer",
            transports=["websocket"]
        )

    def wait_for_qr(self, timeout: int = 30) -> dict | None:
        """انتظر QR Code"""
        self._qr_event.wait(timeout=timeout)
        return self.qr_code

    def disconnect(self):
        if self._connected:
            self.sio.disconnect()

    def end_control(self):
        """إيقاف التحكم"""
        if self._connected:
            self.sio.emit("tipper_end_control_cs")
            print("🛑 تم إيقاف التحكم")


# ────────────────────────────────────────────────
# الدالة الرئيسية للاختبار
# ────────────────────────────────────────────────
def run(platform: str, model_name: str, customer_name: str):
    print(f"🚀 بدء الاتصال - Platform: {platform} | Model: {model_name} | Customer: {customer_name}")

    # الخطوة 1: init REST
    print("\n📡 الخطوة 1: REST Init...")
    user_data = customer_init(platform, model_name, customer_name)
    print(f"✅ Init ناجح: {user_data}")

    ws_url = user_data.get("ws_server_url")
    if not ws_url:
        raise Exception("لم نستلم ws_server_url من السيرفر")

    # الخطوة 2: Socket.IO
    print("\n📡 الخطوة 2: Socket.IO...")
    session = LovenseSession(platform, model_name, customer_name)
    session.user_data = user_data
    session.connect(ws_url)

    # انتظر QR Code
    print("\n⏳ انتظار QR Code (30 ثانية)...")
    qr = session.wait_for_qr(timeout=30)

    if qr:
        print(f"\n🎉 QR Code جاهز!")
        print(f"   Code: {qr.get('code', 'N/A')}")
        print(f"   qrCode: {qr.get('qrCode', 'N/A')}")
    else:
        print("\n⚠️ لم يصل QR Code في الوقت المحدد")

    # اشغّل في الخلفية
    try:
        session.sio.wait()
    except KeyboardInterrupt:
        print("\n👋 إيقاف...")
        session.disconnect()

    return session


if __name__ == "__main__":
    # ← غيّر القيم دي
    PLATFORM      = "cam"
    MODEL_NAME    = "your_model_name"    # ← اسم الموديل
    CUSTOMER_NAME = "your_customer_name" # ← اسم العميل

    run(PLATFORM, MODEL_NAME, CUSTOMER_NAME)
