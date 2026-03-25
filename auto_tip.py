"""
auto_tip.py — سكريبت التحكم التلقائي بأجهزة Lovense
=====================================================

يتصل بـ WebSocket، يبعت tip عشان ياخد التحكم،
وبعدين يتحكم في الجهاز بأنماط مختلفة.

الاستخدام:
    python auto_tip.py                  # وضع تلقائي
    python auto_tip.py --interactive    # وضع تفاعلي

التثبيت:
    pip install python-socketio[asyncio] aiohttp cryptography requests
"""

import asyncio
import json
import logging
import random
import sys
import time
import requests
import socketio

from tipper import (
    get_lovense, LovenseTipperAPI,
    generate_init_signature, SocketEvents,
    random_code,
)
from toy_control import (
    ToyController, ToyControllerAdvanced,
    ToyStatus, TipSettings,
    get_toy_modes, MODE_TO_KEY,
)

# ══════════════════════════════════════════════════════════════
# الإعدادات — غيّر القيم دي حسب حسابك
# ══════════════════════════════════════════════════════════════

CONFIG = {
    "platform":      "flash",
    "model_name":    "jZheVAgIydPT3QeJ1T0zUg==",
    "customer_name": "vHpgecET05OoVbfw9U0cfBxW+Vewhh/aZHAXfwIHKgo=",
}

# أوضاع التحكم التلقائي
AUTO_TIP_CONFIG = {
    "enabled": True,
    "mode": "sequential",      # sequential / random / custom
    "interval_sec": 30,        # الوقت بين كل tip (بالثواني)
    "tip_amounts": [5, 10, 50, 100, 111, 222, 333, 555],
    "max_tips": 0,             # 0 = بلا حد
    "auto_reconnect": True,
    "wait_for_control": True,
    "retry_control_sec": 30,
    "auto_send_entry_tip": True,  # إرسال tip تلقائي عند control_link_not_in_queue
}

# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("AutoTip")


# ══════════════════════════════════════════════════════════════
# دوال إرسال Tip
# ══════════════════════════════════════════════════════════════

class TipSender:
    """
    إرسال Tips عبر Socket.IO.

    المصدر من tipper.js:
      - fanberry_send_tip: حدث client→server لإرسال tip
        الداتا: {tid: "unique_id", amount: NUMBER}

      - tipperjs_notify_exec_tip_tc: حدث server→client لإعلام العميل بتيبة
        الداتا: {camsite: "flash", modelName: "...", customerName: "...", token: NUMBER}

    الآلية:
      1. العميل يبعت fanberry_send_tip مع المبلغ
      2. السيرفر يعالج التيبة ويبعت tipperjs_notify_exec_tip_tc
      3. لو التيبة = giveControl → السيرفر يفتح التحكم
    """

    def __init__(self, sio: socketio.AsyncClient, config: dict):
        self._sio = sio
        self._config = config
        self._tip_count = 0
        self._last_tip_response = None

    async def send_tip(self, amount: int, method: str = "auto") -> bool:
        """
        إرسال tip بقيمة معينة.

        المعاملات:
            amount: عدد التوكنات
            method: طريقة الإرسال
                - "fanberry": عبر fanberry_send_tip event
                - "direct":   عبر tipperjs_notify_exec_tip_tc (محاكاة)
                - "auto":     يجرب fanberry أولاً، ثم direct

        المرجع:
            tipper.js → TIPPERJS_SEND_TIP handler:
                o.emitEvent("fanberry_send_tip", {tid: s, amount: i.amount})
        """
        if amount <= 0:
            log.warning("Tip amount must be > 0")
            return False

        self._tip_count += 1
        tid = random_code(16)

        if method in ("fanberry", "auto"):
            success = await self._send_fanberry_tip(amount, tid)
            if success or method == "fanberry":
                return success

        if method in ("direct", "auto"):
            return await self._send_direct_tip(amount)

        return False

    async def _send_fanberry_tip(self, amount: int, tid: str) -> bool:
        """
        إرسال tip عبر fanberry_send_tip.

        المصدر: tipper.js → Fanberry class:
            case "TIPPERJS_SEND_TIP":
                o.emitEvent("fanberry_send_tip", {tid: s, amount: i.amount})

        الداتا:
            {tid: "unique_transaction_id", amount: NUMBER}
        """
        payload = {
            "tid": tid,
            "amount": amount,
        }
        log.info(f"💰 Sending tip via fanberry_send_tip: {amount} tokens (tid={tid[:8]}...)")

        try:
            await self._sio.emit("fanberry_send_tip", payload)
            log.info(f"✅ fanberry_send_tip emitted: {amount} tokens")
            return True
        except Exception as e:
            log.error(f"❌ fanberry_send_tip failed: {e}")
            return False

    async def _send_direct_tip(self, amount: int) -> bool:
        """
        محاكاة tip عبر إرسال tipperjs_notify_exec_tip_tc مباشرة.

        المصدر: tipper.js → _e() function:
            r = n || {}
            o = r.camsite
            a = r.modelName
            c = r.customerName
            f = Number(n.token || 0)

        الداتا:
            {camsite: "flash", modelName: "...", customerName: "...", token: NUMBER}

        ملاحظة: هذا الحدث عادةً يُرسَل من السيرفر للعميل.
                 إرساله من العميل قد لا يُعالَج من السيرفر.
        """
        payload = json.dumps({
            "camsite": self._config["platform"],
            "modelName": self._config["model_name"],
            "customerName": self._config["customer_name"],
            "token": amount,
        })
        log.info(f"💰 Sending direct tip notification: {amount} tokens")

        try:
            await self._sio.emit("tipperjs_notify_exec_tip_tc", payload)
            log.info(f"✅ Direct tip emitted: {amount} tokens")
            return True
        except Exception as e:
            log.error(f"❌ Direct tip failed: {e}")
            return False

    async def send_givecontrol_tip(self, settings: TipSettings) -> int:
        """
        إرسال tip بقيمة giveControl من إعدادات النموذج.

        يبحث في specialCommand عن type=giveControl ويبعت التيبة المطلوبة.

        المرجع: tipSettings.specialCommand[].type === "giveControl"
            {tokensBegin: 150, tokensEnd: 150, type: "giveControl",
             controlType: "normal", tokenPerMin: 50, time: 40}
        """
        if not settings:
            log.warning("No tip settings available")
            return 0

        for cmd in settings.special_commands:
            if cmd.cmd_type == "giveControl":
                log.info(f"🎮 Found giveControl: {cmd.tokens} tokens → "
                         f"{cmd.duration}s control @ {cmd.token_per_min}t/min")
                await self.send_tip(cmd.tokens)
                return cmd.tokens

        log.warning("No giveControl command found in tip settings")
        return 0

    @property
    def tip_count(self) -> int:
        return self._tip_count


# ══════════════════════════════════════════════════════════════
# الكلاس الرئيسي
# ══════════════════════════════════════════════════════════════

class AutoTipper:
    """
    سكريبت التحكم التلقائي.

    يتصل بالسيرفر، يبعت tip عشان ياخد التحكم،
    ويبعت tips تلقائي بأنماط مختلفة.
    """

    def __init__(self, config: dict, tip_config: dict):
        self.config = config
        self.tip_config = tip_config

        # حالة الاتصال
        self._connected = False
        self._control_ready = False
        self._toy_connected = False
        self._running = False
        self._session_data = None

        # أحداث asyncio
        self._connected_event = asyncio.Event()
        self._control_event = asyncio.Event()
        self._toy_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._tip_received_event = asyncio.Event()

        # Lovense API
        self._api = get_lovense()
        self._sio = None
        self._ctrl = None
        self._tip_sender = None

    # ── تهيئة الجلسة ──────────────────────────────────────────

    def _init_session(self):
        self._api.destroy_all()
        self._api.init(
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )
        log.info(f"API initialized: {self.config['platform']}/{self.config['model_name'][:20]}...")

    def _check_model_status(self) -> bool:
        log.info("Checking model status...")
        try:
            r = requests.post(
                "https://display.lovense-api.com/api/customer/checkModelStatus",
                data={
                    "pf": self.config["platform"],
                    "modelName": self.config["model_name"],
                    "customerName": self.config["customer_name"],
                },
                timeout=10,
            )
            d = r.json()
            if d.get("code") == 0:
                online = d.get("data", {}).get("isModelCamOnline", False)
                log.info(f"Model online: {online}")
                return online
            else:
                log.warning(f"Status check failed: {d.get('message', 'unknown')}")
                return False
        except Exception as e:
            log.error(f"Status check error: {e}")
            return False

    def _get_session(self) -> dict:
        log.info("Getting session data...")
        payload = self._api.build_init_payload()
        try:
            r = requests.post(
                "https://display.lovense-api.com/ws/customer/init",
                data=payload,
                timeout=15,
            )
            d = r.json()
            if d.get("code") == 0:
                info = d.get("data", {})
                log.info(f"Session OK: {info.get('ws_server_url', '')[:50]}...")
                return info
            else:
                log.error(f"Init failed: {d.get('message', 'unknown')}")
                return None
        except Exception as e:
            log.error(f"Init error: {e}")
            return None

    # ── الاتصال بـ WebSocket ──────────────────────────────────

    async def connect(self):
        self._init_session()

        if not self._check_model_status():
            log.warning("Model is offline. Waiting...")
            while not self._check_model_status():
                await asyncio.sleep(30)
            log.info("Model is now online!")

        session = self._get_session()
        if not session:
            log.error("Failed to get session data")
            return False

        self._session_data = session
        ws_url = self._api.build_socket_url(session["ws_server_url"])
        io_path = session.get("socketIoPath", "/customer")

        log.info(f"Connecting to WebSocket...")

        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self._ctrl = ToyControllerAdvanced(
            self._sio,
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )
        self._tip_sender = TipSender(self._sio, self.config)

        self._register_events()

        try:
            await self._sio.connect(ws_url, socketio_path=io_path, transports=["websocket"])
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=15)
            log.info("Connected successfully!")
            return True
        except asyncio.TimeoutError:
            log.error("Connection timeout")
            return False

    def _register_events(self):
        sio = self._sio
        ctrl = self._ctrl

        @sio.event
        async def connect():
            self._connected = True
            self._connected_event.set()
            log.info("🟢 WebSocket connected!")

        @sio.event
        async def disconnect():
            self._connected = False
            self._control_ready = False
            self._toy_connected = False
            self._connected_event.clear()
            self._control_event.clear()
            self._toy_event.clear()
            log.warning("🔴 WebSocket disconnected!")

            if self.tip_config["auto_reconnect"] and self._running:
                log.info("Reconnecting in 5 seconds...")
                await asyncio.sleep(5)
                await self.connect()

        @sio.on(SocketEvents.DEVELOPER_PANEL_SETT_SS)
        async def on_panel(data):
            if isinstance(data, str):
                data = json.loads(data)
            panel = data.get("panelSwitch", False)
            control = data.get("controlSwitch", False)
            tip = data.get("tipSwitch", False)
            log.info(f"⚙️  Panel: panelSwitch={panel}, controlSwitch={control}, tipSwitch={tip}")

        @sio.on(SocketEvents.GET_MODEL_TIP_SETT_SS)
        async def on_tip_settings(data):
            if isinstance(data, str):
                data = json.loads(data)
            ctrl.load_settings(data)
            log.info("💎 Tip settings loaded from server")

            # عرض إعدادات giveControl
            if ctrl.settings:
                for cmd in ctrl.settings.special_commands:
                    if cmd.cmd_type == "giveControl":
                        log.info(f"🎮 giveControl available: {cmd.tokens} tokens → "
                                 f"{cmd.duration}s @ {cmd.token_per_min}t/min")

        @sio.on("control_link_ready_notice_cs")
        async def on_control_ready(data):
            log.info("🎮 Control link READY!")
            self._control_ready = True
            self._control_event.set()

        @sio.on("start_control_link_ss")
        async def on_start_control(data):
            if isinstance(data, str):
                data = json.loads(data)
            log.info(f"✅ Control started by server: {data}")
            self._control_ready = True
            self._control_event.set()

        @sio.on("control_link_not_in_queue_cs")
        async def on_not_in_queue(data):
            """
            السيرفر بيقول: مش في طابور التحكم.
            لازم نبعت tip بقيمة giveControl عشان نحصل على التحكم.

            المصدر: tipper.js → control_link_not_in_queue_cs handler
            """
            log.info("⏳ Not in queue - need to send tip for control access")

            if self.tip_config.get("auto_send_entry_tip") and ctrl.settings:
                # البحث عن giveControl في إعدادات التيبات
                for cmd in ctrl.settings.special_commands:
                    if cmd.cmd_type == "giveControl":
                        log.info(f"🎮 Auto-sending giveControl tip: {cmd.tokens} tokens")
                        await self._tip_sender.send_tip(cmd.tokens)
                        # انتظار رد السيرفر
                        await asyncio.sleep(3)
                        # إعادة طلب التحكم بعد إرسال التيبة
                        log.info("🔄 Re-requesting control after tip...")
                        await ctrl.request_control()
                        return

                log.warning("No giveControl in settings, trying basic tip amounts...")
                # محاولة بأقل قيمة في basicLevel
                if ctrl.settings.basic_levels:
                    min_tip = ctrl.settings.basic_levels[0].tip_begin
                    log.info(f"💰 Sending basic tip: {min_tip} tokens")
                    await self._tip_sender.send_tip(min_tip)
                    await asyncio.sleep(2)
                    await ctrl.request_control()
            else:
                log.info("   Waiting for model to enable control or send tip manually...")

        @sio.on("control_link_in_queue_notice_cs")
        async def on_in_queue(data):
            if isinstance(data, str):
                data = json.loads(data)
            pos = data.get("position", "?")
            log.info(f"⏳ In queue - position: {pos}")

        @sio.on("control_link_toy_status")
        async def on_toy_status(data):
            if isinstance(data, str):
                data = json.loads(data)
            ctrl.on_toy_status(data)
            if ctrl.toy.connected:
                self._toy_connected = True
                self._toy_event.set()
                log.info(f"📱 Toy connected: {ctrl.toy.toy_name} ({ctrl.toy.toy_type}) "
                         f"🔋{ctrl.toy.battery}%")
            else:
                self._toy_connected = False
                self._toy_event.clear()
                log.warning(f"📱 Toy disconnected")

        @sio.on("control_link_info_notice_cs")
        async def on_control_info(data):
            if isinstance(data, str):
                data = json.loads(data)
            log.info(f"📋 Control info: {data}")

        @sio.on("end_control_link_notice_cs")
        async def on_end_control(data):
            log.warning("🔚 Control link ended by server/model")
            self._control_ready = False
            self._control_event.clear()

        @sio.on("end_broadcast_notice_cs")
        async def on_end_broadcast(data):
            log.warning("📡 Broadcast ended - model went offline")
            self._control_ready = False
            self._control_event.clear()

        @sio.on("tipperjs_notify_exec_tip_tc")
        async def on_tip_executed(data):
            """
            السيرفر بيبلغنا إن tip اتنفذ.

            المصدر: tipper.js:
                Lt.on("tipperjs_notify_exec_tip_tc", function(t) { _e(formatStringToObj(t)) })

                _e = we() function:
                    r = n || {}
                    camsite = r.camsite
                    modelName = r.modelName
                    customerName = r.customerName
                    token = Number(n.token || 0)

            الداتا:
                {camsite: "flash", modelName: "...", customerName: "...", token: NUMBER}
            """
            if isinstance(data, str):
                data = json.loads(data)
            tokens = data.get("token", 0)
            log.info(f"💰 TIP EXECUTED by server: {tokens} tokens!")
            log.info(f"   Data: {data}")
            self._tip_received_event.set()

            # تنفيذ الأمر المناسب حسب إعدادات التيبات
            if ctrl.settings and tokens > 0:
                await ctrl.execute_tip(tokens)

        @sio.on("VibeWithMeTipStatusDTO")
        async def on_external_tip(data):
            if isinstance(data, str):
                data = json.loads(data)
            tokens = data.get("tokens", 0)
            log.info(f"💰 External tip (VibeWithMe): {tokens} tokens")

        @sio.on("tipperjs_notify_send_online_heartbeat_tc")
        async def on_heartbeat(data):
            await sio.emit("tipperjs_viewer_online_heartbeat_ts")

    # ── طلب التحكم ────────────────────────────────────────────

    async def request_control(self) -> bool:
        if not self._connected:
            log.error("Not connected")
            return False

        pf = self.config["platform"]
        mod = self.config["model_name"]

        # طلب إعدادات اللوحة والتيبات
        await self._sio.emit(SocketEvents.GET_DEVELOPER_PANEL_SETT_CS, {"pf": pf})
        await self._sio.emit(SocketEvents.GET_MODEL_TIP_SETT_CS, {"pf": pf, "modName": mod})
        await asyncio.sleep(3)

        # طلب التحكم
        log.info("🎮 Requesting control link...")
        await self._ctrl.request_control()

        if self.tip_config["wait_for_control"]:
            retry_sec = self.tip_config["retry_control_sec"]
            attempts = 0
            max_attempts = 10

            while not self._control_ready and self._running and attempts < max_attempts:
                try:
                    await asyncio.wait_for(self._control_event.wait(), timeout=retry_sec)
                    break
                except asyncio.TimeoutError:
                    attempts += 1
                    if not self._running:
                        return False
                    log.info(f"⏳ Still waiting for control... attempt {attempts}/{max_attempts}")
                    # إعادة طلب التحكم
                    await self._ctrl.request_control()
        else:
            try:
                await asyncio.wait_for(self._control_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("Control link not ready (timeout)")
                return False

        if self._control_ready:
            await self._ctrl.start_control()
            log.info("🟢 Control is active!")

            try:
                await asyncio.wait_for(self._toy_event.wait(), timeout=15)
                log.info(f"🎯 Ready! Toy: {self._ctrl.toy}")
            except asyncio.TimeoutError:
                log.warning("Toy not connected yet, but control is active")

            return True

        return False

    # ── إرسال Tips ────────────────────────────────────────────

    async def send_tip(self, amount: int, method: str = "auto"):
        """
        إرسال tip بقيمة معينة.

        المعاملات:
            amount: عدد التوكنات
            method: "fanberry" / "direct" / "auto"
        """
        if not self._tip_sender:
            log.error("Not connected")
            return
        await self._tip_sender.send_tip(amount, method)

    async def auto_tip_loop(self):
        """حلقة إرسال Tips تلقائي."""
        cfg = self.tip_config
        amounts = cfg["tip_amounts"]
        mode = cfg["mode"]
        interval = cfg["interval_sec"]
        max_tips = cfg["max_tips"]
        idx = 0
        tip_count = 0

        log.info(f"\n{'='*55}")
        log.info(f"  🚀 Auto-tip started!")
        log.info(f"  Mode: {mode}")
        log.info(f"  Amounts: {amounts}")
        log.info(f"  Interval: {interval}s")
        log.info(f"  Max tips: {'unlimited' if max_tips == 0 else max_tips}")
        log.info(f"{'='*55}\n")

        while self._running:
            if self._stop_event.is_set():
                break

            if max_tips > 0 and tip_count >= max_tips:
                log.info(f"Reached max tips ({max_tips}). Stopping.")
                break

            if not self._control_ready:
                log.warning("Control not ready. Waiting...")
                try:
                    await asyncio.wait_for(self._control_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    continue

            # اختيار قيمة التيبة
            if mode == "sequential":
                tokens = amounts[idx % len(amounts)]
                idx += 1
            elif mode == "random":
                tokens = random.choice(amounts)
            else:
                tokens = amounts[idx % len(amounts)] if amounts else 10
                idx += 1

            # إرسال التيبة
            tip_count += 1
            log.info(f"\n💰 [{tip_count}] Sending tip: {tokens} tokens")

            try:
                # إرسال عبر fanberry_send_tip
                await self._tip_sender.send_tip(tokens)

                # تنفيذ أوامر التحكم محلياً كمان
                if self._ctrl.settings:
                    await self._ctrl.execute_tip(tokens)

                log.info(f"✅ Tip #{tip_count} sent ({tokens} tokens)")
            except Exception as e:
                log.error(f"❌ Tip failed: {e}")

            # الانتظار
            log.info(f"⏳ Next tip in {interval}s...")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

        log.info(f"\n📊 Auto-tip finished. Total tips sent: {tip_count}")

    # ── أوامر يدوية ───────────────────────────────────────────

    async def send_vibrate(self, level: int, duration: float = 5.0):
        if not self._ctrl:
            log.error("Not connected"); return
        log.info(f"📳 Vibrate: level={level} for {duration}s")
        await self._ctrl.vibrate(level)
        await asyncio.sleep(duration)
        await self._ctrl.stop()

    async def send_pattern(self, pattern_name: str, duration: int = 10):
        if not self._ctrl:
            log.error("Not connected"); return
        log.info(f"🎵 Running pattern: {pattern_name} ({duration}s)")
        if pattern_name == "earthquake":
            await self._ctrl._pattern_earthquake(duration)
        elif pattern_name == "fireworks":
            await self._ctrl._pattern_fireworks(duration)
        elif pattern_name == "wave":
            await self._ctrl._pattern_wave(duration)
        elif pattern_name == "pulse":
            await self._ctrl.pulse(peak=20, duration=0.5, cycles=max(1, duration // 2))
        elif pattern_name == "ramp":
            await self._ctrl.ramp_up(0, 20, step=2, delay=0.3)
            await self._ctrl.ramp_down(20, 0, step=2, delay=0.3)
        else:
            log.warning(f"Unknown pattern: {pattern_name}")

    # ── التشغيل الرئيسي ──────────────────────────────────────

    async def run(self):
        self._running = True
        self._stop_event.clear()

        print(f"\n{'█'*55}")
        print(f"  🤖 Lovense Auto-Tipper v2")
        print(f"{'█'*55}")
        print(f"\n  Platform:  {self.config['platform']}")
        print(f"  Model:     {self.config['model_name'][:25]}...")
        print(f"  Customer:  {self.config['customer_name'][:25]}...")
        print()

        # 1. الاتصال
        if not await self.connect():
            log.error("Failed to connect. Exiting.")
            return

        # 2. طلب التحكم (مع إرسال tip تلقائي لو مطلوب)
        if not await self.request_control():
            log.warning("Could not get control via normal flow.")
            log.info("Trying to send giveControl tip...")

            if self._ctrl.settings:
                tokens = await self._tip_sender.send_givecontrol_tip(self._ctrl.settings)
                if tokens > 0:
                    await asyncio.sleep(5)
                    # إعادة محاولة التحكم
                    self._control_event.clear()
                    await self._ctrl.request_control()
                    try:
                        await asyncio.wait_for(self._control_event.wait(), timeout=30)
                        await self._ctrl.start_control()
                        log.info("🟢 Control acquired after tip!")
                    except asyncio.TimeoutError:
                        log.error("Still no control after tip. The model may need to approve.")

        # 3. عرض معلومات الجهاز
        if self._ctrl and self._ctrl.toy.connected:
            print(f"\n{'─'*55}")
            print(f"  🔧 Device:  {self._ctrl.toy.toy_name} ({self._ctrl.toy.toy_type})")
            print(f"  🔧 Modes:   {self._ctrl.toy.supported_modes}")
            print(f"  🔋 Battery: {self._ctrl.toy.battery}%")
            print(f"{'─'*55}")

        if self._ctrl and self._ctrl.settings:
            print(f"\n  📊 Server tip settings:")
            self._ctrl.settings.print_summary()

        # 4. تشغيل حلقة التيبات التلقائية
        if self.tip_config["enabled"]:
            await self.auto_tip_loop()
        else:
            log.info("Auto-tip disabled. Staying connected...")
            await self._sio.wait()

    async def stop(self):
        log.info("Stopping...")
        self._running = False
        self._stop_event.set()

        if self._ctrl and self._ctrl.in_control:
            try:
                await self._ctrl.stop()
                await self._ctrl.end_control()
            except Exception:
                pass

        if self._sio and self._connected:
            try:
                await self._sio.disconnect()
            except Exception:
                pass

        log.info("Stopped.")

    # ── الوضع التفاعلي ────────────────────────────────────────

    async def interactive_mode(self):
        self._running = True
        self._stop_event.clear()
        self.tip_config["enabled"] = False

        print(f"\n{'█'*55}")
        print(f"  🎮 Lovense Interactive Controller v2")
        print(f"{'█'*55}")

        if not await self.connect():
            return

        # طلب التحكم
        control_ok = await self.request_control()
        if not control_ok:
            log.info("Control not ready yet. You can send tips to get control.")

        print(f"\n{'═'*55}")
        print("  الأوامر المتاحة:")
        print("  ─────────────────────────────────────────────────")
        print("  tip <amount>           → إرسال tip (fanberry)")
        print("  tip <amount> direct    → إرسال tip (direct)")
        print("  tip <amount> fanberry  → إرسال tip (fanberry)")
        print("  givecontrol            → إرسال tip بقيمة giveControl")
        print("  vib <level> [seconds]  → اهتزاز (0-20)")
        print("  stop                   → إيقاف الجهاز")
        print("  earthquake [seconds]   → نمط زلزال")
        print("  fireworks [seconds]    → نمط ألعاب نارية")
        print("  wave [seconds]         → نمط موجة")
        print("  pulse [seconds]        → نبضات")
        print("  ramp                   → صعود وهبوط تدريجي")
        print("  auto                   → تشغيل التيبات التلقائية")
        print("  control                → إعادة طلب التحكم")
        print("  status                 → حالة الاتصال والجهاز")
        print("  settings               → عرض إعدادات التيبات")
        print("  quit / exit            → خروج")
        print(f"{'═'*55}\n")

        while self._running:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("🎮 > ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                break

            if not cmd:
                continue

            parts = cmd.lower().split()
            action = parts[0]

            try:
                if action in ("quit", "exit", "q"):
                    break

                elif action == "tip" and len(parts) > 1:
                    tokens = int(parts[1])
                    method = parts[2] if len(parts) > 2 else "auto"
                    await self.send_tip(tokens, method)

                elif action == "givecontrol":
                    if self._ctrl and self._ctrl.settings:
                        tokens = await self._tip_sender.send_givecontrol_tip(self._ctrl.settings)
                        if tokens > 0:
                            await asyncio.sleep(3)
                            await self._ctrl.request_control()
                    else:
                        log.warning("No tip settings loaded yet")

                elif action == "vib" and len(parts) > 1:
                    level = int(parts[1])
                    duration = float(parts[2]) if len(parts) > 2 else 5.0
                    await self.send_vibrate(level, duration)

                elif action == "stop":
                    if self._ctrl:
                        await self._ctrl.stop()
                    log.info("⏹ Stopped")

                elif action in ("earthquake", "fireworks", "wave", "pulse", "ramp"):
                    duration = int(parts[1]) if len(parts) > 1 else 10
                    await self.send_pattern(action, duration)

                elif action == "auto":
                    self.tip_config["enabled"] = True
                    await self.auto_tip_loop()
                    self.tip_config["enabled"] = False

                elif action == "control":
                    self._control_event.clear()
                    self._control_ready = False
                    await self.request_control()

                elif action == "status":
                    print(f"  Connected:  {self._connected}")
                    print(f"  Control:    {self._control_ready}")
                    print(f"  Toy:        {self._ctrl.toy if self._ctrl else 'N/A'}")
                    print(f"  Tips sent:  {self._tip_sender.tip_count if self._tip_sender else 0}")
                    if self._ctrl and self._ctrl.toy.connected:
                        print(f"  Modes:      {self._ctrl.toy.supported_modes}")
                        print(f"  Battery:    {self._ctrl.toy.battery}%")

                elif action == "settings":
                    if self._ctrl and self._ctrl.settings:
                        self._ctrl.settings.print_summary()
                    else:
                        log.warning("No settings loaded")

                else:
                    print(f"  ❓ أمر غير معروف: {cmd}")

            except ValueError as e:
                print(f"  ❌ قيمة غير صحيحة: {e}")
            except Exception as e:
                log.error(f"Command error: {e}")

        await self.stop()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    tipper = AutoTipper(CONFIG, AUTO_TIP_CONFIG)

    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        await tipper.interactive_mode()
    else:
        try:
            await tipper.run()
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        finally:
            await tipper.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ Stopped by user")
