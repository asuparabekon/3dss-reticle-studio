"""X-Ray exposure history for a supplied scene-average luminance.

Matches phase_luminance's smoothed frame coefficient and bloom_luminance_3's
exposure interpolation. The scene-average input is supplied, not inferred from
an SDR screenshot. The glow shader consumes the resulting exposure unchanged.
"""

import numpy as np


class Adaptation:
    def __init__(self):
        self.coefficient = np.float32(0.5)
        self.exposure = np.float32(127 / 255)
        self.remainder = 0.0

    def advance(self, elapsed, luminance, speed, middle_grey, amount, floor, fps=60):
        dt = 1 / fps
        # A suspended window resumes the simulation without replaying hours on the UI thread.
        self.remainder += min(max(elapsed, 0.0), 1.0)
        count = int(self.remainder / dt)
        self.remainder -= count * dt
        f = np.float32
        grey = f(1 + (middle_grey - 1) * amount)
        low = f(1 + (floor - 1) * amount)
        target = grey / f(f(luminance) * f(amount) + low)
        for _ in range(count):
            self.coefficient = f(f(0.9) * self.coefficient + f(0.1) * f(dt) * f(speed))
            self.exposure = f(self.exposure + f(target - self.exposure) * self.coefficient)
        return float(self.exposure)
