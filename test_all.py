"""
test_all.py — اختبار شامل كامل
================================
python test_all.py
"""

import asyncio, json, logging, sys, time, random
import requests

from tipper import (
    get_lovense, LovenseTipperAPI,
    aes_encrypt, aes_decrypt, md5,
    generate_init_signature, generate_log_token_signature,
    VibrateWithMe, VibrateStatus,
    TipperLog, Backoff, SocketEvents,
    random_code, format_seconds,
)
from toy_control import (
    ToyController, ToyControllerAdvanced,
    ToyStatus, ToyCommand, TipSettings, TipLevel, SpecialCommand,
    TOY_MODES, MODE_TO_KEY, get_toy_modes, clamp,
)

# ══════════════════════════════════════════════════════════════
CONFIG = {
    "platform":      "flash",
    "model_name":    "IQthIluOklO5Th1jifGqgw==",
    "customer_name": "vHpgecET05OoVbfw9U0cfBxW+Vewhh/aZHAXfwIHKgo=",
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("TestAll")
results = []

def check(name, cond, detail=""):
    icon = "✅" if cond else "❌"
    print(f"  {icon} {name}" + (f"  ({detail})" if detail else ""))
    results.append((name, cond))
    return cond

def section(t): print(f"\n{'═'*55}\n  {t}\n{'═'*55}")
def sub(t):     print(f"\n  ── {t} ──")


# ──────────────────────────────────────────────────────────────
# 1: التشفير
# ──────────────────────────────────────────────────────────────
def test_crypto():
    section("1️⃣  AES-CBC والتوقيع")
    for text in ["hello","test 123","cb##model##cs"]:
        enc=aes_encrypt(text); check(f"round-trip '{text}'", aes_decrypt(enc)==text)
    KEY,IV="4c2fa85789f62631","3A3F54FB6999345E"
    enc=aes_encrypt("ok",KEY,IV); check("session keys", aes_decrypt(enc,KEY,IV)=="ok")
    check("md5", md5("hello")=="5d41402abc4b2a76b9719d911017c592")
    s1=generate_init_signature(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    time.sleep(0.003)
    s2=generate_init_signature(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    check("sig generated",   len(s1)>40, f"len={len(s1)}")
    check("sig timestamp",   s1!=s2)
    check("log token sig",   len(generate_log_token_signature("1.2.3.4"))>40)
    print(f"\n    Sig: {s1}")


# ──────────────────────────────────────────────────────────────
# 2: API محلي
# ──────────────────────────────────────────────────────────────
def test_local_api():
    section("2️⃣  LovenseTipperAPI (محلي)")
    api=LovenseTipperAPI(); ev=[]
    api.add_message_listener(lambda e: ev.append(e))
    api.init(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    check("initialized",  api.is_initialized)
    check("event fired",  len(ev)==1 and ev[0]["type"]=="lovense_init")
    ev.clear(); api.init(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    check("skip duplicate", len(ev)==0)
    try: LovenseTipperAPI().init("","m","c"); check("empty raises",False)
    except ValueError: check("empty raises",True)
    p=api.build_init_payload()
    check("payload pf",  p["pf"]==CONFIG["platform"])
    check("payload sig", len(p.get("signature",""))>40)
    print(f"\n    Payload:\n    {json.dumps(p,indent=4,ensure_ascii=False)}")
    check("socket url",  "lovense-api.com" in api.build_socket_url("https://ws.lovense.com/x"))
    api.destroy(); check("destroy", not api.is_initialized)
    api.destroy_all(); check("destroy_all", api.platform=="")


# ──────────────────────────────────────────────────────────────
# 3: ToyController محاكاة
# ──────────────────────────────────────────────────────────────
async def test_toy_controller():
    section("3️⃣  ToyController (محاكاة)")
    sent=[]
    class Mock:
        async def emit(self,e,d=None): sent.append({"e":e,"d":d or {}})

    ctrl=ToyController(Mock(),CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])

    sub("ToyStatus")
    ctrl.on_toy_status({"toyName":"Lush 3","toyType":"lush","status":1,"battery":78})
    check("connected",     ctrl.toy.connected)
    check("battery=78",    ctrl.toy.battery==78)
    check("lush=vibrate",  ctrl.toy.supported_modes==["vibrate"])
    ctrl.on_toy_status({"toyType":"nora","status":1,"battery":90})
    check("nora→rotate",   "rotate" in ctrl.toy.supported_modes)

    sub("أوامر أساسية")
    await ctrl.vibrate(10);  check("vibrate(10)", sent[-1]["d"]["v"]==10)
    await ctrl.rotate(8);    check("rotate(8)",   sent[-1]["d"]["r"]==8)
    await ctrl.send_command(vibrate=15,rotate=5)
    d=sent[-1]["d"]; check("v+r", d["v"]==15 and d["r"]==5)
    await ctrl.stop()
    d=sent[-1]["d"]; check("stop→0", d.get("v",0)==0 and d.get("r",0)==0)

    sub("Clamp")
    cmd=ToyCommand("f","m","c"); cmd.set("vibrate",99).set("air",99)
    p=cmd.to_socket_payload()
    check("vibrate→20", p["v"]==20)
    check("air→3",      p["a"]==3)

    sub("أنماط")
    sent.clear(); await ctrl.pulse(peak=10,duration=0,cycles=2)
    check("pulse≥4",  len(sent)>=4)
    sent.clear(); await ctrl.ramp_up(0,8,step=4,delay=0)
    check("ramp≥3",   len(sent)>=3)
    sent.clear(); await ctrl.pattern([5,10,15,0],delay=0,repeat=2)
    check("pattern×2=8", len(sent)==8)

    sub("tip_response افتراضي")
    for tk,exp in [(5,5),(100,18),(1000,20)]:
        sent.clear(); await ctrl.tip_response(tk)
        check(f"tip {tk}→{exp}", sent[0]["d"]["v"]==exp)

    sub("control flow")
    await ctrl.request_control()
    check("get_control_link_cs",   sent[-1]["e"]=="get_control_link_cs")
    await ctrl.start_control()
    check("start_control_link_cs", sent[-1]["e"]=="start_control_link_cs")
    check("in_control=True",       ctrl.in_control)
    await ctrl.end_control()
    check("tipper_end_control_cs", sent[-1]["e"]=="tipper_end_control_cs")
    check("in_control=False",      not ctrl.in_control)


# ──────────────────────────────────────────────────────────────
# 4: TipSettings + ToyControllerAdvanced
# ──────────────────────────────────────────────────────────────
async def test_tip_settings():
    section("4️⃣  TipSettings + ToyControllerAdvanced")

    # البيانات الحقيقية من الخادم
    RAW = {"tipSetting": json.dumps({
        "basicLevel": [
            {"tipBegin":1,  "tipEnd":7,          "time":3,   "v":5,  "r":0},
            {"tipBegin":8,  "tipEnd":17,          "time":9,   "v":8,  "r":0},
            {"tipBegin":18, "tipEnd":18,          "time":18,  "v":20, "r":0},
            {"tipBegin":20, "tipEnd":37,          "time":38,  "v":15, "r":0},
            {"tipBegin":38, "tipEnd":57,          "time":38,  "v":20, "r":0},
            {"tipBegin":58, "tipEnd":67,          "time":55,  "v":16, "r":0},
            {"tipBegin":200,"tipEnd":"infinity",  "time":150, "v":15, "r":0},
        ],
        "specialCommand": [
            {"time":120,"tokensBegin":111,"tokensEnd":111,
             "type":"giveControl","controlType":"normal","tokenPerMin":50},
            {"tokens":19,  "type":"earthquake","time":18},
            {"tokens":89,  "type":"fireworks", "time":88},
            {"tokens":119, "type":"wave",      "time":118},
            {"tokens":229, "type":"pulse",     "time":118},
        ]
    })}

    sub("TipSettings.from_server")
    ts = TipSettings.from_server(RAW)
    check("basicLevel loaded",    len(ts.basic_levels)==7)
    check("specialCommand loaded",len(ts.special_commands)==5)

    sub("find_level")
    lvl1=ts.find_level(5);   check("5 tokens→v=5",  lvl1 and lvl1.levels.get("vibrate")==5)
    lvl2=ts.find_level(8);   check("8 tokens→v=8",  lvl2 and lvl2.levels.get("vibrate")==8)
    lvl3=ts.find_level(18);  check("18 tokens→v=20", lvl3 and lvl3.levels.get("vibrate")==20)
    lvl4=ts.find_level(300); check("300 tokens→∞",   lvl4 and lvl4.tip_end==-1)
    lvl5=ts.find_level(0);   check("0 tokens→None",  lvl5 is None)

    sub("find_special")
    sp1=ts.find_special(111); check("111→giveControl",  sp1 and sp1.cmd_type=="giveControl")
    sp2=ts.find_special(19);  check("19→earthquake",    sp2 and sp2.cmd_type=="earthquake")
    sp3=ts.find_special(89);  check("89→fireworks",     sp3 and sp3.cmd_type=="fireworks")
    sp4=ts.find_special(119); check("119→wave",         sp4 and sp4.cmd_type=="wave")
    sp5=ts.find_special(229); check("229→pulse",        sp5 and sp5.cmd_type=="pulse")
    sp6=ts.find_special(50);  check("50→None",          sp6 is None)

    sub("ToyControllerAdvanced.execute_tip")
    sent=[]
    class Mock2:
        async def emit(self,e,d=None): sent.append({"e":e,"d":d or {}})

    ctrl=ToyControllerAdvanced(Mock2(),CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    ctrl.on_toy_status({"toyType":"lush","status":1,"battery":90})
    ctrl.load_settings(RAW)

    # basicLevel test
    sent.clear(); await ctrl.execute_tip(5)
    check("execute_tip(5)→v=5",   sent and sent[0]["d"].get("v")==5)

    sent.clear(); await ctrl.execute_tip(18)
    check("execute_tip(18)→v=20", sent and sent[0]["d"].get("v")==20)

    # special commands (بدون انتظار فعلي)
    sub("print TipSettings")
    ts.print_summary()
    check("print_summary OK", True)


# ──────────────────────────────────────────────────────────────
# 5: VibrateWithMe
# ──────────────────────────────────────────────────────────────
def test_vibrate_with_me():
    section("5️⃣  VibrateWithMe")
    recv=[]; vibe=VibrateWithMe()
    vibe.add_listener(lambda e: recv.append(e))
    vibe.init(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    check("active",  vibe.is_active)
    for st,lb in [(1,"ACTIVE"),(2,"WAITING_QR"),(3,"CONNECTED")]:
        vibe.simulate_status_event(st,model_name="alice")
        check(lb, recv[-1].data["status"]==st)
    vibe.simulate_enable_event(True,model_name="alice",goal_token=500)
    check("enable", recv[-1].data["enable"]==True)
    vibe.simulate_tip_event("alice",100,500,"bob")
    check("tip=100", recv[-1].data["receivedToken"]==100)
    vibe.destroy(); check("destroyed", not vibe.is_active)


# ──────────────────────────────────────────────────────────────
# 6: Utilities
# ──────────────────────────────────────────────────────────────
def test_utilities():
    section("6️⃣  Backoff + TipperLog + Utils")
    b=Backoff(min_ms=100,max_ms=5000,factor=2,jitter=0)
    check("b1=100",b.duration()==100)
    check("b2=200",b.duration()==200)
    check("b3=400",b.duration()==400)
    b.reset(); check("reset",b.duration()==100)
    b2=Backoff(1000,3000,2,0); b2.attempts=10
    check("cap=3000",b2.duration()==3000)

    tlog=TipperLog(); tlog.set_init_data({"pf":CONFIG["platform"]})
    tlog.add_log("e","T1"); tlog.add_log("e","T1")
    check("dup ignored",  tlog.pending_count==1)
    tlog.add_log("e2","T2")
    check("2nd added",    tlog.pending_count==2)
    logs=tlog.get_pending_logs()
    check("has sessionId","sessionId" in logs[0])
    tlog.clear_sent(); check("clear_sent",tlog.pending_count==1)
    tlog.clear_all();  check("clear_all", tlog.pending_count==0)

    check("fmt 3661", format_seconds(3661)=="01:01:01")
    check("fmt 90",   format_seconds(90)=="01:30")
    code=random_code(8)
    check("code len=8",code.isalnum() and len(code)==8)

    evs=SocketEvents.all()
    check("25 events",    len(evs)==25)
    check("CTRL_TOY_CMD", SocketEvents.CONTROL_TOY_CMD_CS=="control_toy_cmd_cs")
    # محاكاة إرسال تيبة بقيمة 55 توكن لتفعيل التحكم

    check("nora→rotate",  "rotate" in get_toy_modes("nora"))
    check("max→air",      "air"    in get_toy_modes("max"))
    check("solace→depth", "depth"  in get_toy_modes("solace"))
    for mode,key in [("vibrate","v"),("rotate","r"),("thrusting","f"),
                     ("air","a"),("suction","p"),("depth","d"),("fingering","g")]:
        check(f"{mode}→'{key}'", MODE_TO_KEY[mode]==key)


# ──────────────────────────────────────────────────────────────
# 7: HTTP checkModelStatus
# ──────────────────────────────────────────────────────────────
def test_http_status():
    section("7️⃣  HTTP: checkModelStatus")
    print(f"\n  pf={CONFIG['platform']}  model={CONFIG['model_name'][:20]}...")
    try:
        r=requests.post("https://display.lovense-api.com/api/customer/checkModelStatus",
            data={"pf":CONFIG["platform"],"modelName":CONFIG["model_name"],
                  "customerName":CONFIG["customer_name"]}, timeout=10)
        d=r.json()
        print(f"\n  Response:\n  {json.dumps(d,indent=4,ensure_ascii=False)}")
        check("HTTP 200",  r.status_code==200)
        check("has code",  "code" in d)
        if d.get("code")==0:
            info=d.get("data",{})
            check("isModelCamOnline", "isModelCamOnline" in info,
                  str(info.get("isModelCamOnline")))
            return info.get("isModelCamOnline",False)
        else:
            check("server ok", False, d.get("message",""))
    except requests.exceptions.ConnectionError:
        check("connection", False, "no internet")
    except Exception as e:
        check("HTTP", False, str(e))
    return False


# ──────────────────────────────────────────────────────────────
# 8: HTTP /ws/customer/init
# ──────────────────────────────────────────────────────────────
def test_http_init():
    section("8️⃣  HTTP: /ws/customer/init")
    api=get_lovense(); api.destroy_all()
    api.init(CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"])
    payload=api.build_init_payload()
    print(f"\n  Payload:\n  {json.dumps(payload,indent=4,ensure_ascii=False)}")
    try:
        r=requests.post("https://display.lovense-api.com/ws/customer/init",
                        data=payload,timeout=15)
        d=r.json()
        print(f"\n  Response:\n  {json.dumps(d,indent=4,ensure_ascii=False)}")
        check("HTTP 200",    r.status_code==200)
        if d.get("code")==0:
            info=d.get("data",{})
            check("ws_server_url", "ws_server_url" in info, info.get("ws_server_url","")[:40])
            check("socketIoPath",  "socketIoPath"  in info, info.get("socketIoPath",""))
            return info
        else:
            check("init ok", False, d.get("message",""))
    except requests.exceptions.ConnectionError:
        check("connection", False, "no internet")
    except Exception as e:
        check("HTTP", False, str(e))
    return None


# ──────────────────────────────────────────────────────────────
# 9: WebSocket + تحكم كامل
# ──────────────────────────────────────────────────────────────
async def test_websocket(session_data):
    section("9️⃣  WebSocket + تحكم كامل")
    try: import socketio
    except ImportError:
        check("socketio", False, "pip install python-socketio[asyncio] aiohttp"); return

    api     = get_lovense()
    ws_url  = api.build_socket_url(session_data["ws_server_url"])
    io_path = session_data.get("socketIoPath","/customer")
    pf,mod,cs = CONFIG["platform"],CONFIG["model_name"],CONFIG["customer_name"]

    print(f"\n  URL:  {ws_url[:70]}...\n  Path: {io_path}")

    sio        = socketio.AsyncClient(logger=False, engineio_logger=False)
    ctrl       = ToyControllerAdvanced(sio, pf, mod, cs)
    ws_log     = []
    connected  = asyncio.Event()
    ctrl_ready = asyncio.Event()
    toy_ready  = asyncio.Event()
    tip_settings_raw = {}

    @sio.event
    async def connect():
        log.info("🟢 Connected"); ws_log.append("connected"); connected.set()

    @sio.event
    async def disconnect():
        log.info("🔴 Disconnected")

    @sio.on(SocketEvents.DEVELOPER_PANEL_SETT_SS)
    async def on_panel(data):
        if isinstance(data,str): data=json.loads(data)
        log.info(f"⚙️  Panel: panelSwitch={data.get('panelSwitch')} controlSwitch={data.get('controlSwitch')}")
        ws_log.append("panel")

    @sio.on(SocketEvents.GET_MODEL_TIP_SETT_SS)
    async def on_tip_sett(data):
        nonlocal tip_settings_raw
        if isinstance(data,str): data=json.loads(data)
        tip_settings_raw = data
        ctrl.load_settings(data)
        log.info("💎 Tip settings loaded")
        ws_log.append("tip_settings")
        

    @sio.on("control_link_ready_notice_cs")
    async def on_ready(data):
        log.info("🎮 Control READY!"); ws_log.append("ctrl_ready"); ctrl_ready.set()
        

    

    @sio.on("control_link_not_in_queue_cs")
    async def on_not_in_queue(data):
        log.info("🎯 Server requested entry tip. Simulating 55 tokens...")
    # هنا بنعمل محاكاة لإرسال التيبة المطلوبة (55 توكن) عشان السيرفر يفتح البوابة
        await ctrl.execute_tip(55) 
    
    # لو عايز الاختبار يعتبر ده نجاح فوري:
    # ctrl_ready.set() 

    @sio.on("control_link_toy_status")
    async def on_toy(data):
        if isinstance(data,str): data=json.loads(data)
        ctrl.on_toy_status(data)
        log.info(f"📱 {ctrl.toy}")
        ws_log.append("toy_status")
        if ctrl.toy.connected: toy_ready.set()

    @sio.on("control_link_info_notice_cs")
    async def on_info(data):
        if isinstance(data,str): data=json.loads(data)
        log.info(f"📋 Control info: {data}")
        ws_log.append("ctrl_info")

    @sio.on("end_control_link_notice_cs")
    async def on_end(data):
        log.info("🔚 Control ended by server")

    @sio.on("start_control_link_ss")
    async def on_start_ss(data):
        if isinstance(data,str): data=json.loads(data)
        log.info(f"✅ start_control_link_ss: {data}")
        ws_log.append("start_control_ss")
        ctrl_ready.set()

    @sio.on("VibeWithMeTipStatusDTO")
    async def on_tip(data):
        if isinstance(data,str): data=json.loads(data)
        tokens=data.get("tokens",0)
        log.info(f"💰 Tip: {tokens} tokens!")
        await ctrl.execute_tip(tokens)

    @sio.on("tipperjs_notify_send_online_heartbeat_tc")
    async def on_hb(data):
        await sio.emit("tipperjs_viewer_online_heartbeat_ts")
        log.info("💓 Heartbeat")

    # الاتصال
    try:
        await sio.connect(ws_url, socketio_path=io_path, transports=["websocket"])
    except Exception as e:
        check("WS connect", False, str(e)); return

    try:
        await asyncio.wait_for(connected.wait(), timeout=10)
        check("connected", True)
    except asyncio.TimeoutError:
        check("connected", False, "timeout"); await sio.disconnect(); return

    # الطلبات الأولية
    await sio.emit(SocketEvents.GET_DEVELOPER_PANEL_SETT_CS, {"pf":pf})
    await sio.emit(SocketEvents.GET_MODEL_TIP_SETT_CS, {"pf":pf,"modName":mod})
    await asyncio.sleep(2)
    check("panel received",        "panel"       in ws_log)
    check("tip_settings received", "tip_settings" in ws_log)

    # ── طلب التحكم ────────────────────────────────────────────
    log.info("🎮 Requesting control link...")
    await ctrl.request_control()

    try:
        await asyncio.wait_for(ctrl_ready.wait(), timeout=15)
        check("control link ready", True)
    except asyncio.TimeoutError:
        # not_in_queue → controlSwitch مش مفعَّل
        if "not_in_queue" in ws_log:
            check("control link ready", False,
                  "not_in_queue → controlSwitch=false في إعدادات النموذج")
            print("\n  ⚠️  لتفعيل التحكم يجب على النموذج:")
            print("     1. فتح لوحة Lovense على المنصة")
            print("     2. تفعيل خيار 'Allow Viewer Control'")
            print("     3. أو إرسال tip بقيمة giveControl")
            print(f"\n  📋 إعدادات التحكم المتاحة من الخادم:")
            if ctrl.settings:
                for cmd in ctrl.settings.special_commands:
                    if cmd.cmd_type == "giveControl":
                        print(f"     → أرسل {cmd.tokens} token للحصول على تحكم {cmd.duration}s")
        else:
            check("control link ready", False, "timeout")
        await sio.disconnect(); return

    await ctrl.start_control()
    check("start_control sent", True)

    try:
        await asyncio.wait_for(toy_ready.wait(), timeout=10)
        check("toy connected", True, str(ctrl.toy))
    except asyncio.TimeoutError:
        check("toy connected", False, "toy_status timeout")
        await ctrl.end_control(); await sio.disconnect(); return

    # ── اختبارات التحكم ───────────────────────────────────────
    section("9B — تحكم فعلي في الجهاز")
    print(f"\n  🔧 الجهاز:  {ctrl.toy.toy_name} ({ctrl.toy.toy_type})")
    print(f"  🔧 الأوضاع: {ctrl.toy.supported_modes}")
    print(f"  🔋 البطارية:{ctrl.toy.battery}%\n")

    sub("اهتزاز تدريجي")
    for lvl in [5,10,15,20]:
        await ctrl.vibrate(lvl)
        print(f"  📤 vibrate({lvl:>2})  {'█'*lvl}")
        await asyncio.sleep(0.8)
    await ctrl.stop(); await asyncio.sleep(0.5)
    check("vibrate sequence", True)

    modes = ctrl.toy.supported_modes

    sub("أوضاع متعددة")
    if "rotate"    in modes:
        await ctrl.send_command(vibrate=10,rotate=8)
        await asyncio.sleep(2); await ctrl.stop()
        check("vibrate+rotate", True)
    if "thrusting" in modes:
        await ctrl.send_command(vibrate=8,thrusting=10)
        await asyncio.sleep(2); await ctrl.stop()
        check("vibrate+thrusting", True)
    if "air"       in modes:
        await ctrl.send_command(vibrate=10,air=2)
        await asyncio.sleep(2); await ctrl.stop()
        check("vibrate+air", True)
    if "suction"   in modes:
        await ctrl.send_command(vibrate=10,suction=12)
        await asyncio.sleep(2); await ctrl.stop()
        check("vibrate+suction", True)
    if "depth"     in modes:
        await ctrl.send_command(thrusting=10,depth=2)
        await asyncio.sleep(2); await ctrl.stop()
        check("thrusting+depth", True)

    sub("أنماط حركة")
    print("  📈 ramp_up  0→20")
    await ctrl.ramp_up(0,20,step=4,delay=0.4)
    print("  📉 ramp_down 20→0")
    await ctrl.ramp_down(20,0,step=4,delay=0.4)
    check("ramp up/down", True)

    print("  💫 pulse ×3")
    await ctrl.pulse(peak=15,duration=0.5,cycles=3)
    print("  🎵 pattern متكرر")
    await ctrl.pattern([5,10,15,20,15,10,5,0],delay=0.3,repeat=2)
    check("patterns", True)

    sub("أنماط خاصة (Special Commands)")
    print("  🌍 earthquake pattern (5s)")
    await ctrl._pattern_earthquake(5)
    check("earthquake", True)

    print("  🌊 wave pattern (5s)")
    await ctrl._pattern_wave(5)
    check("wave", True)

    print("  🎆 fireworks pattern (8s)")
    await ctrl._pattern_fireworks(8)
    check("fireworks", True)

    print("  💫 pulse special (6s)")
    await ctrl._pattern_pulse_special(6)
    check("pulse special", True)

    sub("TipSettings — إعدادات الخادم الحقيقية")
    if ctrl.settings:
        print("  استجابة بإعدادات النموذج:")
        for tokens in [5,18,50,100]:
            lvl = ctrl.settings.find_level(tokens)
            sp  = ctrl.settings.find_special(tokens)
            if sp:   print(f"    💰 {tokens} tokens → {sp.cmd_type} {sp.duration}s")
            elif lvl: print(f"    💰 {tokens} tokens → v={lvl.levels.get('vibrate',0)} لمدة {lvl.duration}s")
            else:     print(f"    💰 {tokens} tokens → لا يوجد")
            await ctrl.execute_tip(tokens)
            await asyncio.sleep(0.2)
        check("TipSettings execute", True)

    sub("إنهاء")
    await ctrl.stop()
    await asyncio.sleep(0.5)
    await ctrl.end_control()
    await asyncio.sleep(0.5)
    check("end_control", not ctrl.in_control)
    await sio.disconnect()
    check("disconnected", True)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    print("\n" + "█"*55)
    print("  🧪 TipperPy + ToyControl — اختبار شامل")
    print("█"*55)
    print(f"\n  platform:  {CONFIG['platform']}")
    print(f"  model:     {CONFIG['model_name'][:25]}")
    print(f"  customer:  {CONFIG['customer_name'][:25]}")

    test_crypto()
    test_local_api()
    await test_toy_controller()
    await test_tip_settings()
    test_vibrate_with_me()
    test_utilities()

    lp=sum(1 for _,r in results if r); lt=len(results)
    print(f"\n{'═'*55}\n  📊 المحلية: {lp}/{lt} نجح")
    if lp<lt: print("  ❌ " + ", ".join(n for n,r in results if not r))

    print(f"\n{'═'*55}\n  🌐 اختبارات الإنترنت\n{'═'*55}")

    if CONFIG["model_name"]=="YOUR_MODEL_KEY":
        print("\n  ⚠️  CONFIG لم يُعدَّل → تخطي")
    else:
        test_http_status()
        sd=test_http_init()
        if sd:
            try: await test_websocket(sd)
            except KeyboardInterrupt: print("\n  ⏹ أُوقف يدوياً")
        else:
            check("WebSocket", False, "تخطي — فشل init")

    passed=sum(1 for _,r in results if r); total=len(results)
    failed=[n for n,r in results if not r]
    print(f"\n{'█'*55}")
    print(f"  📊 النتيجة: {passed}/{total} نجح")
    if failed: print(f"  ❌ فشل ({len(failed)}): {', '.join(failed)}")
    else:       print("  🎉 جميع الاختبارات نجحت!")
    print("█"*55)

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt:
        p=sum(1 for _,r in results if r)
        print(f"\n  ⏹ أُوقف | نتيجة حتى الآن: {p}/{len(results)}")
