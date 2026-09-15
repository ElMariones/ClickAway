"""Pure screen geometry, independent of either operating system."""

from dataclasses import dataclass
import math


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Screen:
    x: float
    y: float
    width: float
    height: float
    name: str = "Display"

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.x, self.y, self.width, self.height)):
            raise ValueError("Screen dimensions must be finite")
        if not 100 <= self.width <= 32768 or not 100 <= self.height <= 32768:
            raise ValueError("Screen dimensions are outside the supported range")


class Portal:
    def __init__(self, local: Screen, remote: Screen, side="left", speed=1.0):
        if side not in ("left", "right"):
            raise ValueError("Mac position must be left or right")
        if not math.isfinite(speed) or not 0.25 <= speed <= 3.0:
            raise ValueError("Speed must be between 0.25 and 3")
        self.local, self.remote, self.side, self.speed = local, remote, side, speed
        self.x = self.y = 0.0

    def at_edge(self, x, y, beyond=False):
        """``beyond`` accepts overshoot past an edge with no other display behind it."""
        s = self.local
        if not s.y <= y < s.y + s.height:
            return False
        if self.side == "left":
            return s.x <= x <= s.x + 1 or (beyond and x < s.x)
        right = s.x + s.width
        return right - 2 <= x < right or (beyond and x >= right)

    def enter(self, local_y):
        self.x = self.remote.width - 3 if self.side == "left" else 2.0
        self.y = clamp((local_y - self.local.y) / (self.local.height - 1), 0, 1) * (
            self.remote.height - 1
        )
        return self.x, self.y

    def move(self, dx, dy, dragging=False):
        x = self.x + dx * self.speed
        self.y = clamp(self.y + dy * self.speed, 0, self.remote.height - 1)
        leave = x >= self.remote.width - 1 if self.side == "left" else x <= 0
        self.x = clamp(x, 0, self.remote.width - 1)
        return leave and not dragging, self.x, self.y

    def return_position(self):
        s = self.local
        x = s.x + 12 if self.side == "left" else s.x + s.width - 13
        y = s.y + self.y / (self.remote.height - 1) * (s.height - 1)
        return round(x), round(y)
