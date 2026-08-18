# -*- coding: utf-8 -*-
"""
hand_console.py — 灵巧手交互式测试控制台（RH56DFTP, RS485 EB90）v2
用法: python hand_console.py [--port COM7] [--baud 115200] [--id 1]

安全设计:
  - 角度/速度两步设置: sp 只设速度(手不动), a/g 写角度才运动, 运动用最近 sp 值
  - 若未设过速度, a/g 自动使用安全默认速度 250 (可 --speed 改)
  - 每次 a/g 运动都附力阈值 200, 防止夹坏 (可 f 命令改)
  - x 停止: 把速度放慢并写回当前角度, 手指冻结在当前姿态
  - 真正紧急情况: 直接断电源最可靠

交互命令:
  s / status        显示 角度/力/温度/状态/错误
  a <6个角度>       设置角度 0-1000(-1=跳过), 自动用安全速度, 如: a 500 500 500 500 500 500
  aN <值>           单指调试, N=0..5, 如: a3 500  (只动食指, 其他不动)
  sp <6个速度>      设定后续运动速度 0-1000 (只设参数, 手不动)
  f  <6个力阈值>    设定力阈值 0-3000 (只设参数, 手不动)
  g <手势>          常用手势(见下), 建议先单指调试再整手
  x / stop          停止: 冻结在当前姿态
  id                读手 ID
  clr               清除错误
  save              保存参数到 Flash
  h / help          帮助
  q / exit          退出

手势角度参考 (小指 无名指 中指 食指 拇指弯 拇指转; 1000=张开 0=弯曲 -1=不动):
  open   [1000 1000 1000 1000 1000 1000]  五指张开
  grab   [ 500  500  500  520  520   0  ]  抓取/握水瓶
  pinch  [  0    0  500  520  520   0  ]  捏取(实测)
  point  [  0    0    0  1000   0   180 ]  食指指(实测)
  like   [  0    0    0    0  1000 1000 ]  点赞(实测)
  victory[  0    0 1000 1000  250 1000 ]  比耶(实测)
  middle [  0    0 1000    0  300 1000 ]  中指(实测)
  注: 手势均为用户实测值; 拇指角度传感器反馈不准(显示值异常), 但实际行程 0-1000 正常, 不影响设置
"""
import argparse, sys, time
from hand_driver import DEFAULT_RS485_PORT, InspireHandRS485
from gesture_config import GESTURES, SAFE_FORCE, SAFE_SPEED, STOP_SPEED, execute_gesture

def fmt(vals):
    return "[" + " ".join(str(v) for v in vals) + "]"

def main():
    ap = argparse.ArgumentParser(description="灵巧手交互式测试控制台 v2")
    ap.add_argument("--port", default=DEFAULT_RS485_PORT)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--id", type=int, default=1)
    ap.add_argument("--speed", type=int, default=SAFE_SPEED,
                    help="未单独设速度时的安全默认速度 (默认 %d)" % SAFE_SPEED)
    args = ap.parse_args()

    try:
        h = InspireHandRS485(args.port, args.baud, hand_id=args.id)
        h.open()
    except Exception as e:
        print("无法打开 %s: %s" % (args.port, e))
        print("请确认 USB-RS485 适配器已插入、未被占用，或换 --port")
        sys.exit(1)

    last_speed = None  # 最近一次 sp 设置的速度
    print("== 灵巧手控制台 v2 | %s@%d ID=%d | 安全默认速度 %d ==" % (args.port, args.baud, args.id, args.speed))
    print("输入 h 查看帮助；x 停止；q 退出")
    print("提示: 先 sp 设慢速(如 100)，再 a 或 aN 单指调试，确认安全后再整手动作")

    def move(angles, tag=""):
        """带安全速度/力阈值的运动：用最近 sp 值或安全默认。"""
        spd = last_speed if last_speed is not None else args.speed
        h.set_angle(angles, speed=[spd] * 6, force=[SAFE_FORCE] * 6)
        print("运动 %s: 角度 %s @ 速度 %d 力 %d" % (tag, fmt(angles), spd, SAFE_FORCE))

    while True:
        try:
            line = input(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()

        if cmd in ("q", "exit", "quit"):
            break
        elif cmd in ("h", "help"):
            print(__doc__.split("交互命令:")[1].split("import argparse")[0].strip())
        elif cmd in ("s", "status"):
            try:
                print("角度   :", fmt(h.read_angles()))
                print("力     :", fmt(h.read_forces()))
                print("温度   :", fmt(h.read_temps()))
                print("状态   :", h.read_status().hex(" "))
                print("错误   :", h.read_errors().hex(" "))
            except TimeoutError as e:
                print("读超时:", e)
        elif cmd == "a":
            if len(parts) < 7:
                print("需要 6 个角度值 (-1=跳过)，如: a 500 500 500 500 500 500")
                continue
            vals = [int(x) for x in parts[1:7]]
            move(vals, "整手")
        elif len(cmd) == 2 and cmd[0] == "a" and cmd[1].isdigit():
            # 单指调试: aN <值>  (N=0..5)
            dof = int(cmd[1])
            if dof < 0 or dof > 5 or len(parts) < 2:
                print("单指格式: aN <值>，N=0小指 1无名 2中指 3食指 4拇指弯 5拇指转，如 a3 500")
                continue
            val = int(parts[1])
            if val < -1 or val > 1000:
                print("角度范围 0-1000 (-1=不动)")
                continue
            angles = [-1] * 6
            angles[dof] = val
            move(angles, "单指%d=%d" % (dof, val))
        elif cmd == "sp":
            if len(parts) < 7:
                print("需要 6 个速度值，如: sp 100 100 100 100 100 100")
                continue
            vals = [int(x) for x in parts[1:7]]
            if any(v < 0 or v > 1000 for v in vals):
                print("速度范围 0-1000")
                continue
            last_speed = vals[0]
            h._write_shorts(1522, vals)  # SPEED_SET
            print("已设定速度 %s (仅参数，手不动；下次 a/g 用此速度)" % fmt(vals))
        elif cmd == "f":
            if len(parts) < 7:
                print("需要 6 个力阈值，如: f 200 200 200 200 200 200")
                continue
            vals = [int(x) for x in parts[1:7]]
            if any(v < 0 or v > 3000 for v in vals):
                print("力阈值范围 0-3000")
                continue
            h._write_shorts(1498, vals)  # FORCE_SET
            print("已设定力阈值 %s (仅参数，手不动)" % fmt(vals))
        elif cmd == "g":
            name = parts[1] if len(parts) > 1 else ""
            if name not in GESTURES:
                print("可用手势:", " ".join(GESTURES.keys()))
                continue
            g = GESTURES[name]
            spd = last_speed if last_speed is not None else args.speed

            def report_step(step, angles, gesture):
                label = "预动作: 拇指根部先转到0" if step == "pre" else "目标动作"
                print("%s %s: 角度 %s @ 速度 %d 力 %d" %
                      (label, name, fmt(angles), spd, SAFE_FORCE))

            execute_gesture(h, name, speed=spd, force=SAFE_FORCE, on_step=report_step)
        elif cmd in ("x", "stop"):
            try:
                cur = h.read_angles()
                h.set_angle(cur, speed=[STOP_SPEED] * 6, force=[SAFE_FORCE] * 6)
                print("已停止: 写回当前角度 %s @ 慢速 %d，手指冻结在当前姿态" % (fmt(cur), STOP_SPEED))
                print("(若仍未停，直接断开电源最可靠)")
            except TimeoutError as e:
                print("读超时:", e)
        elif cmd == "id":
            try:
                print("HAND_ID =", h.get_hand_id())
            except TimeoutError as e:
                print("读超时:", e)
        elif cmd == "clr":
            h.clear_error()
            print("已发送清除错误")
        elif cmd == "save":
            h.save_to_flash()
            print("已发送保存到 Flash")
        else:
            print("未知命令，输入 h 查看帮助")

    h.close()
    print("已断开")

if __name__ == "__main__":
    main()
