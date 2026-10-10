"""Synthetic fast-speech transcript shaped like a real streamer VOD, for the
review-flag threshold tests (no real data in the repo)."""
import random

from core import Line

WORDS = ("so yeah this is what I was saying about the game earlier and honestly "
         "chat you guys are not ready for the next part because it gets crazy").split()


def streamer_lines(n: int = 4459, seed: int = 7) -> list:
    rng = random.Random(seed)
    lines, t = [], 0.0
    for i in range(n):
        dur = rng.uniform(2.0, 8.0)
        # Fast talkers say 2.5-4 words/s; ~20% of lines hold a mid-line pause
        # (reading chat), so far fewer words than the slot would fit.
        wps = rng.uniform(0.4, 1.5) if rng.random() < 0.2 else rng.uniform(2.4, 4.0)
        words = max(1, round(dur * wps))
        en = " ".join(rng.choice(WORDS) for _ in range(words))
        zh = "字" * max(1, round(dur * rng.uniform(4.0, 6.5)))
        lines.append(Line(idx=i, start=t, end=t + dur, zh=zh, en=en))
        r = rng.random()
        gap = (rng.uniform(0, 1) if r < 0.55 else rng.uniform(1, 3) if r < 0.9
               else min(60.0, 3 + rng.expovariate(1 / 8)))
        t += dur + gap
    return lines
