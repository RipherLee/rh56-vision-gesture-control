# -*- coding: utf-8 -*-
"""灵巧手手势配置（控制台和视觉识别共用）。"""

import time

SAFE_SPEED = 250
SAFE_FORCE = 200
STOP_SPEED = 50
GRASP_PRE_DELAY = 0  # 抓/捏动作中，拇指根部预转后的等待时间（秒）

# 角度顺序：小指、无名指、中指、食指、拇指弯曲、拇指旋转。
GESTURES = {
    "open": {
        "angles": [1000, 1000, 1000, 1000, 1000, 1000],
        "desc": "五指张开",
    },
    "grab": {
        # 临时使用握水瓶参数，让 MediaPipe Closed_Fist 触发抓取动作。
        "angles": [500, 500, 500, 520, 520, 0],
        "desc": "抓取/握水瓶",
        "pre_angles": [1000, 1000, 1000, 1000, 1000, 0],
        "pre_delay": GRASP_PRE_DELAY,
    },
    "pinch": {
        "angles": [0, 0, 500, 520, 520, 0],
        "desc": "捏取(实测)",
        "pre_angles": [500, 500, 1000, 1000, 1000, 0],
        "pre_delay": GRASP_PRE_DELAY,
    },
    "point": {
        "angles": [0, 0, 0, 1000, 0, 180],
        "desc": "食指指(实测)",
    },
    "like": {
        "angles": [0, 0, 0, 0, 1000, 1000],
        "desc": "点赞(实测)",
    },
    "victory": {
        "angles": [0, 0, 1000, 1000, 250, 1000],
        "desc": "比耶(实测)",
    },
    "middle": {
        "angles": [0, 0, 1000, 0, 300, 1000],
        "desc": "中指(实测)",
    },
}


def execute_gesture(hand, name, speed=SAFE_SPEED, force=SAFE_FORCE, on_step=None):
    """执行配置动作；若有 pre_angles，则先执行预动作并等待，再执行目标动作。"""
    gesture = GESTURES[name]
    pre_angles = gesture.get("pre_angles")
    if pre_angles is not None:
        if on_step:
            on_step("pre", pre_angles, gesture)
        hand.set_angle(pre_angles, speed=[speed] * 6, force=[force] * 6)
        time.sleep(float(gesture.get("pre_delay", 0.0)))

    if on_step:
        on_step("target", gesture["angles"], gesture)
    hand.set_angle(gesture["angles"], speed=[speed] * 6, force=[force] * 6)
