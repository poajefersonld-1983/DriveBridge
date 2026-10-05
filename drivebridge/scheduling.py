"""Agendamento por intervalo ou calendário, no fuso do servidor."""
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Sao_Paulo')
FREQUENCIES = {'minutes': 1, 'hours': 60, 'days': 1440, 'weeks': 10080, 'months': 43200, 'manual': 1}


def normalize_schedule(profile):
    value = profile.get('schedule')
    if value is None:
        return {'frequency': 'minutes' if profile.get('enabled') else 'manual', 'every': max(1, int(profile.get('interval', 15))), 'start': 'interval', 'time': '12:00', 'weekday': 0, 'day': 1}
    if not isinstance(value, dict):
        raise ValueError('Agendamento inválido.')
    frequency = value.get('frequency', 'manual')
    start = value.get('start', 'startup')
    every = int(value.get('every', 1))
    weekday, day = int(value.get('weekday', 0)), int(value.get('day', 1))
    clock = str(value.get('time', '12:00'))
    if frequency not in FREQUENCIES or start not in ('startup', 'time', 'interval'):
        raise ValueError('Escolha uma frequência e um início válidos.')
    if not 1 <= every <= 525600 // FREQUENCIES[frequency]:
        raise ValueError('Escolha um intervalo entre 1 e 365 dias (até 12 meses).')
    if not 0 <= weekday <= 6 or not 1 <= day <= 31:
        raise ValueError('Escolha um dia válido para o agendamento.')
    try:
        datetime.strptime(clock, '%H:%M')
    except ValueError:
        raise ValueError('Informe o horário no formato HH:MM.') from None
    return dict(frequency=frequency, every=every, start=start, time=clock, weekday=weekday, day=day)


def advance(value, schedule):
    if schedule['frequency'] == 'months':
        index = value.year * 12 + value.month - 1 + schedule['every']
        year, month = divmod(index, 12)
        month += 1
        day = min(schedule['day'] if schedule['start'] == 'time' else value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)
    return value + timedelta(minutes=FREQUENCIES[schedule['frequency']] * schedule['every'])


def next_run(profile, now=None, initial=False):
    schedule = normalize_schedule(profile)
    if schedule['frequency'] == 'manual':
        return None
    now = now or datetime.now(ZONE)
    if schedule['start'] == 'startup' and initial:
        return now.timestamp()
    if schedule['start'] != 'time':
        return advance(now, schedule).timestamp()
    hour, minute = map(int, schedule['time'].split(':'))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule['frequency'] == 'weeks':
        candidate += timedelta(days=(schedule['weekday'] - candidate.weekday()) % 7)
    elif schedule['frequency'] == 'months':
        candidate = candidate.replace(day=min(schedule['day'], calendar.monthrange(candidate.year, candidate.month)[1]))
    while candidate <= now:
        candidate = advance(candidate, schedule)
    return candidate.timestamp()
