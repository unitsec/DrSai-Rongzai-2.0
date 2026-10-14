# main.py —— ESP32 / ESP32-C3 MicroPython 固件（第 7 章）
#
# 这是"便宜硬件实验台"的固件端。它只干一件事：
#    把 USB 串口上的一行一条文本命令，翻译成对 ADC / GPIO / PWM 的操作。
#
# 为什么要用"行协议"（每行一条命令，回车结束）？
#   * 人可以在串口终端里手打，调试最快；
#   * 任何语言都能解析（Python/C/LabVIEW/EPICS StreamDevice）；
#   * 出问题时你能亲眼看到发了什么、回了什么。
#   工业设备里的 SCPI、AT 指令都是这个思路。
#
# ---------------- 协议 ----------------
#   请求                      应答
#   PING                  ->  PONG
#   INFO                  ->  INFO <版本> <运行毫秒>
#   AI <n>                ->  AI <n> <0..4095>      读第 n 号模拟输入
#   DO <n> <0|1>          ->  OK                    写第 n 号数字输出
#   SERVO <角度 0..180>   ->  OK                    舵机角度
#   SRV?                  ->  SRV <角度>             查询舵机角度
#   未知命令              ->  ERR unknown <原命令>
#
# ---------------- 接线（默认引脚，可按需改）----------------
#   AI0 = GPIO34 电位器中间脚      AI1 = GPIO35 光敏电阻分压
#   AI2 = GPIO32 热敏/NTC 分压      DO0 = GPIO2  LED（串 220Ω 到 GND）
#   SERVO = GPIO13 舵机信号
#
# ---------------- 安全设计（这才是重点）----------------
#   1. 上电默认所有输出为"关"、舵机在 0 度（不要靠"上次的值"）；
#   2. **看门狗**：超过 WATCHDOG_MS 没收到命令，就把输出全部复位到安全状态。
#      没有这一条，你的 PC 崩溃时加热/快门会一直保持最后的状态——真实事故都这么来的；
#   3. 未知命令必须回 ERR，不能沉默（沉默会让上位机一直等）；
#   4. 所有数值都做范围检查，越界回 ERR。

import sys
import time

try:
    import machine
    import select
except ImportError:  # 在 PC 上直接跑这个文件时给个提示
    print("这个文件要在 MicroPython（ESP32）里运行")
    raise

# ============================ 配置 ============================
WATCHDOG_MS = 5000          # 超过这么久没收到命令 -> 回到安全状态
LED_PIN = 2
SERVO_PIN = 13
AI_PINS = {0: 34, 1: 35, 2: 32}

# ============================ 硬件对象 ============================
led = machine.Pin(LED_PIN, machine.Pin.OUT, value=0)     # 上电即关
servo = machine.PWM(machine.Pin(SERVO_PIN), freq=50)     # 50 Hz 舵机
adcs = {}
for index, pin in AI_PINS.items():
    try:
        adc = machine.ADC(machine.Pin(pin))
        try:
            adc.atten(machine.ADC.ATTN_11DB)    # 量程 0..3.3V
            adc.width(machine.ADC.WIDTH_12BIT)  # 12 bit -> 0..4095
        except Exception:
            pass
        adcs[index] = adc
    except Exception as exc:                    # 引脚不存在就跳过
        print(f"# AI{index} (GPIO{pin}) 初始化失败: {exc}")


def set_servo_angle(angle):
    """舵机：0.5 ms ~ 2.5 ms 脉宽对应 0~180 度。"""
    angle = max(0.0, min(180.0, float(angle)))
    duty_ns = int((500_000 + (angle / 180.0) * 2_000_000))  # 纳秒
    try:
        servo.duty_ns(duty_ns)
    except Exception:
        servo.duty(int(duty_ns / 20_000))       # 老固件用 duty()
    return angle


def safe_state():
    """安全状态：LED 关、舵机回 0 度。"""
    led.value(0)
    set_servo_angle(0)


# ============================ 主循环 ============================
def main():
    safe_state()
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    started = time.ticks_ms()
    last_command = time.ticks_ms()
    servo_angle = 0.0
    buffer = ""

    print("READY esp32-lineproto/1.0")     # 上位机看到 READY 就知道固件活了

    while True:
        if poller.poll(20):                 # 20 ms 超时，兼顾响应和看门狗
            char = sys.stdin.read(1)
            if not char:
                continue
            if char == "\n":
                line = buffer.strip()
                buffer = ""
                if not line:
                    continue
                last_command = time.ticks_ms()
                reply = handle(line, started, servo_angle)
                if reply is not None:
                    if reply.startswith("SRV "):
                        servo_angle = float(reply.split()[1])
                    sys.stdout.write(reply + "\n")

        # ---- 看门狗：上位机失联就回到安全状态 ----
        if time.ticks_diff(time.ticks_ms(), last_command) > WATCHDOG_MS:
            safe_state()
            servo_angle = 0.0
            last_command = time.ticks_ms()   # 避免反复打印
            print("ERR watchdog reset")


def handle(line, started, servo_angle):
    parts = line.split()
    cmd = parts[0].upper()

    if cmd == "PING":
        return "PONG"
    if cmd == "INFO":
        return f"INFO esp32-lineproto/1.0 {time.ticks_diff(time.ticks_ms(), started)}"
    if cmd == "AI":
        if len(parts) != 2:
            return "ERR usage AI <n>"
        try:
            index = int(parts[1])
        except ValueError:
            return "ERR bad index"
        adc = adcs.get(index)
        if adc is None:
            return f"ERR no such AI {index}"
        try:
            raw = adc.read_u16()             # MicroPython 给 0..65535
            if raw > 4095:                   # 换算到 12 bit 方便标定
                raw = raw >> 4
        except Exception as exc:
            return f"ERR adc {exc}"
        return f"AI {index} {int(raw)}"
    if cmd == "DO":
        if len(parts) != 3:
            return "ERR usage DO <n> <0|1>"
        try:
            index, value = int(parts[1]), int(parts[2])
        except ValueError:
            return "ERR bad args"
        if index != 0:
            return f"ERR no such DO {index}"
        if value not in (0, 1):
            return "ERR value must be 0 or 1"
        led.value(value)
        return "OK"
    if cmd == "SERVO":
        if len(parts) != 2:
            return "ERR usage SERVO <angle>"
        try:
            angle = float(parts[1])
        except ValueError:
            return "ERR bad angle"
        if not (0 <= angle <= 180):
            return "ERR angle out of range 0..180"
        set_servo_angle(angle)
        return f"SRV {angle:.1f}"
    if cmd == "SRV?":
        return f"SRV {servo_angle:.1f}"
    return f"ERR unknown {line}"


if __name__ == "__main__":
    main()
