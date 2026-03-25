"""
toy_control.py — التحكم في أجهزة Lovense
==========================================

المصدر:
  - tipper.js  → SocketEvents + $$lvsSendDataToServer
  - 323_1cbdc51b.js (chunk 323) → module 8484 (toy supportMode)

التثبيت:
    pip install python-socketio[asyncio] aiohttp

الاستخدام السريع:
    controller = ToyController(socket_client)
    controller.vibrate(level=10)
    controller.stop()
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("ToyControl")


# ══════════════════════════════════════════════════════════════════════════════
# القسم 1: أوضاع التحكم لكل جهاز
# المصدر: module 8484 في 323_1cbdc51b.js
#
# كل جهاز له "supportMode" — قائمة الأوضاع التي يدعمها:
#   vibrate   = اهتزاز (0-20)
#   rotate    = دوران (0-20)
#   thrusting = دفع (0-20)
#   air       = ضغط هواء (0-3)
#   suction   = شفط (0-20)
#   depth     = عمق (0-3)
#   fingering = إصبع (0-3)
# ══════════════════════════════════════════════════════════════════════════════

TOY_MODES: Dict[str, Dict] = {
    # الكود الأصلي: module 8484
    "nora":          {"name": "Nora",          "modes": ["vibrate", "rotate"]},
    "max":           {"name": "Max",           "modes": ["vibrate", "air"]},
    "gravity":       {"name": "Gravity",       "modes": ["vibrate", "thrusting"]},
    "flexer":        {"name": "Flexer",        "modes": ["vibrate", "fingering"]},
    "tenera":        {"name": "Tenera",        "modes": ["vibrate", "suction"]},
    "xmachine":      {"name": "XMachine",      "modes": ["vibrate", "thrusting"]},
    "mini xmachine": {"name": "Mini XMachine", "modes": ["vibrate", "thrusting"]},
    "solace":        {"name": "Solace",        "modes": ["thrusting", "depth"]},
    "ridge":         {"name": "Ridge",         "modes": ["vibrate", "rotate"]},
    "spinel":        {"name": "Spinel",        "modes": ["vibrate", "thrusting"]},
    "velvo":         {"name": "Velvo",         "modes": ["vibrate", "rotate"]},
    "c24":           {"name": "C24",           "modes": ["vibrate", "rotate"]},
    # أجهزة من TOY_CATALOG (tipper.js) بدون multi-mode
    "lush":          {"name": "Lush",          "modes": ["vibrate"]},
    "hush":          {"name": "Hush",          "modes": ["vibrate"]},
    "ferri":         {"name": "Ferri",         "modes": ["vibrate"]},
    "domi":          {"name": "Domi",          "modes": ["vibrate"]},
    "osci":          {"name": "Osci",          "modes": ["vibrate"]},
    "ambi":          {"name": "Ambi",          "modes": ["vibrate"]},
    "edge":          {"name": "Edge",          "modes": ["vibrate"]},
    "diamo":         {"name": "Diamo",         "modes": ["vibrate"]},
    "dolce":         {"name": "Dolce",         "modes": ["vibrate"]},
    "mission":       {"name": "Mission",       "modes": ["vibrate"]},
    "hyphy":         {"name": "Hyphy",         "modes": ["vibrate"]},
    "calor":         {"name": "Calor",         "modes": ["vibrate"]},
    "gush":          {"name": "Gush",          "modes": ["vibrate"]},
}

# حدود مستويات التحكم لكل نوع
MODE_LIMITS: Dict[str, Dict] = {
    "vibrate":   {"min": 0, "max": 20, "step": 1},
    "rotate":    {"min": 0, "max": 20, "step": 1},
    "thrusting": {"min": 0, "max": 20, "step": 1},
    "suction":   {"min": 0, "max": 20, "step": 1},
    "air":       {"min": 0, "max": 3,  "step": 1},
    "depth":     {"min": 0, "max": 3,  "step": 1},
    "fingering": {"min": 0, "max": 3,  "step": 1},
}


def get_toy_modes(toy_type: str) -> List[str]:
    """
    أوضاع جهاز معين.

    مثال:
        get_toy_modes("nora")   → ["vibrate", "rotate"]
        get_toy_modes("lush")   → ["vibrate"]
        get_toy_modes("solace") → ["thrusting", "depth"]
    """
    info = TOY_MODES.get(toy_type.lower().strip())
    return info["modes"] if info else ["vibrate"]


def clamp(value: int, mode: str) -> int:
    """تقييد المستوى ضمن الحدود المسموح بها للوضع."""
    limits = MODE_LIMITS.get(mode, {"min": 0, "max": 20})
    return max(limits["min"], min(limits["max"], int(value)))


# ══════════════════════════════════════════════════════════════════════════════
# القسم 2: هيكل أمر التحكم
# المصدر: control_toy_cmd_cs في SocketEvents (tipper.js)
#
# البروتوكول:
#   socket.emit("control_toy_cmd_cs", {
#       "pf":      "chaturbate",
#       "modName": "model_alice",
#       "csName":  "viewer_bob",
#       "v":  10,   ← vibrate (0-20)
#       "r":  0,    ← rotate  (0-20)  [إذا كان الجهاز يدعمه]
#       ...
#   })
#
# خرائط أسماء الأوضاع → حروف الأمر:
#   vibrate   → "v"
#   rotate    → "r"
#   thrusting → "f"
#   air       → "a"
#   suction   → "p"
#   depth     → "d"
#   fingering → "g"
# ══════════════════════════════════════════════════════════════════════════════

MODE_TO_KEY: Dict[str, str] = {
    "vibrate":   "v",
    "rotate":    "r",
    "thrusting": "f",   # f = forward/thrusting
    "air":       "a",
    "suction":   "p",   # p = pressure/suction
    "depth":     "d",
    "fingering": "g",
}

KEY_TO_MODE: Dict[str, str] = {v: k for k, v in MODE_TO_KEY.items()}


@dataclass
class ToyCommand:
    """
    أمر التحكم في الجهاز.

    المصدر: الـ payload المُرسَل مع control_toy_cmd_cs

    مثال:
        cmd = ToyCommand(pf="chaturbate", mod_name="alice", cs_name="bob")
        cmd.set("vibrate", 15)
        cmd.set("rotate", 5)
        print(cmd.to_socket_payload())
        # {"pf":"chaturbate","modName":"alice","csName":"bob","v":15,"r":5}
    """
    pf:       str
    mod_name: str
    cs_name:  str
    levels:   Dict[str, int] = field(default_factory=dict)

    def set(self, mode: str, level: int) -> "ToyCommand":
        """تعيين مستوى وضع معين."""
        self.levels[mode] = clamp(level, mode)
        return self

    def set_all_stop(self) -> "ToyCommand":
        """إيقاف جميع الأوضاع."""
        self.levels = {m: 0 for m in self.levels}
        return self

    def to_socket_payload(self) -> dict:
        """
        تحويل الأمر إلى الـ payload المطلوب لـ Socket.IO.

        الكود الأصلي (Vue component):
            this.$$lvsSendDataToServer({
                event: "control_toy_cmd_cs",
                data:  { pf, modName, csName, v: vibrateLevel, r: rotateLevel, ... }
            })
        """
        payload = {
            "pf":      self.pf,
            "modName": self.mod_name,
            "csName":  self.cs_name,
        }
        for mode, level in self.levels.items():
            key = MODE_TO_KEY.get(mode)
            if key:
                payload[key] = level
        return payload

    def __repr__(self):
        levels_str = ", ".join(f"{m}={v}" for m, v in self.levels.items())
        return f"ToyCommand({self.pf}/{self.mod_name} | {levels_str})"


# ══════════════════════════════════════════════════════════════════════════════
# القسم 3: حالة الجهاز الحالية
# المصدر: control_link_toy_status event
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToyStatus:
    """
    حالة الجهاز المُرسَلة من الخادم.

    المصدر: حدث control_link_toy_status عبر Socket.IO
    """
    toy_id:      str  = ""
    toy_name:    str  = ""
    toy_type:    str  = ""     # نوع الجهاز (lush, nora, max, ...)
    toy_version: str  = ""
    status:      int  = 0      # 0=غير متصل, 1=متصل
    battery:     int  = 0      # نسبة البطارية (0-100)
    connected:   bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ToyStatus":
        """إنشاء من بيانات الحدث."""
        return cls(
            toy_id      = data.get("toyId", ""),
            toy_name    = data.get("toyName", ""),
            toy_type    = data.get("toyType", "").lower(),
            toy_version = data.get("fVersion", ""),
            status      = int(data.get("status", 0)),
            battery     = int(data.get("battery", 0)),
            connected   = int(data.get("status", 0)) == 1,
        )

    @property
    def supported_modes(self) -> List[str]:
        return get_toy_modes(self.toy_type)

    def __repr__(self):
        bat = f"🔋{self.battery}%" if self.battery else ""
        status = "🟢 متصل" if self.connected else "🔴 منفصل"
        return f"ToyStatus({self.toy_name or self.toy_type} | {status} {bat})"


# ══════════════════════════════════════════════════════════════════════════════
# القسم 4: ToyController — واجهة التحكم الرئيسية
# ══════════════════════════════════════════════════════════════════════════════

class ToyController:
    """
    واجهة التحكم في جهاز Lovense عبر Socket.IO.

    الاستخدام:
        # بعد الاتصال بـ WebSocket وبدء جلسة control link
        controller = ToyController(
            sio_client  = sio,         # AsyncClient من python-socketio
            pf          = "chaturbate",
            mod_name    = "model_alice",
            cs_name     = "viewer_bob",
        )

        # بدء جلسة التحكم
        await controller.request_control()

        # التحكم
        await controller.vibrate(15)
        await asyncio.sleep(5)
        await controller.stop()
    """

    CONTROL_EVENT = "control_toy_cmd_cs"

    def __init__(self, sio_client, pf: str, mod_name: str, cs_name: str):
        """
        المعاملات:
            sio_client: كائن socketio.AsyncClient متصل
            pf:         رمز المنصة
            mod_name:   اسم/مفتاح النموذج
            cs_name:    اسم/مفتاح المشاهد
        """
        self._sio      = sio_client
        self._pf       = pf
        self._mod      = mod_name
        self._cs       = cs_name
        self._toy      = ToyStatus()
        self._in_ctrl  = False       # هل نحن في جلسة تحكم نشطة
        self._listeners: List[Callable] = []
        self._current_levels: Dict[str, int] = {}

    # ── إدارة جلسة التحكم ────────────────────────────────────────

    async def request_control(self):
        """
        طلب بدء جلسة التحكم.

        المصدر:
            socket.emit("get_control_link_cs", {pf, modName, csName})
            ← يُرسل من tipper.js بعد تهيئة الجلسة

        بعد هذا الطلب يصلك حدث:
            control_link_ready_notice_cs → يعني التحكم مفتوح
        """
        payload = {"pf": self._pf, "modName": self._mod, "csName": self._cs}
        await self._sio.emit("get_control_link_cs", payload)
        logger.info(f"🎮 Control link requested: {self._pf}/{self._mod}/{self._cs}")

    async def start_control(self, control_type: str = "1"):
        """
        بدء التحكم بعد الحصول على رابط التحكم.

        المصدر: socket.emit("start_control_link_cs", {...})
        يُرسَل عادةً بعد استقبال control_link_ready_notice_cs.

        المعاملات:
            control_type: "1" = تحكم عادي (الافتراضي)
        """
        payload = {
            "pf":          self._pf,
            "modName":     self._mod,
            "csName":      self._cs,
            "controlType": control_type,
        }
        await self._sio.emit("start_control_link_cs", payload)
        self._in_ctrl = True
        logger.info("🟢 Control started")

    async def end_control(self):
        """
        إنهاء جلسة التحكم.

        المصدر: socket.emit("tipper_end_control_cs", {...})
        """
        await self._sio.emit("tipper_end_control_cs", {
            "pf":      self._pf,
            "modName": self._mod,
            "csName":  self._cs,
        })
        self._in_ctrl = False
        self._current_levels = {}
        logger.info("🔴 Control ended")

    # ── أوامر التحكم المباشرة ─────────────────────────────────────

    async def send_command(self, **modes: int):
        """
        إرسال أمر تحكم مخصص.

        المصدر:
            socket.emit("control_toy_cmd_cs", {pf, modName, csName, v, r, f, ...})

        المعاملات (keyword args):
            vibrate   = مستوى الاهتزاز (0-20)
            rotate    = مستوى الدوران  (0-20)
            thrusting = مستوى الدفع    (0-20)
            air       = ضغط الهواء     (0-3)
            suction   = مستوى الشفط    (0-20)
            depth     = العمق           (0-3)
            fingering = مستوى الإصبع   (0-3)

        مثال:
            await controller.send_command(vibrate=15, rotate=5)
            await controller.send_command(vibrate=0)   # إيقاف الاهتزاز
        """
        cmd = ToyCommand(pf=self._pf, mod_name=self._mod, cs_name=self._cs)
        for mode, level in modes.items():
            cmd.set(mode, level)
            self._current_levels[mode] = clamp(level, mode)

        payload = cmd.to_socket_payload()
        await self._sio.emit(self.CONTROL_EVENT, payload)

        levels_str = " | ".join(f"{m}={v}" for m, v in modes.items())
        logger.info(f"📤 Sent: {levels_str}")
        return payload

    async def vibrate(self, level: int):
        """
        تشغيل الاهتزاز.

        المعاملات:
            level: 0-20 (0 = إيقاف)
        """
        return await self.send_command(vibrate=level)

    async def rotate(self, level: int):
        """
        تشغيل الدوران (Nora, Ridge, Velvo, C24 فقط).

        المعاملات:
            level: 0-20 (0 = إيقاف)
        """
        return await self.send_command(rotate=level)

    async def thrust(self, level: int):
        """
        تشغيل الدفع/الحركة الخطية (Gravity, XMachine, Solace...).

        المعاملات:
            level: 0-20 (0 = إيقاف)
        """
        return await self.send_command(thrusting=level)

    async def air(self, level: int):
        """
        ضغط الهواء (Max فقط).

        المعاملات:
            level: 0-3
        """
        return await self.send_command(air=level)

    async def suction(self, level: int):
        """
        الشفط (Tenera فقط).

        المعاملات:
            level: 0-20
        """
        return await self.send_command(suction=level)

    async def depth(self, level: int):
        """
        العمق (Solace فقط).

        المعاملات:
            level: 0-3
        """
        return await self.send_command(depth=level)

    async def fingering(self, level: int):
        """
        حركة الإصبع (Flexer فقط).

        المعاملات:
            level: 0-3
        """
        return await self.send_command(fingering=level)

    async def stop(self):
        """
        إيقاف جميع الأوضاع فوراً.

        يُرسل 0 لجميع الأوضاع النشطة حالياً.
        """
        stop_modes = {mode: 0 for mode in self._current_levels}
        if not stop_modes:
            # إيقاف افتراضي بالاهتزاز
            stop_modes = {"vibrate": 0}
        return await self.send_command(**stop_modes)

    # ── أنماط تشغيل جاهزة ────────────────────────────────────────

    async def pulse(self, peak: int = 20, duration: float = 0.5, cycles: int = 3):
        """
        نبضات متكررة: رفع → خفض → رفع...

        المعاملات:
            peak:     المستوى الأقصى (0-20)
            duration: مدة كل نبضة بالثواني
            cycles:   عدد الدورات
        """
        logger.info(f"💫 Pulse: peak={peak}, duration={duration}s, cycles={cycles}")
        for i in range(cycles):
            await self.vibrate(peak)
            await asyncio.sleep(duration)
            await self.vibrate(0)
            await asyncio.sleep(duration / 2)
        logger.info("✅ Pulse complete")

    async def ramp_up(self, start: int = 0, end: int = 20,
                      step: int = 2, delay: float = 0.5):
        """
        صعود تدريجي من start إلى end.

        المعاملات:
            start: مستوى البداية
            end:   مستوى النهاية
            step:  مقدار الزيادة في كل خطوة
            delay: التأخير بين الخطوات (ثانية)
        """
        logger.info(f"📈 Ramp up: {start} → {end} (step={step})")
        level = start
        while level <= end:
            await self.vibrate(level)
            await asyncio.sleep(delay)
            level += step

    async def ramp_down(self, start: int = 20, end: int = 0,
                        step: int = 2, delay: float = 0.5):
        """
        هبوط تدريجي من start إلى end.
        """
        logger.info(f"📉 Ramp down: {start} → {end} (step={step})")
        level = start
        while level >= end:
            await self.vibrate(level)
            await asyncio.sleep(delay)
            level -= step

    async def pattern(self, levels: List[int], delay: float = 0.5, repeat: int = 1):
        """
        تشغيل نمط مخصص من المستويات.

        مثال:
            await controller.pattern([5, 10, 15, 20, 10, 5, 0], delay=0.3)
            await controller.pattern([20, 0, 20, 0], delay=0.2, repeat=3)
        """
        logger.info(f"🎵 Pattern: {levels} × {repeat}")
        for _ in range(repeat):
            for level in levels:
                await self.vibrate(level)
                await asyncio.sleep(delay)

    async def tip_response(self, token_amount: int):
        """
        استجابة تلقائية للإكرامية بناءً على مقدار التوكنات.

        المبدأ: كلما كانت الإكرامية أكبر، كان الاهتزاز أقوى وأطول.

        المعاملات:
            token_amount: عدد التوكنات المُهداة
        """
        if token_amount <= 0:
            return

        # حساب المستوى والمدة
        if token_amount < 10:
            level, duration = 5,  2.0
        elif token_amount < 50:
            level, duration = 10, 3.0
        elif token_amount < 100:
            level, duration = 15, 5.0
        elif token_amount < 500:
            level, duration = 18, 8.0
        else:
            level, duration = 20, 10.0

        logger.info(f"💰 Tip response: {token_amount} tokens → level={level} for {duration}s")
        await self.vibrate(level)
        await asyncio.sleep(duration)
        await self.stop()

    # ── استقبال حالة الجهاز ──────────────────────────────────────

    def on_toy_status(self, data: dict):
        """
        معالجة حدث control_link_toy_status من الخادم.

        المصدر:
            socket.on("control_link_toy_status", handler)

        يُحدِّث حالة الجهاز المحلية ويُطلق المستمعين.
        """
        if isinstance(data, str):
            data = json.loads(data)

        self._toy = ToyStatus.from_dict(data)
        logger.info(f"📱 Toy status: {self._toy}")

        for cb in self._listeners:
            try:
                cb(self._toy)
            except Exception as e:
                logger.error(f"Toy status listener error: {e}")

    def add_status_listener(self, callback: Callable[[ToyStatus], None]):
        """
        إضافة مستمع لتحديثات حالة الجهاز.

        المعاملات:
            callback: دالة تستقبل ToyStatus
        """
        self._listeners.append(callback)

    # ── Properties ────────────────────────────────────────────────

    @property
    def toy(self) -> ToyStatus:
        """الحالة الحالية للجهاز."""
        return self._toy

    @property
    def in_control(self) -> bool:
        """هل جلسة التحكم نشطة."""
        return self._in_ctrl

    @property
    def current_levels(self) -> Dict[str, int]:
        """المستويات الحالية لجميع الأوضاع."""
        return dict(self._current_levels)

    @property
    def supported_modes(self) -> List[str]:
        """أوضاع الجهاز الحالي."""
        return self._toy.supported_modes


# ══════════════════════════════════════════════════════════════════════════════
# القسم 5: مثال الاستخدام الكامل مع WebSocket
# ══════════════════════════════════════════════════════════════════════════════

FULL_EXAMPLE = '''
"""
مثال كامل: الاتصال + التحكم في الجهاز
"""
import asyncio
import socketio
from tipper import get_lovense, generate_init_signature
from toy_control import ToyController, ToyStatus

# ── الإعدادات ─────────────────────────────────────────────────────
PF        = "chaturbate"
MOD_NAME  = "model_alice"
CS_NAME   = "viewer_bob"

# ── الاتصال والتحكم ───────────────────────────────────────────────
async def main():
    # 1. تهيئة API
    lovense = get_lovense()
    lovense.init(PF, MOD_NAME, CS_NAME)

    # 2. جلب بيانات الجلسة (init payload)
    import requests
    payload = lovense.build_init_payload()
    resp = requests.post(
        "https://display.lovense-api.com/ws/customer/init",
        data=payload, timeout=15
    ).json()

    if resp.get("code") != 0:
        print(f"❌ Init failed: {resp.get('message')}")
        return

    ws_url  = lovense.build_socket_url(resp["data"]["ws_server_url"])
    io_path = resp["data"].get("socketIoPath", "/customer")

    # 3. الاتصال بـ WebSocket
    sio = socketio.AsyncClient()

    # إنشاء ToyController
    controller = ToyController(sio, PF, MOD_NAME, CS_NAME)

    # مستمع حالة الجهاز
    def on_toy_update(status: ToyStatus):
        print(f"📱 {status}")
        if status.connected:
            print(f"   أوضاع: {status.supported_modes}")

    controller.add_status_listener(on_toy_update)

    @sio.event
    async def connect():
        print("🟢 Connected!")
        # 4. طلب رابط التحكم
        await controller.request_control()

    @sio.on("control_link_ready_notice_cs")
    async def on_control_ready(data):
        print("🎮 Control link ready!")
        # 5. بدء التحكم
        await controller.start_control()

    @sio.on("control_link_toy_status")
    async def on_toy_status(data):
        controller.on_toy_status(data)

    @sio.on("VibeWithMeTipStatusDTO")
    async def on_tip(data):
        import json
        d = json.loads(data) if isinstance(data, str) else data
        tokens = d.get("tokens", 0)
        print(f"💰 Tip: {tokens} tokens!")
        # استجابة تلقائية للإكرامية
        await controller.tip_response(tokens)

    @sio.on("control_link_in_queue_notice_cs")
    async def on_queue(data):
        print("⏳ في قائمة الانتظار...")

    @sio.on("end_control_link_notice_cs")
    async def on_end(data):
        print("🔴 Control link ended")

    # 6. الاتصال
    await sio.connect(ws_url, socketio_path=io_path, transports=["websocket"])

    # 7. انتظار الاتصال ثم التحكم
    await asyncio.sleep(2)

    if controller.in_control and controller.toy.connected:
        print("\\n=== بدء أنماط التحكم ===")

        # اهتزاز بسيط
        print("▶ اهتزاز مستوى 10 لـ 3 ثوانٍ")
        await controller.vibrate(10)
        await asyncio.sleep(3)
        await controller.stop()
        await asyncio.sleep(1)

        # صعود تدريجي
        print("▶ صعود تدريجي 0 → 20")
        await controller.ramp_up(start=0, end=20, step=4, delay=0.5)
        await asyncio.sleep(1)

        # هبوط تدريجي
        print("▶ هبوط تدريجي 20 → 0")
        await controller.ramp_down(start=20, end=0, step=4, delay=0.5)
        await asyncio.sleep(1)

        # نبضات
        print("▶ نبضات × 3")
        await controller.pulse(peak=15, duration=0.5, cycles=3)
        await asyncio.sleep(1)

        # نمط مخصص
        print("▶ نمط مخصص")
        await controller.pattern([5,10,15,20,15,10,5,0], delay=0.3, repeat=2)

        # للـ Nora (تحكم مزدوج)
        if "rotate" in controller.supported_modes:
            print("▶ اهتزاز + دوران (Nora)")
            await controller.send_command(vibrate=10, rotate=8)
            await asyncio.sleep(3)
            await controller.stop()

        # إنهاء التحكم
        await controller.end_control()
        print("✅ انتهى التحكم")

    await sio.wait()

if __name__ == "__main__":
    asyncio.run(main())
'''


# ══════════════════════════════════════════════════════════════════════════════
# اختبار محلي
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import random

    print("=" * 55)
    print("  ToyControl — اختبار محلي")
    print("=" * 55)

    # ── 1. أوضاع الأجهزة ──────────────────────────────────────────
    print("\n[1] أوضاع الأجهزة:")
    devices = ["nora", "lush", "max", "solace", "gravity", "tenera", "flexer"]
    for d in devices:
        modes = get_toy_modes(d)
        limits = [f"{m}(0-{MODE_LIMITS[m]['max']})" for m in modes]
        print(f"  {d:<15} → {', '.join(limits)}")

    # ── 2. بناء أوامر التحكم ──────────────────────────────────────
    print("\n[2] بناء أوامر التحكم:")

    # أمر اهتزاز بسيط
    cmd1 = ToyCommand(pf="chaturbate", mod_name="model_alice", cs_name="viewer_bob")
    cmd1.set("vibrate", 15)
    p1 = cmd1.to_socket_payload()
    print(f"  اهتزاز 15:  {p1}")
    assert p1["v"] == 15

    # أمر مزدوج (Nora)
    cmd2 = ToyCommand(pf="chaturbate", mod_name="model_alice", cs_name="viewer_bob")
    cmd2.set("vibrate", 10).set("rotate", 8)
    p2 = cmd2.to_socket_payload()
    print(f"  Nora (v+r): {p2}")
    assert p2["v"] == 10 and p2["r"] == 8

    # أمر Max (vibrate + air)
    cmd3 = ToyCommand(pf="chaturbate", mod_name="model_alice", cs_name="viewer_bob")
    cmd3.set("vibrate", 12).set("air", 2)
    p3 = cmd3.to_socket_payload()
    print(f"  Max (v+a):  {p3}")
    assert p3["v"] == 12 and p3["a"] == 2

    # clamp test
    cmd4 = ToyCommand(pf="chaturbate", mod_name="model_alice", cs_name="viewer_bob")
    cmd4.set("vibrate", 99).set("air", 99)   # يجب تقليصها
    p4 = cmd4.to_socket_payload()
    print(f"  Clamp test: {p4}")
    assert p4["v"] == 20 and p4["a"] == 3, f"Clamp failed: {p4}"

    # ── 3. ToyStatus ──────────────────────────────────────────────
    print("\n[3] حالة الجهاز:")
    status = ToyStatus.from_dict({
        "toyId":    "abc123",
        "toyName":  "Lush 3",
        "toyType":  "lush",
        "fVersion": "3.2",
        "status":   1,
        "battery":  85,
    })
    print(f"  {status}")
    print(f"  أوضاع: {status.supported_modes}")
    assert status.connected and status.battery == 85

    nora_status = ToyStatus.from_dict({"toyType": "nora", "status": 1})
    print(f"  Nora modes: {nora_status.supported_modes}")
    assert "rotate" in nora_status.supported_modes

    # ── 4. محاكاة ToyController ───────────────────────────────────
    print("\n[4] محاكاة ToyController:")

    sent_commands = []

    class MockSocket:
        """محاكاة Socket.IO للاختبار"""
        async def emit(self, event, data=None):
            sent_commands.append({"event": event, "data": data})
            print(f"  📤 emit('{event}', {data})")

    async def run_controller_test():
        mock = MockSocket()
        ctrl = ToyController(mock, "chaturbate", "model_alice", "viewer_bob")

        # محاكاة استقبال حالة الجهاز
        ctrl.on_toy_status({"toyType": "nora", "status": 1, "battery": 90})

        print("  --- أوامر الإرسال ---")
        await ctrl.vibrate(10)
        await ctrl.rotate(5)
        await ctrl.send_command(vibrate=15, rotate=8)
        await ctrl.stop()

        print(f"\n  أوامر مُرسَلة: {len(sent_commands)}")
        print(f"  الجهاز: {ctrl.toy}")
        print(f"  أوضاع مدعومة: {ctrl.supported_modes}")

        assert len(sent_commands) == 4
        assert sent_commands[0]["data"]["v"] == 10
        assert sent_commands[1]["data"]["r"] == 5
        assert sent_commands[2]["data"]["v"] == 15
        assert sent_commands[2]["data"]["r"] == 8

    asyncio.run(run_controller_test())

    # ── 5. استجابة الإكراميات ─────────────────────────────────────
    print("\n[5] جدول استجابة الإكراميات:")
    tips = [5, 10, 50, 100, 200, 500, 1000]
    for t in tips:
        if t < 10:     level, dur = 5,  2.0
        elif t < 50:   level, dur = 10, 3.0
        elif t < 100:  level, dur = 15, 5.0
        elif t < 500:  level, dur = 18, 8.0
        else:          level, dur = 20, 10.0
        bar = "█" * level
        print(f"  {t:>5} tokens → مستوى {level:>2}/20  {dur:.0f}s  {bar}")

    # ── 6. قائمة أسماء الأوضاع والمفاتيح ─────────────────────────
    print("\n[6] خريطة الأوضاع → مفاتيح Socket:")
    for mode, key in MODE_TO_KEY.items():
        limits = MODE_LIMITS[mode]
        print(f"  {mode:<12} → '{key}'  (0-{limits['max']})")

    print("\n✅ جميع الاختبارات نجحت!")
    print()
    print("للاستخدام الكامل مع WebSocket الحقيقي، راجع:")
    print("  controller = ToyController(sio, pf, mod_name, cs_name)")
    print("  await controller.request_control()")
    print("  await controller.vibrate(15)")


# ══════════════════════════════════════════════════════════════════════════════
# القسم 6: TipSettings — إعدادات الإكرامية الحقيقية من الخادم
# المصدر: get_model_tip_sett_ss → tipSetting JSON
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TipLevel:
    """
    مستوى إكرامية واحد من basicLevel.

    مثال من الخادم:
        {"tipBegin":1,"tipEnd":7,"time":3,"v":5,"r":0,"f":0,...}
    """
    tip_begin: int
    tip_end:   int           # -1 = infinity
    duration:  int           # بالثواني
    levels:    Dict[str, int] = field(default_factory=dict)  # {"v":5,"r":0,...}

    @classmethod
    def from_dict(cls, d: dict) -> "TipLevel":
        tip_end_raw = d.get("tipEnd", 0)
        tip_end = -1 if str(tip_end_raw) == "infinity" else int(tip_end_raw)
        levels = {}
        for key in ["v", "r", "f", "a", "p", "d", "g", "o", "s", "t", "m"]:
            val = int(d.get(key, 0))
            if val > 0:
                mode = {v: k for k, v in MODE_TO_KEY.items()}.get(key, key)
                levels[mode] = val
        return cls(
            tip_begin=int(d.get("tipBegin", 0)),
            tip_end=tip_end,
            duration=int(d.get("time", 3)),
            levels=levels,
        )

    def matches(self, tokens: int) -> bool:
        if self.tip_end == -1:
            return tokens >= self.tip_begin
        return self.tip_begin <= tokens <= self.tip_end

    def __repr__(self):
        end = "∞" if self.tip_end == -1 else str(self.tip_end)
        lvl = " ".join(f"{m}={v}" for m, v in self.levels.items())
        return f"TipLevel({self.tip_begin}-{end}t → {lvl} for {self.duration}s)"


@dataclass
class SpecialCommand:
    """
    أمر خاص من specialCommand.

    الأنواع:
        giveControl  → يعطي المشاهد التحكم المباشر
        earthquake   → نمط هزة أرضية
        fireworks    → نمط ألعاب نارية
        wave         → نمط موجة
        pulse        → نمط نبضات
    """
    cmd_type:     str   # giveControl / earthquake / fireworks / wave / pulse
    tokens:       int   # عدد التوكنات المطلوب
    tokens_end:   int   # -1 إذا نقطة محددة
    duration:     int   # بالثواني
    control_type: str   = ""   # normal / (للـ giveControl فقط)
    token_per_min:int   = 0    # تكلفة التحكم في الدقيقة

    @classmethod
    def from_dict(cls, d: dict) -> "SpecialCommand":
        tokens_begin = int(d.get("tokensBegin") or d.get("tokens", 0))
        tokens_end   = int(d.get("tokensEnd", tokens_begin))
        return cls(
            cmd_type     = d.get("type", ""),
            tokens       = tokens_begin,
            tokens_end   = tokens_end,
            duration     = int(d.get("time", 0)),
            control_type = d.get("controlType", ""),
            token_per_min= int(d.get("tokenPerMin", 0)),
        )

    def matches(self, tokens: int) -> bool:
        return self.tokens <= tokens <= self.tokens_end

    def __repr__(self):
        if self.cmd_type == "giveControl":
            return f"SpecialCmd({self.tokens}t → giveControl/{self.control_type} {self.duration}s @ {self.token_per_min}t/min)"
        return f"SpecialCmd({self.tokens}t → {self.cmd_type} {self.duration}s)"


class TipSettings:
    """
    إعدادات الإكرامية الكاملة المُحمَّلة من get_model_tip_sett_ss.

    الاستخدام:
        settings = TipSettings.from_server(data)
        cmd = settings.find_command(tokens=50)  # basicLevel
        special = settings.find_special(tokens=111)  # giveControl
    """

    def __init__(self):
        self.basic_levels:    List[TipLevel]      = []
        self.special_commands: List[SpecialCommand] = []

    @classmethod
    def from_server(cls, raw_data: dict) -> "TipSettings":
        """
        بناء من بيانات get_model_tip_sett_ss.

        المصدر: data.tipSetting (JSON string داخل JSON)
        """
        obj = cls()
        tip_setting_str = raw_data.get("tipSetting", "{}")
        if isinstance(tip_setting_str, str):
            try:
                tip_setting = json.loads(tip_setting_str)
            except Exception:
                return obj
        else:
            tip_setting = tip_setting_str

        for lvl in tip_setting.get("basicLevel", []):
            obj.basic_levels.append(TipLevel.from_dict(lvl))

        for cmd in tip_setting.get("specialCommand", []):
            obj.special_commands.append(SpecialCommand.from_dict(cmd))

        return obj

    def find_level(self, tokens: int) -> Optional[TipLevel]:
        """البحث عن مستوى الاهتزاز المناسب لعدد التوكنات."""
        for lvl in self.basic_levels:
            if lvl.matches(tokens):
                return lvl
        return None

    def find_special(self, tokens: int) -> Optional[SpecialCommand]:
        """البحث عن أمر خاص مناسب لعدد التوكنات."""
        for cmd in self.special_commands:
            if cmd.matches(tokens):
                return cmd
        return None

    def print_summary(self):
        """طباعة ملخص الإعدادات."""
        print(f"  📊 basicLevel ({len(self.basic_levels)} مستوى):")
        for lvl in self.basic_levels:
            end = "∞" if lvl.tip_end == -1 else str(lvl.tip_end)
            lvl_str = " ".join(f"{m}={v}" for m, v in lvl.levels.items()) or "—"
            print(f"     {lvl.tip_begin:>4}-{end:<6} tokens → {lvl_str} لمدة {lvl.duration}s")

        print(f"\n  ⭐ specialCommand ({len(self.special_commands)} أمر):")
        for cmd in self.special_commands:
            print(f"     {cmd.tokens:>4} tokens → {cmd.cmd_type:<12} {cmd.duration}s"
                  + (f" @ {cmd.token_per_min}t/min" if cmd.token_per_min else ""))


# ══════════════════════════════════════════════════════════════════════════════
# القسم 7: ToyControllerAdvanced — تحكم متقدم مع TipSettings
# ══════════════════════════════════════════════════════════════════════════════

class ToyControllerAdvanced(ToyController):
    """
    نسخة متقدمة من ToyController تدعم:
      - التنفيذ التلقائي لإعدادات الإكرامية الحقيقية من الخادم
      - الأوامر الخاصة (earthquake / fireworks / wave / pulse / giveControl)
      - إدارة حالة control_link_not_in_queue_cs

    الاستخدام:
        ctrl = ToyControllerAdvanced(sio, pf, mod, cs)
        ctrl.load_settings(tip_settings_raw)

        # استجابة تلقائية بإعدادات النموذج الحقيقية
        await ctrl.execute_tip(tokens=50)
    """

    def __init__(self, sio_client, pf: str, mod_name: str, cs_name: str):
        super().__init__(sio_client, pf, mod_name, cs_name)
        self._settings: Optional[TipSettings] = None
        self._give_ctrl_active = False

    def load_settings(self, raw_data: dict):
        """
        تحميل إعدادات الإكرامية من بيانات get_model_tip_sett_ss.
        """
        self._settings = TipSettings.from_server(raw_data)
        logger.info(f"⚙️  TipSettings loaded: "
                    f"{len(self._settings.basic_levels)} levels, "
                    f"{len(self._settings.special_commands)} specials")
        self._settings.print_summary()

    async def execute_tip(self, tokens: int):
        """
        تنفيذ استجابة الإكرامية حسب إعدادات النموذج الحقيقية.

        يبحث أولاً في specialCommand، ثم في basicLevel.

        المعاملات:
            tokens: عدد التوكنات المُهداة
        """
        if not self._settings:
            # fallback للاستجابة الافتراضية
            await self.tip_response(tokens)
            return

        # ── بحث في الأوامر الخاصة أولاً ──
        special = self._settings.find_special(tokens)
        if special:
            await self._execute_special(special, tokens)
            return

        # ── بحث في basicLevel ──
        level = self._settings.find_level(tokens)
        if level:
            await self._execute_basic_level(level)
        else:
            logger.info(f"💰 {tokens} tokens → لا يوجد مستوى مناسب")

    async def _execute_basic_level(self, level: TipLevel):
        """تنفيذ مستوى basicLevel."""
        logger.info(f"💰 Executing: {level}")
        if not level.levels:
            return

        # إرسال الأوامر
        await self.send_command(**level.levels)
        await asyncio.sleep(level.duration)
        await self.stop()

    async def _execute_special(self, cmd: SpecialCommand, tokens: int):
        """تنفيذ أمر خاص."""
        logger.info(f"⭐ Special: {cmd}")

        if cmd.cmd_type == "giveControl":
            await self._execute_give_control(cmd)
        elif cmd.cmd_type == "earthquake":
            await self._pattern_earthquake(cmd.duration)
        elif cmd.cmd_type == "fireworks":
            await self._pattern_fireworks(cmd.duration)
        elif cmd.cmd_type == "wave":
            await self._pattern_wave(cmd.duration)
        elif cmd.cmd_type == "pulse":
            await self._pattern_pulse_special(cmd.duration)

    async def _execute_give_control(self, cmd: SpecialCommand):
        """
        giveControl — يُعطي المشاهد التحكم المباشر.

        المصدر: specialCommand type=giveControl في tipSettings
        يُرسل start_control_link_cs بعد طلب get_control_link_cs.
        """
        logger.info(f"🎮 giveControl: {cmd.duration}s @ {cmd.token_per_min}t/min")
        await self.request_control()
        await asyncio.sleep(1)
        await self.start_control(cmd.control_type or "1")
        self._give_ctrl_active = True

        # إيقاف التحكم بعد المدة المحددة
        async def end_after():
            await asyncio.sleep(cmd.duration)
            if self._give_ctrl_active:
                await self.end_control()
                self._give_ctrl_active = False
                logger.info("🔚 giveControl ended (timeout)")

        asyncio.create_task(end_after())

    # ── أنماط الأوامر الخاصة ──────────────────────────────────────

    async def _pattern_earthquake(self, duration: int):
        """
        🌍 earthquake — اهتزاز متقطع سريع يشبه الزلزال.
        """
        import random
        logger.info(f"🌍 Earthquake pattern for {duration}s")
        end_time = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end_time:
            level = random.randint(10, 20)
            await self.vibrate(level)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await self.vibrate(0)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.stop()

    async def _pattern_fireworks(self, duration: int):
        """
        🎆 fireworks — صعود سريع ثم انفجار ثم هبوط.
        """
        logger.info(f"🎆 Fireworks pattern for {duration}s")
        cycles = max(1, duration // 4)
        for _ in range(cycles):
            # صعود سريع
            for v in range(0, 21, 5):
                await self.vibrate(v)
                await asyncio.sleep(0.1)
            # انفجار
            await self.vibrate(20)
            await asyncio.sleep(0.5)
            # هبوط
            for v in range(20, -1, -5):
                await self.vibrate(v)
                await asyncio.sleep(0.15)
            await asyncio.sleep(0.3)
        await self.stop()

    async def _pattern_wave(self, duration: int):
        """
        🌊 wave — موجة صعود وهبوط ناعمة ومتكررة.
        """
        logger.info(f"🌊 Wave pattern for {duration}s")
        end_time = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end_time:
            for v in list(range(0, 21, 2)) + list(range(20, -1, -2)):
                if asyncio.get_event_loop().time() >= end_time:
                    break
                await self.vibrate(v)
                await asyncio.sleep(0.15)
        await self.stop()

    async def _pattern_pulse_special(self, duration: int):
        """
        💫 pulse — نبضات قوية ومتكررة.
        """
        logger.info(f"💫 Pulse pattern for {duration}s")
        cycles = max(1, duration // 2)
        await self.pulse(peak=20, duration=0.4, cycles=cycles)
        await self.stop()

    @property
    def settings(self) -> Optional[TipSettings]:
        return self._settings
