"""
auto_tip.py — سكريبت التحكم في أجهزة Lovense عبر WebSocket
============================================================

يتصل بـ WebSocket ويبعت أوامر تحكم مباشرة عبر control_toy_cmd_cs.

ملاحظة مهمة:
  - إرسال Tips حقيقية لازم يكون من منصة الكام (Stripchat/Chaturbate) نفسها
  - fanberry_send_tip خاص بمنصة Fanberry بس
  - لكن أوامر التحكم (control_toy_cmd_cs) ممكن تتبعت مباشرة
    لو controlSwitch=true في إعدادات النموذج

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
    ToyStatus, TipSettings, ToyCommand,
    get_toy_modes, MODE_TO_KEY,
)

# ══════════════════════════════════════════════════════════════
# الإعدادات
# ══════════════════════════════════════════════════════════════

CONFIG = {
    "platform":      "flash",
    "model_name":    "jZheVAgIydPT3QeJ1T0zUg==",
    "customer_name": "vHpgecET05OoVbfw9U0cfBxW+Vewhh/aZHAXfwIHKgo=",
}

AUTO_TIP_CONFIG = {
    "enabled": True,
    "mode": "sequential",        # sequential / random
    "interval_sec": 30,          # الوقت بين كل tip بالثواني
    "tip_amounts": [5, 10, 50, 100, 111, 222, 333, 555],
    "max_tips": 0,               # 0 = بلا حد
    "auto_reconnect": True,
}

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
    يتصل بـ WebSocket ويبعت أوامر تحكم مباشرة.

    الآلية:
      1. يتصل بالسيرفر كـ customer display panel
      2. يحمّل إعدادات التيبات (basicLevel + specialCommand)
      3. يبعت أوامر control_toy_cmd_cs مباشرة
         (مش محتاج giveControl أو control queue)
      4. السيرفر بيقبل الأوامر لو controlSwitch=true

    المرجع من tipper.js:
      - socket.emit("control_toy_cmd_cs", {pf, modName, csName, v, r, f, ...})
      - الأوامر دي بتتبعت حتى بدون control link
    """

    def __init__(self, config: dict, tip_config: dict):
        self.config = config
        self.tip_config = tip_config

        self._connected = False
        self._running = False
        self._tip_count = 0

        self._connected_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._settings_loaded = asyncio.Event()

        self._api = get_lovense()
        self._sio = None
        self._ctrl = None
        self._settings = None

    # ── تهيئة الجلسة ──────────────────────────────────────────

    def _init_session(self):
        self._api.destroy_all()
        self._api.init(
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )

    def _check_model_status(self) -> bool:
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
                return d.get("data", {}).get("isModelCamOnline", False)
        except Exception as e:
            log.error(f"Status check error: {e}")
        return False

    def _get_session(self) -> dict:
        payload = self._api.build_init_payload()
        try:
            r = requests.post(
                "https://display.lovense-api.com/ws/customer/init",
                data=payload,
                timeout=15,
            )
            d = r.json()
            if d.get("code") == 0:
                return d.get("data", {})
        except Exception as e:
            log.error(f"Init error: {e}")
        return None

    # ── الاتصال بـ WebSocket ──────────────────────────────────

    async def connect(self) -> bool:
        self._init_session()

        log.info("Checking model status...")
        if not self._check_model_status():
            log.warning("Model is offline. Waiting...")
            while not self._check_model_status():
                await asyncio.sleep(30)

        log.info("Model is online! Getting session...")
        session = self._get_session()
        if not session:
            log.error("Failed to get session")
            return False

        ws_url = self._api.build_socket_url(session["ws_server_url"])
        io_path = session.get("socketIoPath", "/customer")

        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self._ctrl = ToyControllerAdvanced(
            self._sio,
            self.config["platform"],
            self.config["model_name"],
            self.config["customer_name"],
        )

        self._register_events()

        try:
            await self._sio.connect(ws_url, socketio_path=io_path, transports=["websocket"])
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=15)
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
            self._connected_event.clear()
            self._settings_loaded.clear()
            log.warning("🔴 Disconnected!")

            if self.tip_config["auto_reconnect"] and self._running:
                log.info("Reconnecting in 5s...")
                await asyncio.sleep(5)
                await self.connect()

        @sio.on(SocketEvents.DEVELOPER_PANEL_SETT_SS)
        async def on_panel(data):
            if isinstance(data, str):
                data = json.loads(data)
            log.info(f"⚙️  Panel: controlSwitch={data.get('controlSwitch')}, "
                     f"tipSwitch={data.get('tipSwitch')}")

        @sio.on(SocketEvents.GET_MODEL_TIP_SETT_SS)
        async def on_tip_settings(data):
            if isinstance(data, str):
                data = json.loads(data)
            ctrl.load_settings(data)
            self._settings = ctrl.settings
            self._settings_loaded.set()
            log.info("💎 Tip settings loaded!")

        @sio.on("tipperjs_notify_exec_tip_tc")
        async def on_tip_executed(data):
            """
            السيرفر بيبلغنا إن tip حقيقي اتبعت من المنصة.

            المصدر: tipper.js → _e() function
            الداتا: {camsite, modelName, customerName, token}
            """
            if isinstance(data, str):
                data = json.loads(data)
            tokens = data.get("token", 0)
            log.info(f"💰 REAL TIP from platform: {tokens} tokens!")
            if ctrl.settings and tokens > 0:
                await ctrl.execute_tip(tokens)

        @sio.on("control_link_toy_status")
        async def on_toy_status(data):
            if isinstance(data, str):
                data = json.loads(data)
            ctrl.on_toy_status(data)
            log.info(f"📱 Toy: {ctrl.toy}")

        @sio.on("control_link_not_in_queue_cs")
        async def on_not_in_queue(data):
            # هذا طبيعي - مش محتاجين نكون في الطابور
            # أوامر control_toy_cmd_cs بتتبعت مباشرة
            log.info("📋 Not in control queue (normal - sending commands directly)")

        @sio.on("end_broadcast_notice_cs")
        async def on_end_broadcast(data):
            log.warning("📡 Model went offline")

        @sio.on("VibeWithMeTipStatusDTO")
        async def on_vibe_tip(data):
            if isinstance(data, str):
                data = json.loads(data)
            log.info(f"💰 VibeWithMe tip: {data.get('tokens', 0)} tokens")

        @sio.on("tipperjs_notify_send_online_heartbeat_tc")
        async def on_heartbeat(data):
            await sio.emit("tipperjs_viewer_online_heartbeat_ts")

    # ── تحميل الإعدادات ───────────────────────────────────────

    async def load_settings(self) -> bool:
        """تحميل إعدادات اللوحة والتيبات من السيرفر."""
        pf = self.config["platform"]
        mod = self.config["model_name"]

        await self._sio.emit(SocketEvents.GET_DEVELOPER_PANEL_SETT_CS, {"pf": pf})
        await self._sio.emit(SocketEvents.GET_MODEL_TIP_SETT_CS, {"pf": pf, "modName": mod})

        try:
            await asyncio.wait_for(self._settings_loaded.wait(), timeout=10)
            return True
        except asyncio.TimeoutError:
            log.warning("Settings load timeout")
            return False

    # ── إرسال أوامر التحكم مباشرة ─────────────────────────────

    async def send_command(self, **modes: int):
        """
        إرسال أمر تحكم مباشر عبر control_toy_cmd_cs.

        المصدر: tipper.js → $$lvsSendDataToServer
            socket.emit("control_toy_cmd_cs", {
                pf: "flash",
                modName: "...",
                csName: "...",
                v: 10,   // vibrate 0-20
                r: 0,    // rotate 0-20
                f: 0,    // thrusting 0-20
                ...
            })

        لا يحتاج control link أو giveControl.
        يعمل مباشرة لو controlSwitch=true في إعدادات النموذج.
        """
        return await self._ctrl.send_command(**modes)

    async def execute_tip(self, tokens: int):
        """
        تنفيذ أوامر التحكم بناءً على قيمة التيبة.

        يبحث في إعدادات النموذج (basicLevel + specialCommand)
        ويبعت الأمر المناسب مباشرة عبر control_toy_cmd_cs.
        """
        self._tip_count += 1
        log.info(f"💰 [{self._tip_count}] Executing tip: {tokens} tokens")

        if self._settings:
            # بحث في specialCommand أولاً
            special = self._settings.find_special(tokens)
            if special:
                log.info(f"⭐ Special command: {special.cmd_type} for {special.duration}s")
                await self._execute_special(special)
                return

            # بحث في basicLevel
            level = self._settings.find_level(tokens)
            if level:
                log.info(f"📊 Basic level: {level}")
                await self._execute_level(level)
                return

        # fallback
        log.info(f"📊 Using default tip response")
        await self._ctrl.tip_response(tokens)

    async def _execute_level(self, level):
        """تنفيذ مستوى basicLevel."""
        if not level.levels:
            return
        await self._ctrl.send_command(**level.levels)
        await asyncio.sleep(level.duration)
        await self._ctrl.stop()

    async def _execute_special(self, cmd):
        """تنفيذ أمر خاص (earthquake/fireworks/wave/pulse)."""
        if cmd.cmd_type == "earthquake":
            await self._ctrl._pattern_earthquake(cmd.duration)
        elif cmd.cmd_type == "fireworks":
            await self._ctrl._pattern_fireworks(cmd.duration)
        elif cmd.cmd_type == "wave":
            await self._ctrl._pattern_wave(cmd.duration)
        elif cmd.cmd_type == "pulse":
            await self._ctrl._pattern_pulse_special(cmd.duration)
        elif cmd.cmd_type == "random":
            level = random.randint(1, 20)
            await self._ctrl.vibrate(level)
            await asyncio.sleep(random.randint(3, 10))
            await self._ctrl.stop()
        elif cmd.cmd_type == "randomTime":
            level = 15  # default from settings
            duration = random.randint(1, 6)
            await self._ctrl.vibrate(level)
            await asyncio.sleep(duration)
            await self._ctrl.stop()
        else:
            log.info(f"Unknown special: {cmd.cmd_type}")

    # ── حلقة التيبات التلقائية ─────────────────────────────────

    async def auto_tip_loop(self):
        cfg = self.tip_config
        amounts = cfg["tip_amounts"]
        mode = cfg["mode"]
        interval = cfg["interval_sec"]
        max_tips = cfg["max_tips"]
        idx = 0

        log.info(f"\n{'='*55}")
        log.info(f"  🚀 Auto-tip started!")
        log.info(f"  Mode: {mode} | Interval: {interval}s")
        log.info(f"  Amounts: {amounts}")
        log.info(f"  Max: {'unlimited' if max_tips == 0 else max_tips}")
        log.info(f"{'='*55}\n")

        while self._running:
            if self._stop_event.is_set():
                break
            if max_tips > 0 and self._tip_count >= max_tips:
                log.info(f"Reached max tips ({max_tips})")
                break

            # اختيار قيمة التيبة
            if mode == "random":
                tokens = random.choice(amounts)
            else:
                tokens = amounts[idx % len(amounts)]
                idx += 1

            # تنفيذ التيبة
            await self.execute_tip(tokens)

            # انتظار
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

        log.info(f"\n📊 Total tips: {self._tip_count}")

    # ── التشغيل الرئيسي ──────────────────────────────────────

    async def run(self):
        self._running = True
        self._stop_event.clear()

        print(f"\n{'█'*55}")
        print(f"  🤖 Lovense Auto-Tipper v3 (Direct Control)")
        print(f"{'█'*55}")
        print(f"\n  Platform:  {self.config['platform']}")
        print(f"  Model:     {self.config['model_name'][:25]}...")
        print()

        if not await self.connect():
            log.error("Failed to connect")
            return

        if not await self.load_settings():
            log.warning("Settings not loaded, using defaults")

        # عرض إعدادات التيبات
        if self._settings:
            print(f"\n  📊 Tip settings from server:")
            self._settings.print_summary()

        # تشغيل حلقة التيبات
        if self.tip_config["enabled"]:
            await self.auto_tip_loop()
        else:
            log.info("Auto-tip disabled. Staying connected...")
            await self._sio.wait()

    async def stop(self):
        self._running = False
        self._stop_event.set()
        if self._ctrl:
            try:
                await self._ctrl.stop()
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
        print(f"  🎮 Lovense Interactive Controller v3")
        print(f"{'█'*55}")

        if not await self.connect():
            return
        await self.load_settings()

        print(f"\n{'═'*55}")
        print("  الأوامر:")
        print("  ─────────────────────────────────────────────────")
        print("  tip <amount>           → تنفيذ أوامر tip (حسب إعدادات النموذج)")
        print("  vib <level> [seconds]  → اهتزاز مباشر (0-20)")
        print("  rot <level> [seconds]  → دوران (0-20)")
        print("  thrust <level> [sec]   → دفع (0-20)")
        print("  stop                   → إيقاف الجهاز")
        print("  earthquake [seconds]   → نمط زلزال")
        print("  fireworks [seconds]    → نمط ألعاب نارية")
        print("  wave [seconds]         → نمط موجة")
        print("  pulse [seconds]        → نبضات")
        print("  ramp                   → صعود وهبوط")
        print("  auto                   → تشغيل التيبات التلقائية")
        print("  status                 → حالة الاتصال")
        print("  settings               → عرض إعدادات التيبات")
        print("  quit                   → خروج")
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
                    await self.execute_tip(tokens)

                elif action == "vib" and len(parts) > 1:
                    level = int(parts[1])
                    duration = float(parts[2]) if len(parts) > 2 else 5.0
                    await self._ctrl.vibrate(level)
                    await asyncio.sleep(duration)
                    await self._ctrl.stop()

                elif action == "rot" and len(parts) > 1:
                    level = int(parts[1])
                    duration = float(parts[2]) if len(parts) > 2 else 5.0
                    await self._ctrl.rotate(level)
                    await asyncio.sleep(duration)
                    await self._ctrl.stop()

                elif action == "thrust" and len(parts) > 1:
                    level = int(parts[1])
                    duration = float(parts[2]) if len(parts) > 2 else 5.0
                    await self._ctrl.thrust(level)
                    await asyncio.sleep(duration)
                    await self._ctrl.stop()

                elif action == "stop":
                    await self._ctrl.stop()
                    log.info("⏹ Stopped")

                elif action in ("earthquake", "fireworks", "wave", "pulse"):
                    dur = int(parts[1]) if len(parts) > 1 else 10
                    if action == "earthquake":
                        await self._ctrl._pattern_earthquake(dur)
                    elif action == "fireworks":
                        await self._ctrl._pattern_fireworks(dur)
                    elif action == "wave":
                        await self._ctrl._pattern_wave(dur)
                    elif action == "pulse":
                        await self._ctrl._pattern_pulse_special(dur)

                elif action == "ramp":
                    await self._ctrl.ramp_up(0, 20, step=2, delay=0.3)
                    await self._ctrl.ramp_down(20, 0, step=2, delay=0.3)

                elif action == "auto":
                    self.tip_config["enabled"] = True
                    await self.auto_tip_loop()
                    self.tip_config["enabled"] = False

                elif action == "status":
                    print(f"  Connected:  {self._connected}")
                    print(f"  Tips sent:  {self._tip_count}")
                    if self._ctrl and self._ctrl.toy.toy_type:
                        print(f"  Toy:        {self._ctrl.toy}")
                        print(f"  Modes:      {self._ctrl.toy.supported_modes}")

                elif action == "settings":
                    if self._settings:
                        self._settings.print_summary()
                    else:
                        log.info("No settings loaded")

                else:
                    print(f"  ❓ Unknown: {cmd}")

            except ValueError as e:
                print(f"  ❌ Invalid value: {e}")
            except Exception as e:
                log.error(f"Error: {e}")

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
            pass
        finally:
            await tipper.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ Stopped")
