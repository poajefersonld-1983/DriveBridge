import unittest
from datetime import datetime
from drivebridge.scheduling import ZONE, next_run, normalize_schedule
from drivebridge.core import plan


class CalendarTests(unittest.TestCase):
    def profile(self, **values):
        schedule=dict(frequency='days',every=1,start='time',time='12:00',weekday=0,day=31)
        schedule.update(values)
        return {'enabled': True, 'schedule':schedule}

    def date(self, result):
        return datetime.fromtimestamp(result,ZONE)

    def test_next_daily_time_and_two_day_repeat(self):
        before=datetime(2026,10,5,10,tzinfo=ZONE)
        self.assertEqual(self.date(next_run(self.profile(),before)),datetime(2026,10,5,12,tzinfo=ZONE))
        after=datetime(2026,10,5,13,tzinfo=ZONE)
        self.assertEqual(self.date(next_run(self.profile(every=2),after)),datetime(2026,10,7,12,tzinfo=ZONE))

    def test_weekday_and_next_week(self):
        now=datetime(2026,10,5,13,tzinfo=ZONE)
        self.assertEqual(self.date(next_run(self.profile(frequency='weeks',weekday=2),now)),datetime(2026,10,7,12,tzinfo=ZONE))
        self.assertEqual(self.date(next_run(self.profile(frequency='weeks',weekday=0,every=2),now)),datetime(2026,10,19,12,tzinfo=ZONE))

    def test_month_end_clamps_and_restores_day_31(self):
        now=datetime(2027,1,31,13,tzinfo=ZONE)
        feb=self.date(next_run(self.profile(frequency='months'),now))
        self.assertEqual(feb,datetime(2027,2,28,12,tzinfo=ZONE))
        march=self.date(next_run(self.profile(frequency='months'),feb))
        self.assertEqual(march,datetime(2027,3,31,12,tzinfo=ZONE))

    def test_startup_then_repeat_and_manual(self):
        now=datetime(2026,10,5,13,tzinfo=ZONE)
        profile=self.profile(frequency='hours',every=6,start='startup')
        self.assertEqual(next_run(profile,now,initial=True),now.timestamp())
        self.assertEqual(self.date(next_run(profile,now)),datetime(2026,10,5,19,tzinfo=ZONE))
        self.assertIsNone(next_run(self.profile(frequency='manual'),now))
        self.assertEqual(normalize_schedule({'enabled':True,'interval':360})['start'],'interval')

    def test_overwrite_rules_and_bidirectional_newest(self):
        old={'md5Checksum':'old','size':10,'modifiedTime':100}
        new={'md5Checksum':'new','size':10,'modifiedTime':200}
        self.assertEqual(plan({'a':old},{'a':new},{},'upload','newer'),([],[]))
        self.assertEqual(plan({'a':new},{'a':old},{},'both','newer'),([('upload','a',10)],[]))
        self.assertEqual(plan({'a':old},{'a':new},{},'both','newer'),([('download','a',10)],[]))
        self.assertEqual(plan({'a':old},{'a':old},{},'upload','always'),([('upload','a',10)],[]))
        self.assertEqual(plan({'a':old},{'a':old},{},'upload','changed'),([],[]))
        missing=dict(new);missing.pop('modifiedTime')
        self.assertEqual(plan({'a':old},{'a':missing},{},'both','newer'),([],['a']))
