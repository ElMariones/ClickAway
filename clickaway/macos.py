"""Quartz mouse injection. Called only after explicit pairing and Accessibility approval."""

import threading
import time

import AppKit
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    kAXTrustedCheckOptionPrompt,
)
import Quartz as Q

from .geometry import Screen, clamp


def accessibility(prompt=False):
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))


def screen_recording(prompt=False):
    """Whether macOS lets ClickAway capture the screen, and with it the sound.

    Granting it takes effect only after ClickAway is reopened, which is macOS's
    rule for this permission rather than something ClickAway can work around.
    """
    if prompt and not Q.CGPreflightScreenCaptureAccess():
        return bool(Q.CGRequestScreenCaptureAccess())
    return bool(Q.CGPreflightScreenCaptureAccess())


def screens():
    # CG coordinates are logical display points, including on Retina screens.
    _, ids, _ = Q.CGGetActiveDisplayList(32, None, None)
    result = []
    main = Q.CGMainDisplayID()
    for display_id in sorted(ids, key=lambda d: d != main):
        bounds = Q.CGDisplayBounds(display_id)
        label = f"Display {len(result) + 1} · {int(bounds.size.width)} × {int(bounds.size.height)}"
        if display_id == main:
            label += " (main)"
        result.append(
            Screen(
                bounds.origin.x,
                bounds.origin.y,
                bounds.size.width,
                bounds.size.height,
                label,
            )
        )
    return result


class MouseReceiver:
    BUTTONS = {"left": 0, "right": 1, "middle": 2, "back": 3, "forward": 4}

    def __init__(self, screen):
        self.screen = screen
        self.point = (screen.x, screen.y)
        self.active = False
        self.buttons = set()
        self.clicks = {}
        self.lock = threading.RLock()
        self.source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)

    def handle(self, m):
        with self.lock:
            kind = m["type"]
            if kind == "leave":
                self.release()
                return
            if kind == "enter":
                if not accessibility():
                    raise PermissionError(
                        "Enable Accessibility for ClickAway on the Mac"
                    )
                self.active = True
            if not self.active:
                return
            if kind in ("enter", "move"):
                s = self.screen
                self.point = (
                    s.x + clamp(m["x"], 0, s.width - 1),
                    s.y + clamp(m["y"], 0, s.height - 1),
                )
                button = 0
                event_type = Q.kCGEventMouseMoved
                if self.buttons:
                    button = min(self.buttons)
                    event_type = (
                        Q.kCGEventLeftMouseDragged
                        if button == 0
                        else Q.kCGEventRightMouseDragged
                        if button == 1
                        else Q.kCGEventOtherMouseDragged
                    )
                self._mouse(event_type, button)
            elif kind == "button":
                button = self.BUTTONS[m["button"]]
                down = m["down"]
                if down:
                    self.buttons.add(button)
                    now = time.monotonic()
                    last_time, last_point, count = self.clicks.get(
                        button, (0, self.point, 0)
                    )
                    nearby = (
                        sum((a - b) ** 2 for a, b in zip(last_point, self.point)) <= 25
                    )
                    count = (
                        count + 1
                        if now - last_time <= AppKit.NSEvent.doubleClickInterval()
                        and nearby
                        else 1
                    )
                    self.clicks[button] = (now, self.point, count)
                else:
                    self.buttons.discard(button)
                self._button(button, down)
            elif kind == "scroll":
                # Windows wheel units: 120 per detent; pixel events preserve small deltas.
                event = Q.CGEventCreateScrollWheelEvent(
                    self.source,
                    Q.kCGScrollEventUnitPixel,
                    2,
                    round(m["dy"] / 3),
                    round(-m["dx"] / 3),
                )
                if event:
                    Q.CGEventSetFlags(
                        event,
                        Q.CGEventSourceFlagsState(
                            Q.kCGEventSourceStateCombinedSessionState
                        ),
                    )
                    Q.CGEventPost(Q.kCGHIDEventTap, event)

    def _mouse(self, kind, button, click_count=None):
        event = Q.CGEventCreateMouseEvent(self.source, kind, self.point, button)
        if event is None:
            raise RuntimeError("macOS could not create a mouse event")
        Q.CGEventSetFlags(
            event, Q.CGEventSourceFlagsState(Q.kCGEventSourceStateCombinedSessionState)
        )
        if click_count is not None:
            Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, click_count)
        Q.CGEventPost(Q.kCGHIDEventTap, event)

    def _button(self, button, down):
        kinds = (
            (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp),
            (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp),
            (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp),
        )
        kind = kinds[min(button, 2)][0 if down else 1]
        self._mouse(kind, button, self.clicks.get(button, (0, None, 1))[2])

    def release(self):
        with self.lock:
            try:
                for button in list(self.buttons):
                    self._button(button, False)
            finally:
                self.buttons.clear()
                self.clicks.clear()
                self.active = False
