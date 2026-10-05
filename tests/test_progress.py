import unittest
from drivebridge.progress import TransferStats, format_bytes


class TransferRateTests(unittest.TestCase):
    def test_rate_measures_confirmed_bytes_with_monotonic_time(self):
        stats = TransferStats()
        stats.reset(10)
        stats.update(2 * 1024 * 1024, 10 * 1024 * 1024, 12)
        rate, average = stats.rates(12)
        self.assertEqual(rate, 1024 * 1024)
        self.assertEqual(average, rate)
        self.assertEqual(format_bytes(rate) + '/s', '1.0 MiB/s')

    def test_idle_rate_drops_to_zero(self):
        stats = TransferStats()
        stats.reset(0)
        stats.update(100, 1000, 1)
        self.assertEqual(stats.rates(6)[0], 0)

    def test_final_average_stays_fixed(self):
        stats = TransferStats()
        stats.reset(10)
        stats.update(1000, 1000, 12)
        stats.finished = 12
        stats.active = False
        self.assertEqual(stats.rates(100), (0, 500))

    def test_empty_files_and_reset(self):
        stats = TransferStats()
        stats.reset(1)
        stats.update(0, 0, 1)
        self.assertEqual(stats.rates(1), (0, 0))
        stats.update(100, 100, 2)
        stats.reset(3)
        self.assertEqual(stats.rates(3), (0, 0))
