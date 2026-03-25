"""
tipper.py - مكتبة Python مستوحاة من TipperJS v2.4.2 (Lovense Cam API)
=======================================================================
pip install cryptography requests python-socketio[asyncio] aiohttp
"""

import base64, hashlib, json, logging, random, string, time
from datetime import datetime
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives import padding as crypto_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logging.basicConfig(level=logging.INFO, format="[TipperPy] %(levelname)s: %(message)s")
logger = logging.getLogger("TipperPy")

# ─── مفاتيح AES (من الكود الأصلي) ───────────────────────────────────────────
DEFAULT_AES_KEY  = "3d4ab4b0bb42dd54"   # المفتاح الافتراضي
DEFAULT_AES_IV   = "262ef81aafa7a095"   # IV الافتراضي
SESSION_SIGN_KEY = "4c2fa85789f62631"   # VUE_APP_APP_ID
SESSION_SIGN_IV  = "3A3F54FB6999345E"   # VUE_APP_APP_SECRECT
LOG_TOKEN_KEY    = "92838c406060032b"   # مفتاح رمز السجلات
LOG_TOKEN_IV     = "516D943DC18A89D1"   # IV رمز السجلات


# ══════════════════════════════════════════════════════════════════════════════
# القسم 1: AES-CBC الحقيقي
# المصدر: function b(t,e,n) في tipper.js — كان خطأ استخدام MD5 فقط
# ══════════════════════════════════════════════════════════════════════════════

def aes_encrypt(text: str, key: str = DEFAULT_AES_KEY, iv: str = DEFAULT_AES_IV) -> str:
    """
    AES-128-CBC + PKCS7 → Base64
    المصدر: CryptoJS.AES.encrypt(text, key, {iv, mode:CBC, padding:Pkcs7}).toString()
    """
    key_b  = key.encode("utf-8")
    iv_b   = iv.encode("utf-8")
    padder = crypto_padding.PKCS7(128).padder()
    padded = padder.update(text.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key_b), modes.CBC(iv_b))
    enc    = cipher.encryptor()
    ct     = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("utf-8")


def aes_decrypt(ct_b64: str, key: str = DEFAULT_AES_KEY, iv: str = DEFAULT_AES_IV) -> str:
    """
    Base64 → AES-128-CBC decrypt
    المصدر: CryptoJS.AES.decrypt(ct, key, {iv, mode:CBC, padding:Pkcs7})
    """
    key_b    = key.encode("utf-8")
    iv_b     = iv.encode("utf-8")
    ct       = base64.b64decode(ct_b64)
    cipher   = Cipher(algorithms.AES(key_b), modes.CBC(iv_b))
    dec      = cipher.decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = crypto_padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def md5(text: str) -> str:
    """MD5 hex. المصدر: y() = md5() في tipper.js"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# القسم 2: التوقيعات الصحيحة
# ══════════════════════════════════════════════════════════════════════════════

def generate_init_signature(pf: str, model_name: str, customer_name: str,
                             ver: str = "0.0.1") -> str:
    """
    توقيع /ws/customer/init — الخوارزمية الصحيحة.

    المصدر: function ce(config) في tipper.js:
        e = customerName + "##" + pf + "##" + modelName
        n = md5("displayPanel-display-#-" + ver + "-#-" + e) + "-#-" + Date.now()
        config.signature = jsEncrypt(n, APP_ID, APP_SECRET)
    """
    e       = f"{customer_name}##{pf}##{model_name}"
    content = f"displayPanel-display-#-{ver}-#-{e}"
    hashed  = md5(content)
    ts      = int(time.time() * 1000)
    n       = f"{hashed}-#-{ts}"
    return aes_encrypt(n, SESSION_SIGN_KEY, SESSION_SIGN_IV)


def generate_log_token_signature(ip: str) -> str:
    """
    توقيع /coll-log/genLogToken.

    المصدر: genSendToken() في TipperLog بـ tipper.js:
        r = jsEncrypt(md5("cam_tipper-js-#-1.0.0-#-collLog@" + ip) + "-#-" + Date.now(),
                      LOG_KEY, LOG_IV)
    """
    text = md5(f"cam_tipper-js-#-1.0.0-#-collLog@{ip}")
    ts   = int(time.time() * 1000)
    n    = f"{text}-#-{ts}"
    return aes_encrypt(n, LOG_TOKEN_KEY, LOG_TOKEN_IV)


# ══════════════════════════════════════════════════════════════════════════════
# القسم 3: أدوات مساعدة
# ══════════════════════════════════════════════════════════════════════════════

def random_code(length: int = 16, include_special: bool = False) -> str:
    chars = string.digits + string.ascii_letters
    if include_special: chars += "@#$%&*-_"
    return "".join(random.choice(chars) for _ in range(length))

def generate_session_id() -> str:
    return "-".join(random_code(6) for _ in range(4))

def format_seconds(total: int) -> str:
    s = int(total); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    parts = ([f"{h:02d}"] if h else []) + [f"{m:02d}", f"{s:02d}"]
    return ":".join(parts)

def deep_clone(obj: Any, defaults: dict = None) -> Any:
    try:
        c = json.loads(json.dumps(obj))
        if isinstance(c, dict) and defaults:
            for k, v in defaults.items():
                if k not in c: c[k] = v
        return c
    except Exception as e:
        logger.error(f"deep_clone: {e}"); return obj

def is_mobile_user_agent(ua: str = "") -> bool:
    u = ua.lower()
    return any(k in u for k in ["iphone","ipod","ios","android"]) and "ipad" not in u

def convert_sex_machine_name(name: str) -> str:
    n = name.lower()
    if n == "lovense sex machine":      return "xmachine"
    if n == "lovense mini sex machine": return "mini xmachine"
    if n == "h01":                      return "solace"
    return "".join(c for c in n if not c.isdigit())


# ══════════════════════════════════════════════════════════════════════════════
# القسم 4: Socket Events
# المصدر: module 8069 في tipper.js
# ══════════════════════════════════════════════════════════════════════════════

class SocketEvents:
    START_CONTROL_LINK_SS            = "start_control_link_ss"
    CONTROL_LINK_WAIT_TIME_NOTICE_CS = "control_link_wait_time_notice_cs"
    REMOTE_SCAN_MODEL_CONTROL        = "remote_scan_model_control"
    EXIT_CONTROL_MODEL               = "exit_control_model"
    CONTROL_MODEL_DISCONNECT         = "control_model_disconnect"
    CONTROL_TYPE_SWITCH_CS           = "control_type_switch_cs"
    CONTROL_TOY_CMD_CS               = "control_toy_cmd_cs"
    START_CONTROL_LINK_CS            = "start_control_link_cs"
    CONTROL_LINK_TOY_STATUS          = "control_link_toy_status"
    END_BROADCAST_NOTICE_CS          = "end_broadcast_notice_cs"
    END_CONTROL_LINK_NOTICE_CS       = "end_control_link_notice_cs"
    CONTROL_LINK_INFO_NOTICE_CS      = "control_link_info_notice_cs"
    CONTROL_LINK_IN_QUEUE_NOTICE_CS  = "control_link_in_queue_notice_cs"
    CONTROL_LINK_READY_NOTICE_CS     = "control_link_ready_notice_cs"
    GET_MODEL_TIP_SETT_SS            = "get_model_tip_sett_ss"
    GET_DEVELOPER_PANEL_SETT_CS      = "get_developer_panel_sett_cs"
    GET_MODEL_TIP_SETT_CS            = "get_model_tip_sett_cs"
    GET_CONTROL_LINK_CS              = "get_control_link_cs"
    RECONNECT_FAILED                 = "reconnect_failed"
    DEVELOPER_PANEL_SETT_SS          = "developer_panel_sett_ss"
    DISCONNECT                       = "disconnect"
    RECONNECT_ATTEMPT                = "reconnect_attempt"
    MODEL_CLEAR_QUEUE_NOTICE_CS      = "model_clear_queue_notice_cs"
    TIPPER_END_CONTROL_CS            = "tipper_end_control_cs"
    TIPPER_END_CONTROL_NOTICE_CS     = "tipper_end_control_notice_cs"

    @classmethod
    def all(cls) -> dict:
        return {k: v for k, v in vars(cls).items()
                if not k.startswith("_") and isinstance(v, str)}


# ══════════════════════════════════════════════════════════════════════════════
# القسم 5: VibrateWithMe
# ══════════════════════════════════════════════════════════════════════════════

class VibrateStatus:
    INACTIVE=0; ACTIVE=1; WAITING_QR=2; CONNECTED=3

class VibrateEvent:
    def __init__(self, t, d):
        self.type=t; self.data=d; self.timestamp=datetime.now().isoformat()
    def __repr__(self): return f"VibrateEvent(type={self.type}, data={self.data})"

class VibrateWithMe:
    def __init__(self):
        self._pf=self._model=self._customer=""; self._active=False; self._cbs=[]

    def init(self, pf, model, customer):
        _validate(pf, model, customer)
        self._pf, self._model, self._customer, self._active = pf, model, customer, True
        logger.info(f"VibrateWithMe: {pf}/{model}/{customer}")

    def destroy(self): self._active=False; self._cbs.clear()
    def add_listener(self, cb): self._cbs=[cb]

    def _emit(self, t, d):
        e = VibrateEvent(t, d)
        for cb in self._cbs:
            try: cb(e)
            except Exception as ex: logger.error(f"cb error: {ex}")

    def simulate_enable_event(self, enable, model_name="", goal_token=0, toy_type=""):
        self._emit("vibeWithMeEnable",{"enable":enable,"modelName":model_name,"type":toy_type,"goalToken":goal_token})

    def simulate_status_event(self, status, code="", qr_code="", model_name=""):
        self._emit("vibeWithMeStatus",{"status":status,"code":code,"qrCode":qr_code,"modelName":model_name})

    def simulate_tip_event(self, model_name, received_token, goal_token, customer_name=""):
        self._emit("vibeWithMeTip",{"modelName":model_name,"receivedToken":received_token,"goalToken":goal_token,"customer":customer_name})

    @property
    def is_active(self): return self._active


# ══════════════════════════════════════════════════════════════════════════════
# القسم 6: SessionData
# ══════════════════════════════════════════════════════════════════════════════

def _validate(pf, model, customer):
    if not pf or not pf.strip():       raise ValueError("invalid platform")
    if not model or not model.strip(): raise ValueError("invalid model name")
    if not customer or not customer.strip(): raise ValueError("invalid customer name")

class SessionData:
    def __init__(self):
        self.user_data={}; self.panel_switch=False; self.control_switch=True
        self.tip_switch=False; self.auto_start=False
        self.platform_data={}; self.session_id=generate_session_id()

    def set_platform(self, pf, model, customer, **extra):
        self.platform_data={"pf":pf,"modelName":model,"customerName":customer,
                            "modName":model,"csName":customer,**extra}

    def update_panel(self, data):
        self.panel_switch=data.get("panelSwitch",False)
        self.control_switch=data.get("controlSwitch",True)
        self.tip_switch=data.get("tipSwitch",False)


# ══════════════════════════════════════════════════════════════════════════════
# القسم 7: LovenseTipperAPI
# ══════════════════════════════════════════════════════════════════════════════

_singleton: Optional["LovenseTipperAPI"] = None

class LovenseTipperAPI:
    VERSION          = "2.4.2"
    API_BASE         = "https://apps.lovense-api.com"
    DISPLAY_BASE     = "https://display.lovense-api.com"
    INIT_ENDPOINT    = "/ws/customer/init"
    STATUS_ENDPOINT  = "/api/customer/checkModelStatus"

    def __init__(self):
        self._pf=self._model=self._customer=self._container=""
        self._initialized=self._initializing=False
        self._listeners=[]; self._settings={}
        self._session=SessionData(); self.vibrate_with_me=VibrateWithMe()
        logger.info(f"LovenseTipperAPI v{self.VERSION} loaded")

    def init(self, platform, model_name, customer_name, options=None):
        """تهيئة الجلسة. المصدر: window.Lovense.init()"""
        if options is None: options={}
        _validate(platform, model_name, customer_name)
        if self._is_same(platform, model_name, customer_name):
            logger.info("Same session — skip"); return
        self._pf,self._model,self._customer = platform,model_name,customer_name
        self._session.set_platform(platform, model_name, customer_name)
        if options.get("container"):   self._container=options["container"]
        if "auto_start" in options:    self._session.auto_start=bool(options["auto_start"])
        self._initialized=True
        self._emit("lovense_init",{"status":True,"platform":platform,
                                   "modelName":model_name,"customerName":customer_name})
        logger.info(f"Lovense initialized: {platform}/{model_name}/{customer_name}")

    def destroy(self):
        self._initialized=False; logger.info("Lovense destroyed")

    def destroy_all(self):
        self.__init__(); logger.info("Lovense full reset")

    def end_control(self):
        self._emit("lovenseEvent",{"type":"tipControl","status":"endGiveControl"})

    def add_message_listener(self, cb: Callable):
        """يستبدل المستمع القديم. المصدر: window.Lovense.addMessageListener()"""
        self._listeners=[cb]

    def get_settings(self) -> dict: return dict(self._settings)

    def build_init_payload(self) -> dict:
        """
        الـ payload الكامل لـ POST /ws/customer/init بالتوقيع الصحيح.
        المصدر: xe() في tipper.js → يستخدم ce() لتوليد signature بـ AES-CBC
        """
        return {
            "pf":           self._pf,
            "modelName":    self._model,
            "customerName": self._customer,
            "ver":          "0.0.1",
            "signature":    generate_init_signature(self._pf, self._model, self._customer),
        }

    def build_socket_url(self, ws_url: str) -> str:
        """المصدر: Ce() — يستبدل lovense.com بـ lovense-api.com"""
        return ws_url.replace("lovense.com", "lovense-api.com")

    def _emit(self, t, p):
        e={"type":t,"payload":p}
        for cb in self._listeners:
            try: cb(e)
            except Exception as ex: logger.error(f"Listener: {ex}")

    def _is_same(self, pf, m, c):
        return self._initialized and self._pf==pf and self._model==m and self._customer==c

    @property
    def is_initialized(self): return self._initialized
    @property
    def platform(self): return self._pf
    @property
    def model_name(self): return self._model
    @property
    def customer_name(self): return self._customer
    @property
    def session(self): return self._session


def get_lovense() -> LovenseTipperAPI:
    global _singleton
    if _singleton is None: _singleton=LovenseTipperAPI()
    return _singleton


# ══════════════════════════════════════════════════════════════════════════════
# القسم 8: Backoff
# ══════════════════════════════════════════════════════════════════════════════

class Backoff:
    def __init__(self, min_ms=100, max_ms=10000, factor=2.0, jitter=0.0):
        self.min_ms=min_ms; self.max_ms=max_ms
        self.factor=factor; self.jitter=max(0.0,min(1.0,jitter)); self.attempts=0

    def duration(self) -> int:
        ms=self.min_ms*(self.factor**self.attempts); self.attempts+=1
        if self.jitter>0:
            r=random.random(); d=int(r*self.jitter*ms)
            ms=ms+d if int(r*10)%2 else ms-d
        return int(min(ms,self.max_ms))

    def reset(self): self.attempts=0
    def set_min(self,v): self.min_ms=v
    def set_max(self,v): self.max_ms=v
    def set_jitter(self,v): self.jitter=max(0.0,min(1.0,v))


# ══════════════════════════════════════════════════════════════════════════════
# القسم 9: TipperLog
# ══════════════════════════════════════════════════════════════════════════════

class TipperLog:
    MAX_CACHE=30
    def __init__(self):
        self.session_id=generate_session_id(); self._list=[]; self._init={}
        self._sign=""; self._ts=0; logger.info(f"TipperLog: {self.session_id}")

    def set_init_data(self,d): self._init=dict(d)

    def add_log(self, content, log_no="T0000", log_type="system", detail=None):
        if len(self._list)>=self.MAX_CACHE: return
        sign=(log_no+content)[:30]; now=int(time.time()*1000)
        if self._sign==sign and now-self._ts<300: return
        self._sign=sign; self._ts=now
        self._list.append({"sessionId":self.session_id,"content":content,
            "logNo":log_no,"logType":log_type,
            "detail":json.dumps({"initConfig":self._init,**(detail or {})}),
            "localTime":datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "timestamp":now,"timezone":-time.timezone//3600})

    def get_pending_logs(self): return list(self._list)
    def clear_sent(self):
        if self._list: self._list.pop(0)
    def clear_all(self): self._list.clear()
    @property
    def pending_count(self): return len(self._list)


# ══════════════════════════════════════════════════════════════════════════════
# القسم 10: كتالوج الأجهزة
# ══════════════════════════════════════════════════════════════════════════════

TOY_CATALOG=[
    {"type":"Ferri",   "symbol":["x"],          "desc":"ألعاب ارتداء"},
    {"type":"Hush",    "symbol":["z"],           "desc":"ألعاب مقعدية"},
    {"type":"Gush",    "symbol":["ed"],          "desc":"G-spot"},
    {"type":"Max",     "symbol":["toyb","b"],    "desc":"جهاز ذكوري"},
    {"type":"Domi",    "symbol":["w"],           "desc":"مدلكة"},
    {"type":"Calor",   "symbol":["t"],           "desc":"تدفئة"},
    {"type":"Ambi",    "symbol":["l"],           "desc":"محفز قيثورة"},
    {"type":"Osci",    "symbol":["o"],           "desc":"تذبذب"},
    {"type":"Diamo",   "symbol":["r"],           "desc":"محفز أصابع"},
    {"type":"Dolce",   "symbol":["j"],           "desc":"قيثورة صغير"},
    {"type":"Nora",    "symbol":["toya","a"],    "desc":"ثنائي المحرك"},
    {"type":"Lush",    "symbol":["s"],           "desc":"البيضة الاهتزازية"},
    {"type":"Mission", "symbol":["v"],           "desc":"جهاز سرج"},
    {"type":"Edge",    "symbol":["p"],           "desc":"البروستات"},
    {"type":"XMachine","symbol":["f"],           "desc":"آلة جنسية"},
    {"type":"Hyphy",   "symbol":["eb"],          "desc":"جهاز مزدوج"},
]

def get_toy_by_symbol(sym: str) -> Optional[dict]:
    return next((t for t in TOY_CATALOG if sym in t["symbol"]), None)

def get_all_toy_types() -> list:
    return [t["type"] for t in TOY_CATALOG]


# ══════════════════════════════════════════════════════════════════════════════
# Self-test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"TipperPy v{LovenseTipperAPI.VERSION} — self test\n")

    # AES round-trip
    enc = aes_encrypt("hello world")
    dec = aes_decrypt(enc)
    assert dec == "hello world", "AES failed"
    print(f"✅ AES-CBC: 'hello world' → '{enc[:30]}...' → '{dec}'")

    # توقيع الجلسة
    sig = generate_init_signature("chaturbate", "model_alice", "viewer_bob")
    assert len(sig) > 20
    print(f"✅ Init signature: {sig[:45]}...")

    # API + payload
    api = get_lovense()
    api.add_message_listener(lambda e: None)
    api.init("chaturbate", "model_alice", "viewer_bob")
    p = api.build_init_payload()
    assert p["signature"] and p["pf"] == "chaturbate"
    print(f"✅ Payload: pf={p['pf']}, sig={p['signature'][:30]}...")

    print("\n✅ All tests passed!")
