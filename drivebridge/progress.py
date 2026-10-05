"""Métricas de bytes confirmados pela API, com relógio monotônico."""
from collections import deque


def format_bytes(value):
    value = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


class TransferStats:
    def __init__(self):
        self.reset(0)

    def reset(self, now):
        self.samples = deque([(now, 0)])
        self.started = now
        self.done = 0
        self.total = 0
        self.active = True
        self.finished = None

    def update(self, done, total, now):
        self.done, self.total = done, total
        if now >= self.samples[-1][0]:
            self.samples.append((now, done))

    def rates(self, now):
        while len(self.samples) > 2 and self.samples[1][0] <= now - 5:
            self.samples.popleft()
        elapsed = (self.finished if self.finished is not None else now) - self.started
        average = self.done / elapsed if elapsed > 0 else 0
        start, before = self.samples[0]
        window = now - start
        rate = max(0, self.done - before) / window if window > 0 else 0
        if not self.active or now - self.samples[-1][0] >= 5:
            rate = 0
        return rate, average
