"""c07_pv_bridge.py —— 把一块 20 块钱的开发板变成 EPICS 设备（第 7 章）

这是本课程"从零到能干活"的关键一步。它做的事只有一件：

    ESP32 / Arduino 的串口行协议  ←→  EPICS PV

    ┌────────────┐   USB 串口   ┌──────────────────┐   CA/pvAccess   ┌──────────┐
    │  ESP32     │  115200bps  │  本脚本           │  ────────────►  │ 你的程序  │
    │  ADC/PWM/IO│ ◄──────────► │  "设备服务器"      │                 │ 智能体    │
    └────────────┘   行协议      └──────────────────┘                 └──────────┘
            硬件世界                        软件世界（EPICS 的边界）

跑起来（没有硬件也能跑！--sim 会用一个假设备顶上）：

    # 1) 有硬件：插上 USB，查串口名（Linux: /dev/ttyUSB0 或 /dev/ttyACM0；Win: COM3）
    python c07_pv_bridge.py --port /dev/ttyUSB0 --prefix LAB:

    # 2) 没硬件：先用仿真模式把整套流程跑通
    python c07_pv_bridge.py --sim --prefix LAB:

然后在另一个终端：

    caget LAB:Temp LAB:Light LAB:Pot LAB:LED LAB:Servo
    caput LAB:LED 1                # 点亮板子上的 LED
    caput LAB:Servo 45             # 舵机转到 45 度
    camonitor LAB:Temp

设计上的四个"专业习惯"（真接设备时一个都不能少）：

  1. **依赖注入**：SimDevice 和 SerialDevice 接口一模一样，所以整个系统
     可以在没有硬件的情况下被完整测试（第 10 章的自动寻峰就是这么测的）。
  2. **两层分离**：`Channel`（物理量转换 + 滤波）与 `BridgeIOC`（PV 语义）分开，
     标定参数改了不用动 PV 定义。
  3. **失败要可见**：连续读失败 → `Connected=0` + 日志 + 停止写输出，
     而不是默默返回陈旧的数值。
  4. **安全默认值**：脚本退出 / 失联时把 LED 关掉、舵机停住。
"""

from __future__ import annotations

import argparse
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

from caproto.server import PVGroup, pvproperty, run

# =============================================================================
# 1. 设备协议层：把"串口行协议"封装成两个方法
# =============================================================================

class DeviceError(RuntimeError):
    """设备和桥之间通信失败。"""


class SimDevice:
    """假设备。接口和 SerialDevice 完全一致 —— 这就是"依赖注入"的好处：
    上层代码不需要知道下面接的是真板子还是仿真。
    它还会故意制造麻烦：量化误差、噪声、偶尔超时。
    """

    class _FakeSerial:
        pass

    def __init__(self, noise: float = 0.02, timeout_rate: float = 0.02):
        self.t0 = time.monotonic()
        self.noise = noise
        self.timeout_rate = timeout_rate
        self.led = 0
        self.servo_angle = 0.0
        self.pot_turns = 0.5      # 电位器"旋钮"位置 0..1
        self.light_level = 0.2    # 环境光 0..1
        self.temperature = 25.0
        self._rng = random.Random(20261012)

    # --- 让"仿真"也能被外部驱动（给自动化测试用）---
    def set_pot(self, fraction: float) -> None:
        self.pot_turns = max(0.0, min(1.0, fraction))

    def set_light(self, level: float) -> None:
        self.light_level = max(0.0, min(1.0, level))

    def command(self, line: str) -> str:
        if self._rng.random() < self.timeout_rate:
            raise DeviceError("仿真：模拟一次串口超时")
        parts = line.strip().split()
        if not parts:
            raise DeviceError("空命令")
        cmd = parts[0].upper()

        if cmd == "PING":
            return "PONG"
        if cmd == "INFO":
            return f"INFO sim-1.0 {int((time.monotonic() - self.t0) * 1000)}"
        if cmd == "AI":
            idx = int(parts[1])
            return f"AI {idx} {self._adc(idx)}"
        if cmd == "DO":
            idx, value = int(parts[1]), int(parts[2])
            if idx == 0:
                self.led = value
                # LED 照亮光敏电阻：这就是"闭环"的最短版本
                self.light_level = 0.25 + 0.6 * value
                return "OK"
            raise DeviceError(f"不存在的 DO 通道 {idx}")
        if cmd == "SERVO":
            self.servo_angle = max(0.0, min(180.0, float(parts[1])))
            return "OK"
        if cmd == "SRV?":
            return f"SRV {self.servo_angle:.1f}"
        raise DeviceError(f"未知命令 {cmd}")

    def _adc(self, idx: int) -> int:
        """模拟 12 bit ADC：0..4095。"""
        if idx == 0:      # 电位器
            true = self.pot_turns
        elif idx == 1:    # 光敏电阻
            true = self.light_level
        elif idx == 2:    # 热敏电阻（用舵机角度稍微影响一下，制造"有趣"的耦合）
            self.temperature += (25.0 + 5.0 * math.sin(self.servo_angle / 30.0)
                                 - self.temperature) * 0.01
            true = (self.temperature - 0.0) / 100.0   # 假设 0..100℃ 映射 0..1
        else:
            raise DeviceError(f"不存在的 AI 通道 {idx}")
        value = true * 4095.0 * (1 + self._rng.gauss(0, self.noise))
        return int(max(0, min(4095, round(value))))

    def close(self) -> None:
        pass


class SerialDevice:
    """真设备：pyserial + 行协议（协议定义见 hardware/esp32_firmware/main.py）。"""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0,
                 boot_wait: float = 2.0):
        import serial  # 延迟导入：没有硬件/没装 pyserial 时也能看代码

        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(boot_wait)      # ESP32 打开串口会复位，等它启动完
        self.ser.reset_input_buffer()

    def command(self, line: str) -> str:
        try:
            self.ser.write((line.strip() + "\n").encode("ascii"))
            self.ser.flush()
            reply = self.ser.readline().decode("utf-8", errors="replace").strip()
        except Exception as exc:  # 串口拔掉/驱动炸了
            raise DeviceError(f"串口错误：{exc}") from exc
        if not reply:
            raise DeviceError(f"设备没有回应：{line!r}")
        if reply.startswith("ERR"):
            raise DeviceError(f"设备报错：{reply}")
        return reply

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


# =============================================================================
# 2. 标定 + 滤波：把 0..4095 的原始值变成有物理意义的数
# =============================================================================

@dataclass
class Channel:
    """一个模拟输入通道。

    标定用**两点线性标定**，这也是实验室里最常用、最不容易出错的方法：
    把设备放到已知状态 A，记下原始值 raw0 和对应的物理值 eng0；
    再放到已知状态 B，记下 raw1 / eng1。中间的就是线性插值。

        eng = eng0 + (raw - raw0) * (eng1 - eng0) / (raw1 - raw0)

    滤波用滑动平均：能压噪声，但会带来延迟（窗口越大越平滑也越迟钝）。
    """

    index: int
    name: str
    units: str
    eng0: float
    raw0: float
    eng1: float
    raw1: float
    window: int = 5
    _buf: Deque[float] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        self._buf = deque(maxlen=max(1, self.window))

    def calibrate(self, raw: float) -> float:
        if self.raw1 == self.raw0:
            raise ValueError(f"通道 {self.name} 标定点重复，无法标定")
        return (self.eng0
                + (float(raw) - self.raw0) * (self.eng1 - self.eng0)
                / (self.raw1 - self.raw0))

    def push(self, raw: float) -> float:
        self._buf.append(self.calibrate(raw))
        return sum(self._buf) / len(self._buf)

    @property
    def filtered(self) -> float:
        return sum(self._buf) / len(self._buf) if self._buf else float("nan")


# =============================================================================
# 3. PV 层：EPICS 那边看到的就是这些
# =============================================================================

class BridgeIOC(PVGroup):
    """把设备通道暴露成 PV。

    命名遵守"一个 PV 一个物理量"：每个 PV 都要有单位、精度、范围。
    """

    temperature = pvproperty(value=25.0, name="Temp", dtype=float, precision=2,
                             read_only=True)
    light = pvproperty(value=0.0, name="Light", dtype=float, precision=3,
                       read_only=True)
    pot = pvproperty(value=0.0, name="Pot", dtype=float, precision=3,
                     read_only=True)
    led = pvproperty(value=0, name="LED", dtype=int)
    servo = pvproperty(value=0.0, name="Servo", dtype=float, precision=1)
    connected = pvproperty(value=1, name="Connected", dtype=int)
    pollcount = pvproperty(value=0, name="PollCount", dtype=int, read_only=True)
    lasterror = pvproperty(value="", name="LastError", dtype=str, read_only=True)

    def __init__(self, prefix: str, device, channels: Dict[str, Channel],
                 period: float = 0.1, max_failures: int = 5):
        super().__init__(prefix=prefix)
        self.device = device
        self.channels = channels
        self.period = period
        self.max_failures = max_failures
        self.failures = 0
        self.running = True
        self._poll_count = 0
        self._servo_angle = 0.0

    # ---------------- 后台轮询：这就是"设备服务器"的心跳 ----------------
    @temperature.startup
    async def _poll(self, instance, async_lib):
        """每 period 秒读一次所有模拟通道。

        注意：这里是**轮询**设备，不是让设备推给我们 —— 因为我们的
        串口协议是"问答式"的。在 EPICS 的 IOC 里，如果用 asyn +
        StreamDevice，就可以做到 `I/O Intr`（设备主动上报就立刻更新）。
        对新手项目，10 Hz 轮询足够，而且好调试。
        """
        while self.running:
            try:
                await self._poll_once()
                self.failures = 0
                if not self.connected.value:
                    await self.connected.write(1)
            except DeviceError as exc:
                self.failures += 1
                await self.lasterror.write(str(exc)[:60])
                if self.failures == self.max_failures:
                    await self.connected.write(0)
                    print(f"⚠️  连续 {self.failures} 次读设备失败，已标记 Connected=0：{exc}")
            except Exception as exc:  # 别让后台任务悄悄死掉
                self.failures += 1
                await self.lasterror.write(f"{type(exc).__name__}: {exc}"[:60])
            await async_lib.library.sleep(self.period)

    async def _poll_once(self) -> None:
        for key, channel in self.channels.items():
            reply = self.device.command(f"AI {channel.index}")
            raw = float(reply.split()[2])
            value = channel.push(raw)
            await getattr(self, key).write(round(value, 6))
        self._poll_count += 1
        await self.pollcount.write(self._poll_count)

    # ---------------- 写 PV = 给设备下命令 ----------------
    @led.putter
    async def led(self, instance, value):
        level = 1 if int(value) else 0
        self.device.command(f"DO 0 {level}")   # 设备确认了才算写成功
        print(f"→ LED {'ON' if level else 'OFF'}", flush=True)
        return level

    @servo.putter
    async def servo(self, instance, value):
        angle = max(0.0, min(180.0, float(value)))
        self.device.command(f"SERVO {angle:.1f}")
        self._servo_angle = angle
        print(f"→ Servo {angle:.1f}°", flush=True)
        return angle

    # ---------------- 安全退出：把执行器恢复到安全状态 ----------------
    @led.shutdown
    async def led(self, instance, async_lib):
        try:
            self.device.command("DO 0 0")
            print("已把 LED 关掉（安全退出）", flush=True)
        except Exception:
            pass


# =============================================================================
# 4. main
# =============================================================================

def default_channels() -> Dict[str, Channel]:
    """默认标定参数。真设备上你必须自己用两点法标一次！

    这里写给仿真设备的理想值：0..4095 对应 0..1 归一化量。
    接真硬件时，用第 7 章的标定练习把 raw0/raw1 换成你实测的数字。
    """
    return {
        "pot": Channel(0, "pot", "V", eng0=0.0, raw0=0, eng1=10.0, raw1=4095, window=5),
        "light": Channel(1, "light", "a.u.", eng0=0.0, raw0=0, eng1=1.0, raw1=4095, window=5),
        "temperature": Channel(2, "temperature", "degC", eng0=0.0, raw0=0,
                               eng1=100.0, raw1=4095, window=7),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="串口/仿真设备 ↔ EPICS PV 桥")
    ap.add_argument("--prefix", default="LAB:")
    ap.add_argument("--port", default="/dev/ttyUSB0", help="串口名（--sim 时忽略）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--sim", action="store_true", help="用仿真设备，不需要硬件")
    ap.add_argument("--period", type=float, default=0.1, help="轮询周期（秒）")
    args = ap.parse_args()

    if args.sim:
        device = SimDevice()
        print("★ 仿真模式：没有真硬件，SimDevice 顶替 SerialDevice")
    else:
        try:
            device = SerialDevice(args.port, args.baud)
        except Exception as exc:
            print(f"❌ 打开串口 {args.port} 失败：{exc}")
            print("   提示：Linux 看 /dev/ttyUSB* 或 /dev/ttyACM*；"
                  "Windows 看设备管理器的 COM 口；")
            print("   权限不足时要 sudo usermod -aG dialout $USER 然后重新登录。")
            print("   先试试 --sim 把流程跑通。")
            return 1
        print(f"✓ 已打开串口 {args.port} @ {args.baud}")
        try:
            print("  设备握手：", device.command("INFO"), device.command("PING"))
        except DeviceError as exc:
            print(f"⚠️  握手失败（{exc}）——检查固件是否烧好、波特率是否一致")

    channels = default_channels()
    ioc = BridgeIOC(prefix=args.prefix, device=device, channels=channels,
                    period=args.period)

    print(f"\n要暴露的 PV：")
    for key, channel in channels.items():
        print(f"  {args.prefix}{key:<12} (AI{channel.index}, "
              f"{channel.units}, 标定 {channel.raw0}->{channel.eng0:g}, "
              f"{channel.raw1}->{channel.eng1:g})")
    print(f"  {args.prefix}LED / {args.prefix}Servo / "
          f"{args.prefix}Connected / {args.prefix}PollCount / {args.prefix}LastError")
    print(f"\n在另一个终端里试：\n"
          f"  caget {args.prefix}Temp {args.prefix}Light {args.prefix}Pot\n"
          f"  caput {args.prefix}LED 1\n"
          f"  camonitor {args.prefix}Temp\n")
    print("Ctrl-C 退出\n")

    try:
        run(ioc.pvdb)
    except KeyboardInterrupt:
        print("\n退出中……")
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
