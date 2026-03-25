"""
auto_tip.py — سكريبت التحكم التلقائي بأجهزة Lovense
=====================================================

يتصل بـ WebSocket، يستنى النموذج يفعّل التحكم،
وبعدين يبعت tips تلقائي بأنماط مختلفة.

الاستخدام:
    python auto_tip.py

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
    "tip_amounts": [5, 10, 50, 100, 111, 222, 333, 555],  # قيم التيبات
    "max_tips": 0,             # 0 = بلا حد
    "auto_reconnect": True,    # إعادة الاتصال تلقائي لو انقطع
    "wait_for_control": True,  # استنى التحكم يتفعّل
    "retry_control_sec": 30,   # إعادة محاولة طلب التحكم
}

# ══════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("AutoTip")


# ══════════════════════════════════════════════════════════════
# الكلاس الرئيسي
# ══════════════════════════════════════════════════════════════

class AutoTipper:
    """
    سكريبت التحكم التلقائي.

    يتصل بالسيرفر، يستنى التحكم يتفعّل،
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
        self._tip_count = 0
        self._session_data = None

        # أحداث asyncio
        self._connected_event = asyncio.Event()
        self._control_event = asyncio.Event()
        self._toy_event = asyncio.Event()
        self._stop_event = asyncio.Event()

        # Lovense API
        self._api = get_lovense()
        self._sio = None
        self._ctrl = None

    # ── تهيئة الجلسة ──────────────────────────────────────────

    def _init_session(self):
        """تهيئة جلسة Lovense API."""
        self._api.destroy_all()
        self._api.init(
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )
        log.info(f"API initialized: {self.config['platform']}/{self.config['model_name'][:20]}...")

    def _check_model_status(self) -> bool:
        """التحقق من حالة النموذج (أونلاين ولا لا)."""
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
        """الحصول على بيانات الجلسة (ws_server_url + socketIoPath)."""
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
        """الاتصال بالسيرفر عبر WebSocket."""
        self._init_session()

        # التحقق من حالة النموذج
        if not self._check_model_status():
            log.warning("Model is offline. Waiting...")
            while not self._check_model_status():
                await asyncio.sleep(30)
            log.info("Model is now online!")

        # الحصول على بيانات الجلسة
        session = self._get_session()
        if not session:
            log.error("Failed to get session data")
            return False

        self._session_data = session
        ws_url = self._api.build_socket_url(session["ws_server_url"])
        io_path = session.get("socketIoPath", "/customer")

        log.info(f"Connecting to WebSocket...")
        log.info(f"  URL:  {ws_url[:70]}...")
        log.info(f"  Path: {io_path}")

        # إنشاء Socket.IO client
        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self._ctrl = ToyControllerAdvanced(
            self._sio,
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )

        # تسجيل الأحداث
        self._register_events()

        # الاتصال
        try:
            await self._sio.connect(ws_url, socketio_path=io_path, transports=["websocket"])
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

        # انتظار الاتصال
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=15)
            log.info("Connected successfully!")
            return True
        except asyncio.TimeoutError:
            log.error("Connection timeout")
            return False

    def _register_events(self):
        """تسجيل أحداث Socket.IO."""
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
            log.info(f"⚙️  Panel: panelSwitch={panel}, controlSwitch={control}")

        @sio.on(SocketEvents.GET_MODEL_TIP_SETT_SS)
        async def on_tip_settings(data):
            if isinstance(data, str):
                data = json.loads(data)
            ctrl.load_settings(data)
            log.info("💎 Tip settings loaded from server")

        @sio.on("control_link_ready_notice_cs")
        async def on_control_ready(data):
            log.info("🎮 Control link READY!")
            self._control_ready = True
            self._control_event.set()

        @sio.on("start_control_link_ss")
        async def on_start_control(data):
            if isinstance(data, str):
                data = json.loads(data)
            log.info(f"✅ Control started: {data}")
            self._control_ready = True
            self._control_event.set()

        @sio.on("control_link_not_in_queue_cs")
        async def on_not_in_queue(data):
            log.info("⏳ Not in queue - control not enabled by model yet")
            log.info("   Waiting for model to enable viewer control...")

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
                log.info(f"   Supported modes: {ctrl.toy.supported_modes}")
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

        @sio.on("VibeWithMeTipStatusDTO")
        async def on_external_tip(data):
            if isinstance(data, str):
                data = json.loads(data)
            tokens = data.get("tokens", 0)
            log.info(f"💰 External tip received: {tokens} tokens")

        @sio.on("tipperjs_notify_send_online_heartbeat_tc")
        async def on_heartbeat(data):
            await sio.emit("tipperjs_viewer_online_heartbeat_ts")

    # ── طلب التحكم ────────────────────────────────────────────

    async def request_control(self) -> bool:
        """طلب التحكم والانتظار."""
        if not self._connected:
            log.error("Not connected")
            return False

        pf = self.config["platform"]
        mod = self.config["model_name"]

        # طلب إعدادات اللوحة والتيبات
        await self._sio.emit(SocketEvents.GET_DEVELOPER_PANEL_SETT_CS, {"pf": pf})
        await self._sio.emit(SocketEvents.GET_MODEL_TIP_SETT_CS, {"pf": pf, "modName": mod})
        await asyncio.sleep(2)

        # طلب التحكم
        log.info("🎮 Requesting control link...")
        await self._ctrl.request_control()

        if self.tip_config["wait_for_control"]:
            retry_sec = self.tip_config["retry_control_sec"]
            while not self._control_ready and self._running:
                try:
                    await asyncio.wait_for(self._control_event.wait(), timeout=retry_sec)
                    break
                except asyncio.TimeoutError:
                    if not self._running:
                        return False
                    log.info(f"⏳ Still waiting for control... retrying in {retry_sec}s")
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

            # انتظار اتصال الجهاز
            try:
                await asyncio.wait_for(self._toy_event.wait(), timeout=15)
                log.info(f"🎯 Ready to send tips! Toy: {self._ctrl.toy}")
            except asyncio.TimeoutError:
                log.warning("Toy not connected yet, but control is active")

            return True

        return False

    # ── إرسال Tips تلقائي ─────────────────────────────────────

    async def auto_tip_loop(self):
        """حلقة إرسال Tips تلقائي."""
        cfg = self.tip_config
        amounts = cfg["tip_amounts"]
        mode = cfg["mode"]
        interval = cfg["interval_sec"]
        max_tips = cfg["max_tips"]
        idx = 0

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

            if max_tips > 0 and self._tip_count >= max_tips:
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
            elif mode == "custom":
                tokens = amounts[idx % len(amounts)] if amounts else 10
                idx += 1
            else:
                tokens = amounts[0] if amounts else 10

            # إرسال التيبة
            self._tip_count += 1
            log.info(f"\n💰 [{self._tip_count}] Sending tip: {tokens} tokens")

            try:
                await self._ctrl.execute_tip(tokens)
                log.info(f"✅ Tip #{self._tip_count} sent ({tokens} tokens)")
            except Exception as e:
                log.error(f"❌ Tip failed: {e}")

            # الانتظار قبل التيبة التالية
            log.info(f"⏳ Next tip in {interval}s...")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # normal timeout, continue

        log.info(f"\n📊 Auto-tip finished. Total tips sent: {self._tip_count}")

    # ── أوامر يدوية ───────────────────────────────────────────

    async def send_single_tip(self, tokens: int):
        """إرسال tip واحد يدوي."""
        if not self._ctrl:
            log.error("Not connected")
            return
        log.info(f"💰 Sending single tip: {tokens} tokens")
        await self._ctrl.execute_tip(tokens)
        self._tip_count += 1
        log.info(f"✅ Tip sent ({tokens} tokens)")

    async def send_vibrate(self, level: int, duration: float = 5.0):
        """إرسال أمر اهتزاز مباشر."""
        if not self._ctrl:
            log.error("Not connected")
            return
        log.info(f"📳 Vibrate: level={level} for {duration}s")
        await self._ctrl.vibrate(level)
        await asyncio.sleep(duration)
        await self._ctrl.stop()

    async def send_pattern(self, pattern_name: str):
        """تشغيل نمط جاهز."""
        if not self._ctrl:
            log.error("Not connected")
            return

        log.info(f"🎵 Running pattern: {pattern_name}")
        if pattern_name == "earthquake":
            await self._ctrl._pattern_earthquake(10)
        elif pattern_name == "fireworks":
            await self._ctrl._pattern_fireworks(10)
        elif pattern_name == "wave":
            await self._ctrl._pattern_wave(10)
        elif pattern_name == "pulse":
            await self._ctrl.pulse(peak=20, duration=0.5, cycles=5)
        elif pattern_name == "ramp":
            await self._ctrl.ramp_up(0, 20, step=2, delay=0.3)
            await self._ctrl.ramp_down(20, 0, step=2, delay=0.3)
        else:
            log.warning(f"Unknown pattern: {pattern_name}")

    # ── التشغيل الرئيسي ──────────────────────────────────────

    async def run(self):
        """التشغيل الرئيسي: اتصال → تحكم → tips تلقائي."""
        self._running = True
        self._stop_event.clear()

        print(f"\n{'█'*55}")
        print(f"  🤖 Lovense Auto-Tipper")
        print(f"{'█'*55}")
        print(f"\n  Platform:  {self.config['platform']}")
        print(f"  Model:     {self.config['model_name'][:25]}...")
        print(f"  Customer:  {self.config['customer_name'][:25]}...")
        print()

        # 1. الاتصال
        if not await self.connect():
            log.error("Failed to connect. Exiting.")
            return

        # 2. طلب التحكم
        if not await self.request_control():
            log.error("Failed to get control. Exiting.")
            await self._sio.disconnect()
            return

        # 3. عرض معلومات الجهاز
        if self._ctrl.toy.connected:
            print(f"\n{'─'*55}")
            print(f"  🔧 Device:  {self._ctrl.toy.toy_name} ({self._ctrl.toy.toy_type})")
            print(f"  🔧 Modes:   {self._ctrl.toy.supported_modes}")
            print(f"  🔋 Battery: {self._ctrl.toy.battery}%")
            print(f"{'─'*55}")

        # 4. عرض إعدادات التيبات من السيرفر
        if self._ctrl.settings:
            print(f"\n  📊 Server tip settings:")
            self._ctrl.settings.print_summary()

        # 5. تشغيل حلقة التيبات التلقائية
        if self.tip_config["enabled"]:
            await self.auto_tip_loop()
        else:
            log.info("Auto-tip disabled. Use send_single_tip() or send_pattern()")
            # البقاء متصل
            await self._sio.wait()

    async def stop(self):
        """إيقاف السكريبت."""
        log.info("Stopping...")
        self._running = False
        self._stop_event.set()

        if self._ctrl and self._ctrl.in_control:
            await self._ctrl.stop()
            await self._ctrl.end_control()

        if self._sio and self._connected:
            await self._sio.disconnect()

        log.info("Stopped.")

    # ── الوضع التفاعلي ────────────────────────────────────────

    async def interactive_mode(self):
        """
        الوضع التفاعلي: يتصل ويستنى التحكم،
        وبعدين يسمحلك تبعت أوامر يدوية.
        """
        self._running = True
        self._stop_event.clear()
        self.tip_config["enabled"] = False

        print(f"\n{'█'*55}")
        print(f"  🎮 Lovense Interactive Controller")
        print(f"{'█'*55}")

        # الاتصال والتحكم
        if not await self.connect():
            return
        if not await self.request_control():
            await self._sio.disconnect()
            return

        # عرض الأوامر المتاحة
        print(f"\n{'═'*55}")
        print("  الأوامر المتاحة:")
        print("  ─────────────────────────────────────────────────")
        print("  tip <amount>     → إرسال tip بقيمة معينة")
        print("  vib <level>      → اهتزاز (0-20)")
        print("  stop             → إيقاف الجهاز")
        print("  earthquake       → نمط زلزال (10s)")
        print("  fireworks        → نمط ألعاب نارية (10s)")
        print("  wave             → نمط موجة (10s)")
        print("  pulse            → نبضات (5 دورات)")
        print("  ramp             → صعود وهبوط تدريجي")
        print("  auto             → تشغيل التيبات التلقائية")
        print("  status           → حالة الجهاز")
        print("  quit / exit      → خروج")
        print(f"{'═'*55}\n")

        # حلقة الأوامر
        while self._running:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("🎮 > ").strip().lower()
                )
            except (EOFError, KeyboardInterrupt):
                break

            if not cmd:
                continue

            parts = cmd.split()
            action = parts[0]

            try:
                if action in ("quit", "exit", "q"):
                    break

                elif action == "tip" and len(parts) > 1:
                    tokens = int(parts[1])
                    await self.send_single_tip(tokens)

                elif action == "vib" and len(parts) > 1:
                    level = int(parts[1])
                    duration = float(parts[2]) if len(parts) > 2 else 5.0
                    await self.send_vibrate(level, duration)

                elif action == "stop":
                    await self._ctrl.stop()
                    log.info("⏹ Stopped")

                elif action in ("earthquake", "fireworks", "wave", "pulse", "ramp"):
                    await self.send_pattern(action)

                elif action == "auto":
                    self.tip_config["enabled"] = True
                    await self.auto_tip_loop()
                    self.tip_config["enabled"] = False

                elif action == "status":
                    print(f"  Connected: {self._connected}")
                    print(f"  Control:   {self._control_ready}")
                    print(f"  Toy:       {self._ctrl.toy}")
                    print(f"  Tips sent: {self._tip_count}")

                else:
                    print(f"  ❓ أمر غير معروف: {cmd}")
                    print("  اكتب 'quit' للخروج")

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

    # اختيار الوضع
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
