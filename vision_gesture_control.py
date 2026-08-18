# -*- coding: utf-8 -*-
"""电脑摄像头手势识别 -> RH56DFTP 灵巧手动作。

默认仅识别，不连接/驱动灵巧手。实物联调：
    python vision_gesture_control.py --control-hand --port COM7

本地测试，在项目文件夹下运行：
    python vision_gesture_control.py --control-hand --speed 250 --force 200

窗口按键：A 启用/停用动作，R 解除当前手势锁存，Q/ESC 退出。
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Optional, Sequence, Tuple

from gesture_config import GESTURES, SAFE_FORCE, SAFE_SPEED, execute_gesture
from hand_driver import DEFAULT_RS485_PORT, InspireHandRS485

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)
DEFAULT_MODEL = Path(__file__).with_name("models") / "gesture_recognizer.task"

CANNED_TO_HAND = {
    "Open_Palm": "open",
    "Closed_Fist": "grab",
    "Pointing_Up": "point",
    "Thumb_Up": "like",
    "Victory": "victory",
}

# MediaPipe 的 21 点骨架连接。
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)


def _distance(a, b) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def next_video_timestamp_ms(started: float, previous: int) -> int:
    """生成 MediaPipe VIDEO 模式所需的严格递增毫秒时间戳。"""
    measured = int((time.monotonic() - started) * 1000)
    return max(measured, previous + 1)


def is_pinch(landmarks: Sequence, threshold: float = 0.33) -> bool:
    """拇指尖与食指尖距离相对掌长足够小时判为捏取。"""
    if len(landmarks) != 21:
        return False
    palm_size = _distance(landmarks[0], landmarks[9])
    return palm_size > 1e-6 and _distance(landmarks[4], landmarks[8]) / palm_size < threshold


def classify_result(result, min_score: float, pinch_threshold: float) -> Tuple[Optional[str], float]:
    """把 MediaPipe 结果映射到项目中的手势名。"""
    if not result.hand_landmarks:
        return None, 0.0

    landmarks = result.hand_landmarks[0]
    category = None
    if result.gestures and result.gestures[0]:
        category = result.gestures[0][0]
        # 握拳时拇指尖和食指尖也可能靠近，优先将成熟模型结果映射到抓取动作。
        if category.category_name == "Closed_Fist" and float(category.score or 0.0) >= min_score:
            return "grab", float(category.score)
    if is_pinch(landmarks, pinch_threshold):
        return "pinch", 1.0

    if category is None:
        return None, 0.0
    name = CANNED_TO_HAND.get(category.category_name)
    score = float(category.score or 0.0)
    if name is None or score < min_score:
        return None, score
    return name, score


class GestureStabilizer:
    """要求连续 N 帧相同，过滤瞬时误识别；空手后允许同一动作再次触发。"""

    def __init__(self, stable_frames: int):
        if stable_frames < 1:
            raise ValueError("stable_frames 必须 >= 1")
        self.history = deque(maxlen=stable_frames)
        self.latched: Optional[str] = None

    def update(self, gesture: Optional[str]) -> Optional[str]:
        self.history.append(gesture)
        if len(self.history) < self.history.maxlen:
            return None
        if all(item is None for item in self.history):
            self.latched = None
            return None
        if gesture is not None and all(item == gesture for item in self.history):
            if gesture != self.latched:
                self.latched = gesture
                return gesture
        return None

    def reset(self) -> None:
        self.history.clear()
        self.latched = None


def ensure_model(path: Path, allow_download: bool) -> Path:
    if path.is_file():
        return path
    if not allow_download:
        raise FileNotFoundError("模型不存在: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    print("首次运行，正在下载 MediaPipe 手势模型...")
    try:
        urllib.request.urlretrieve(MODEL_URL, str(path))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    print("模型已保存:", path)
    return path


def draw_hand(cv2, frame, landmarks) -> None:
    height, width = frame.shape[:2]
    points = [(int(p.x * width), int(p.y * height)) for p in landmarks]
    for start, end in HAND_CONNECTIONS:
        cv2.line(frame, points[start], points[end], (60, 220, 60), 2)
    for point in points:
        cv2.circle(frame, point, 3, (0, 220, 255), -1)


def parse_args():
    ap = argparse.ArgumentParser(description="摄像头识别手势并控制 RH56DFTP 灵巧手")
    ap.add_argument("--camera", type=int, default=0, help="摄像头编号，默认 0")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--no-download", action="store_true", help="模型不存在时不自动下载")
    ap.add_argument("--score", type=float, default=0.60, help="分类最低置信度")
    ap.add_argument("--pinch-threshold", type=float, default=0.33, help="捏取距离阈值")
    ap.add_argument("--stable-frames", type=int, default=8, help="连续确认帧数")
    ap.add_argument("--cooldown", type=float, default=1.0, help="两次动作最短间隔/秒")
    ap.add_argument("--control-hand", action="store_true", help="连接灵巧手（仍需按 A 启用动作）")
    ap.add_argument("--port", default=DEFAULT_RS485_PORT)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--id", type=int, default=1)
    ap.add_argument("--speed", type=int, default=SAFE_SPEED)
    ap.add_argument("--force", type=int, default=SAFE_FORCE)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 <= args.score <= 1.0 or args.stable_frames < 1 or args.cooldown < 0:
        print("参数错误：score 需为 0-1，stable-frames >= 1，cooldown >= 0")
        return 2
    if not 0 <= args.speed <= 1000 or not 0 <= args.force <= 3000:
        print("参数错误：speed 需为 0-1000，force 需为 0-3000")
        return 2
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        print("缺少视觉依赖，请先运行: python -m pip install -r requirements.txt")
        print("详情:", exc)
        return 2

    model_path = ensure_model(args.model.resolve(), not args.no_download)
    hand = None
    armed = False
    if args.control_hand:
        try:
            hand = InspireHandRS485(args.port, args.baud, hand_id=args.id).open()
            print("灵巧手已连接；为安全起见当前未启用动作，请在窗口按 A。")
        except Exception as exc:
            print("无法连接灵巧手: %s" % exc)
            return 3

    capture = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
    if not capture.isOpened() and sys.platform == "win32":
        capture.release()
        capture = cv2.VideoCapture(args.camera, cv2.CAP_ANY)
    if not capture.isOpened():
        if hand:
            hand.close()
        print("无法打开摄像头 %d，可尝试 --camera 1" % args.camera)
        return 4

    options = mp.tasks.vision.GestureRecognizerOptions(
        # Windows 原生层不能可靠处理中文模型路径，传 bytes 可彻底规避路径编码问题。
        base_options=mp.tasks.BaseOptions(model_asset_buffer=model_path.read_bytes()),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    stabilizer = GestureStabilizer(args.stable_frames)
    last_action_time = 0.0
    started = time.monotonic()
    last_timestamp_ms = -1

    try:
        with mp.tasks.vision.GestureRecognizer.create_from_options(options) as recognizer:
            while True:
                ok, frame = capture.read()
                if not ok:
                    print("读取摄像头画面失败")
                    break
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = next_video_timestamp_ms(started, last_timestamp_ms)
                last_timestamp_ms = timestamp_ms
                result = recognizer.recognize_for_video(mp_image, timestamp_ms)
                gesture, score = classify_result(result, args.score, args.pinch_threshold)
                event = stabilizer.update(gesture)

                if result.hand_landmarks:
                    draw_hand(cv2, frame, result.hand_landmarks[0])
                if event and armed and hand:
                    if time.monotonic() - last_action_time < args.cooldown:
                        # 冷却结束后，只要仍保持该手势就再次产生事件，不丢动作。
                        stabilizer.latched = None
                    else:
                        config = GESTURES[event]
                        try:
                            execute_gesture(hand, event, speed=args.speed, force=args.force)
                            last_action_time = time.monotonic()
                            print("执行手势 %-7s %s" % (event, config["angles"]))
                        except (TimeoutError, OSError) as exc:
                            armed = False
                            print("控制失败，已自动停用动作:", exc)

                status = "ARMED" if armed else ("READY-PRESS A" if hand else "VISION ONLY")
                label = gesture or "unknown"
                cv2.putText(frame, "Gesture: %s  score: %.2f" % (label, score),
                            (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                cv2.putText(frame, "Mode: %s | A arm | R reset | Q quit" % status,
                            (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 80, 255) if armed else (220, 220, 220), 2)
                cv2.imshow("Dexterous Hand Gesture Control", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key in (ord("a"), ord("A")) and hand:
                    armed = not armed
                    stabilizer.reset()
                    print("动作控制:", "已启用" if armed else "已停用")
                if key in (ord("r"), ord("R")):
                    stabilizer.reset()
                    print("手势锁存已重置")
    finally:
        capture.release()
        cv2.destroyAllWindows()
        if hand:
            hand.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
