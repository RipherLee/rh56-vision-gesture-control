from types import SimpleNamespace
import unittest
import os

from gesture_config import GESTURES, GRASP_PRE_DELAY, SAFE_SPEED, execute_gesture
from hand_driver import DEFAULT_RS485_PORT
from vision_gesture_control import (
    GestureStabilizer,
    classify_result,
    is_pinch,
    next_video_timestamp_ms,
)


def point(x, y, z=0.0):
    return SimpleNamespace(x=x, y=y, z=z)


def landmarks(thumb=(0.0, 0.0), index=(1.0, 0.0)):
    values = [point(0.0, 0.0) for _ in range(21)]
    values[9] = point(0.0, 1.0)  # 掌长 = 1
    values[4] = point(*thumb)
    values[8] = point(*index)
    return values


def result(category="None", score=0.9, marks=None):
    return SimpleNamespace(
        hand_landmarks=[marks or landmarks()],
        gestures=[[SimpleNamespace(category_name=category, score=score)]],
    )


class GestureRecognitionTests(unittest.TestCase):
    def test_new_gestures_and_default_speed(self):
        self.assertIn("grab", GESTURES)
        self.assertNotIn("fist", GESTURES)
        self.assertNotIn("bottle", GESTURES)
        self.assertEqual(GESTURES["middle"]["angles"], [0, 0, 1000, 0, 300, 1000])
        self.assertEqual(SAFE_SPEED, 250)
        self.assertEqual(GRASP_PRE_DELAY, 0)
        expected_port = "COM7" if os.name == "nt" else "/dev/ttyUSB0"
        self.assertEqual(DEFAULT_RS485_PORT, os.environ.get("RH56_PORT", expected_port))

    def test_grab_has_configured_two_stage_motion(self):
        self.assertEqual(GESTURES["grab"]["angles"][3:6], [520, 520, 0])
        self.assertEqual(GESTURES["pinch"]["angles"][3:6], [520, 520, 0])
        self.assertEqual(len(GESTURES["grab"]["pre_angles"]), 6)
        self.assertEqual(len(GESTURES["pinch"]["pre_angles"]), 6)
        self.assertEqual(GESTURES["grab"]["pre_angles"][5], 0)
        self.assertEqual(GESTURES["pinch"]["pre_angles"][5], 0)

    def test_two_stage_motion_uses_configured_pre_angles_first(self):
        class FakeHand:
            def __init__(self):
                self.calls = []

            def set_angle(self, angles, speed, force):
                self.calls.append((list(angles), list(speed), list(force)))

        hand = FakeHand()
        old_delay = GESTURES["pinch"]["pre_delay"]
        GESTURES["pinch"]["pre_delay"] = 0
        try:
            execute_gesture(hand, "pinch", speed=250, force=200)
        finally:
            GESTURES["pinch"]["pre_delay"] = old_delay
        self.assertEqual(hand.calls[0][0], GESTURES["pinch"]["pre_angles"])
        self.assertEqual(hand.calls[1][0], GESTURES["pinch"]["angles"])

    def test_video_timestamp_is_strictly_increasing(self):
        previous = 10**12
        self.assertEqual(next_video_timestamp_ms(0.0, previous), previous + 1)

    def test_canned_mapping(self):
        expected = {
            "Open_Palm": "open",
            "Closed_Fist": "grab",
            "Pointing_Up": "point",
            "Thumb_Up": "like",
            "Victory": "victory",
        }
        for canned, hand in expected.items():
            self.assertEqual(classify_result(result(canned), 0.6, 0.33)[0], hand)

    def test_pinch_by_normalized_tip_distance(self):
        marks = landmarks(thumb=(0.0, 0.0), index=(0.2, 0.0))
        self.assertTrue(is_pinch(marks, 0.33))
        self.assertEqual(classify_result(result(marks=marks), 0.6, 0.33)[0], "pinch")

    def test_closed_fist_maps_to_grab_and_wins_over_pinch_rule(self):
        marks = landmarks(thumb=(0.0, 0.0), index=(0.1, 0.0))
        self.assertEqual(classify_result(result("Closed_Fist", marks=marks), 0.6, 0.33)[0], "grab")

    def test_stabilizer_emits_once_and_rearms_after_empty_frames(self):
        stable = GestureStabilizer(3)
        self.assertIsNone(stable.update("open"))
        self.assertIsNone(stable.update("open"))
        self.assertEqual(stable.update("open"), "open")
        self.assertIsNone(stable.update("open"))
        for _ in range(3):
            self.assertIsNone(stable.update(None))
        stable.update("open")
        stable.update("open")
        self.assertEqual(stable.update("open"), "open")


if __name__ == "__main__":
    unittest.main()
