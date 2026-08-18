# -*- coding: utf-8 -*-
"""
hand_driver.py — 因时机器人（Inspire-Robots）RH56 / RH56DFTP 灵巧手 Python 驱动
================================================================================
支持两种通信接口：
  1) RS485    —— EB 90 寄存器读写协议（默认 115200, 8N1, Hand_ID=1）
  2) Modbus TCP —— 默认 192.168.11.210:6000，Unit ID = 0xFF

协议要点（手册 2.2 / 2.5）：
  - 读寄存器帧   : EB 90 [ID] 04 11 [AddrL] [AddrH] [Len] [CS]
  - 读应答帧     : 90 EB [ID] [Len+3] 11 [AddrL] [AddrH] [Data...] [CS]
  - 写寄存器帧   : EB 90 [ID] [DataLen+3] 12 [AddrL] [AddrH] [Data...] [CS]
  - 写应答帧     : 90 EB [ID] 04 12 [AddrL] [AddrH] 01 [CS]
  - 校验和       : 除帧头 EB 90 与校验和自身外，其余字节累加取低 8 位
  - 数据         : short 小端（低字节在前）

标准动作序列（2.2 节）：写 SPEED_SET → 写 FORCE_SET → 写 ANGLE_SET（最后一步一写即动）。
力控抓取：弯曲途中 FORCE_ACT 达到 FORCE_SET 即停。

依赖：pip install pyserial
"""
from __future__ import annotations

import socket
import struct
import time
import os
from typing import List, Optional, Sequence

# ============================ 寄存器地址（十进制=0x 值） ============================
HAND_ID            = 0x03E8  # 1000  手 ID（1-254）
REDU_RATIO         = 0x03EA  # 1002  波特率档位（RS485: 0=115200/1=57600/2=19200/3=921600）
CLEAR_ERROR        = 0x03EC  # 1004  写 1 清错误
SAVE               = 0x03ED  # 1005  写 1 保存到 Flash
RESET_PARA         = 0x03EE  # 1006  写 1 恢复出厂
GESTURE_FORCE_CLB  = 0x03F1  # 1009  力传感器校准（须手掌张开）
DEFAULT_SPEED_SET  = 0x0408  # 1032  上电默认速度（6 short）
DEFAULT_FORCE_SET  = 0x0414  # 1044  上电默认力阈值（6 short）
POS_SET            = 0x05C2  # 1474  执行器位置 0-2000，-1=跳过
ANGLE_SET          = 0x05CE  # 1486  角度 0-1000，-1=跳过，写入即动作
FORCE_SET          = 0x05DA  # 1498  力阈值 0-3000
SPEED_SET          = 0x05F2  # 1522  速度 0-1000
POS_ACT            = 0x05FE  # 1534  实际位置（只读）
ANGLE_ACT          = 0x060A  # 1546  实际角度（只读）
FORCE_ACT          = 0x062E  # 1582  实际力 -4000~4000 g（只读）
CURRENT            = 0x063A  # 1594  电流 0-2000 mA（只读）
ERROR              = 0x0646  # 1606  错误码 6B（只读）
STATUS             = 0x064C  # 1612  状态 6B（只读）
TEMP               = 0x0652  # 1618  温度 6B（只读）
IP_PART1           = 0x06A4  # 1700  以太网 IP 段1（默认 192），改后需重新上电

# ============================ DOF 通道（RH56DFTP 英文手册） ============================
DOF_LITTLE, DOF_RING, DOF_MIDDLE, DOF_INDEX, DOF_THUMB_BEND, DOF_THUMB_ROTATE = range(6)
DOF_NAMES = ["little", "ring", "middle", "index", "thumb_bend", "thumb_rotate"]


def default_rs485_port() -> str:
    """返回平台默认串口；RH56_PORT 环境变量具有最高优先级。"""
    return os.environ.get("RH56_PORT", "COM7" if os.name == "nt" else "/dev/ttyUSB0")


DEFAULT_RS485_PORT = default_rs485_port()


# ============================ RS485 (EB 90 协议) ============================
class InspireHandRS485:
    """RS485 EB 90 协议驱动。"""

    def __init__(self, port: str, baudrate: int = 115200, hand_id: int = 1,
                 timeout: float = 0.5, retries: int = 3):
        self.port = port
        self.baudrate = baudrate
        self.hand_id = hand_id
        self.timeout = timeout
        self.retries = retries
        self.ser = None

    # ---------- 底层收发 ----------
    def open(self):
        import serial
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        return self

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _checksum(body: bytes) -> int:
        return sum(body) & 0xFF

    @staticmethod
    def _find_response(buf: bytes) -> Optional[bytes]:
        """在缓冲区中定位应答帧（帧头 90 EB）并做校验和验证。"""
        idx = buf.find(b"\x90\xEB")
        if idx < 0:
            return None
        frame = buf[idx:]
        if len(frame) < 9:          # 最小应答：90 EB ID LEN 11 AL AH 01 CS
            return None
        # 从第 4 字节（byte[3]）得到帧数据长度，据此计算总帧长
        data_len = frame[3]         # byte[3] = Register_Length+3（读应答）或 0x04（写应答）
        total = 2 + 1 + 1 + data_len + 1   # 头2 + ID1 + LEN1 + data_len + CS1
        if len(frame) < total:
            return None
        frame = frame[:total]
        if sum(frame[2:-1]) & 0xFF != frame[-1]:
            return None             # 校验和不符 → 视为噪声
        return frame

    def _transact(self, frame: bytes, expect_len: int) -> bytes:
        """发送帧并等待应答（带重试），返回去掉头尾的应答帧。"""
        last_err = None
        for _ in range(self.retries):
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            time.sleep(0.05)
            buf = self.ser.read(expect_len + 16)   # 多读一点，内部再找帧
            resp = self._find_response(buf)
            if resp is not None:
                return resp
            last_err = f"no valid response, got {buf.hex(' ')}"
            time.sleep(0.2)
        raise TimeoutError(f"{self.port}@{self.baudrate} tx {frame.hex(' ')} -> {last_err}")

    # ---------- 寄存器读写 ----------
    def read_reg(self, addr: int, length: int) -> bytes:
        """读寄存器，返回原始数据字节（不含帧头/地址/校验和）。"""
        frame = bytes([0xEB, 0x90, self.hand_id, 0x04, 0x11,
                       addr & 0xFF, (addr >> 8) & 0xFF, length])
        frame += bytes([self._checksum(frame[2:])])
        resp = self._transact(frame, 8 + length)
        # 应答: 90 EB ID [len+3] 11 [AL] [AH] [data...] [cs]
        return resp[7:-1]

    def write_reg(self, addr: int, data: bytes) -> None:
        """写寄存器。data 为原始字节序列。"""
        frame = bytes([0xEB, 0x90, self.hand_id, len(data) + 3, 0x12,
                       addr & 0xFF, (addr >> 8) & 0xFF]) + data
        frame += bytes([self._checksum(frame[2:])])
        self._transact(frame, 9)

    # ---------- 便捷封装 ----------
    def _read_shorts(self, addr: int, n: int) -> List[int]:
        raw = self.read_reg(addr, n * 2)
        return [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw), 2)]

    def _write_shorts(self, addr: int, values: Sequence[int]) -> None:
        data = b"".join(int(v).to_bytes(2, "little", signed=True) for v in values)
        self.write_reg(addr, data)

    def read_angles(self) -> List[int]:      return self._read_shorts(ANGLE_ACT, 6)
    def read_positions(self) -> List[int]:   return self._read_shorts(POS_ACT, 6)
    def read_forces(self) -> List[int]:      return self._read_shorts(FORCE_ACT, 6)
    def read_currents(self) -> List[int]:    return self._read_shorts(CURRENT, 6)
    def read_temps(self) -> List[int]:
        """TEMP 寄存器为 6 字节（每 DOF 1 字节），返回 6 个原始字节。"""
        return list(self.read_reg(TEMP, 6))

    def read_status(self) -> bytes:          return self.read_reg(STATUS, 6)
    def read_errors(self) -> bytes:          return self.read_reg(ERROR, 6)
    def get_hand_id(self) -> int:            return self.read_reg(HAND_ID, 1)[0]

    def clear_error(self) -> None:           self.write_reg(CLEAR_ERROR, b"\x01")
    def save_to_flash(self) -> None:         self.write_reg(SAVE, b"\x01")

    def set_angle(self, angles: Sequence[int], speed: Optional[Sequence[int]] = None,
                  force: Optional[Sequence[int]] = None, wait: bool = False) -> None:
        """标准动作序列：SPEED_SET → FORCE_SET → ANGLE_SET（最后一步写入即动作）。
        angles: 6 个 0-1000 或 -1(跳过)。"""
        if len(angles) != 6:
            raise ValueError("angles 需要 6 个值")
        if speed is not None:
            self._write_shorts(SPEED_SET, speed)
        if force is not None:
            self._write_shorts(FORCE_SET, force)
        self._write_shorts(ANGLE_SET, angles)
        if wait:
            target = [a for a in angles if a >= 0]
            if target:
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    if self.read_angles() == list(angles):
                        break
                    time.sleep(0.05)


# ============================ Modbus TCP ============================
class InspireHandModbusTCP:
    """RH56DFTP 以太网 Modbus TCP 驱动。
    默认 IP 192.168.11.210，端口 6000，Unit ID = 0xFF。
    功能码：03 读、06 写单个、16(0x10) 写多个。寄存器地址与手册十进制地址一致
    （如 HAND_ID=1000）。若与实物地址有 ±1 偏差，可在 addr 上传入偏移后的值。"""

    def __init__(self, host: str = "192.168.11.210", port: int = 6000,
                 unit: int = 0xFF, timeout: float = 1.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.sock = None
        self._tid = 0

    def open(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        return self

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _transact(self, pdu: bytes) -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        mbap = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit)
        self.sock.sendall(mbap + pdu)
        # 应答 MBAP: tid(2) proto(2) len(2) unit(1) + pdu
        head = self._recv_exact(7)
        tid, proto, length, unit = struct.unpack(">HHHB", head)
        if tid != self._tid or proto != 0:
            raise ConnectionError(f"bad MBAP: tid={tid} proto={proto}")
        body = self._recv_exact(length - 1)
        if body[0] & 0x80:                      # Modbus 异常码
            raise ConnectionError(f"Modbus exception 0x{body[1]:02X}")
        return body

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed by hand")
            buf += chunk
        return buf

    def read_registers(self, addr: int, count: int) -> List[int]:
        pdu = struct.pack(">BHH", 0x03, addr, count)
        resp = self._transact(pdu)
        # resp: FC(1) byte_count(1) data(2*count)
        if len(resp) != 2 + 2 * count:
            raise ConnectionError(f"unexpected resp len {len(resp)}")
        return [struct.unpack(">H", resp[2 + 2 * i:4 + 2 * i])[0] for i in range(count)]

    def write_single_register(self, addr: int, value: int) -> None:
        self._transact(struct.pack(">BHH", 0x06, addr, value & 0xFFFF))

    def write_registers(self, addr: int, values: Sequence[int]) -> None:
        vals = [v & 0xFFFF for v in values]
        pdu = struct.pack(">BHHB", 0x10, addr, len(vals), len(vals) * 2) +               b"".join(struct.pack(">H", v) for v in vals)
        self._transact(pdu)

    # 便捷方法（Modbus 寄存器=手册十进制地址，读取数据仍按字节小端解析）
    def read_raw(self, addr: int, nbytes: int) -> bytes:
        regs = self.read_registers(addr, (nbytes + 1) // 2)
        raw = b"".join(struct.pack(">H", r) for r in regs)
        return raw[:nbytes]

    def read_angles(self) -> List[int]:
        raw = self.read_raw(1546, 12)  # ANGLE_ACT
        return [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, 12, 2)]

    def set_angle(self, angles: Sequence[int], speed: Optional[Sequence[int]] = None,
                  force: Optional[Sequence[int]] = None) -> None:
        if len(angles) != 6:
            raise ValueError("angles 需要 6 个值")
        if speed is not None:
            self.write_registers(1522, speed)
        if force is not None:
            self.write_registers(1498, force)
        self.write_registers(1486, angles)


# ============================ CLI 演示 ============================
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Inspire RH56/RH56DFTP 灵巧手驱动")
    ap.add_argument("--mode", choices=["rs485", "tcp"], default="rs485")
    ap.add_argument("--port", default=DEFAULT_RS485_PORT,
                    help="RS485 串口（默认 %s）" % DEFAULT_RS485_PORT)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="192.168.11.210", help="Modbus TCP IP")
    ap.add_argument("--probe", action="store_true", help="只探测连通性")
    ap.add_argument("--angles", action="store_true", help="读取实际角度")
    ap.add_argument("--id", action="store_true", help="读取手 ID")
    ap.add_argument("--status", action="store_true", help="读取状态")
    ap.add_argument("--set-angle", type=int, nargs="+", metavar="A",
                    help="设置 6 个角度（-1=跳过），如 --set-angle 500 500 500 500 500 500")
    args = ap.parse_args()

    if args.mode == "rs485":
        dev = InspireHandRS485(args.port, args.baud)
    else:
        dev = InspireHandModbusTCP(args.host)

    with dev:
        if args.probe:
            if args.mode == "rs485":
                print(f"COM={args.port} baud={args.baud} hand_id={dev.hand_id}")
                print("HAND_ID  =", dev.read_reg(HAND_ID, 1).hex(" "))
                print("STATUS   =", dev.read_reg(STATUS, 6).hex(" "))
                print("ANGLE_ACT=", dev.read_angles())
                print("TEMP     =", dev.read_temps())
            else:
                print(f"TCP {args.host}:6000 unit=0xFF")
                print("HAND_ID  =", dev.read_raw(1000, 1).hex(" "))
                print("ANGLE_ACT=", dev.read_angles())
        if args.id:
            print("HAND_ID =", dev.get_hand_id() if args.mode == "rs485" else dev.read_raw(1000, 1).hex(" "))
        if args.status:
            print("STATUS  =", dev.read_status() if args.mode == "rs485" else dev.read_raw(1612, 6).hex(" "))
        if args.angles:
            print("ANGLES  =", dev.read_angles())
        if args.set_angle:
            vals = list(args.set_angle)
            if len(vals) == 1:
                vals = vals * 6
            if len(vals) != 6:
                raise SystemExit("需要 6 个角度值")
            dev.set_angle(vals)
            print("SET ANGLES =", vals)
            time.sleep(1.0)
            print("ANGLES  =", dev.read_angles())
