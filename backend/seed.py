from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import calendar


def demo_data():
    tz = ZoneInfo('America/New_York')
    today = datetime.now(tz).date()
    first = today.replace(day=1)
    days = calendar.monthrange(today.year, today.month)[1]
    names = ['Amelia Chen','Marcus Reed','Sofia Patel','Ethan Brooks','Olivia Grant','Noah Williams','Isabel Torres','Daniel Kim','Priya Shah','Lucas Rivera','Emma Johnson','James Wilson','Mia Thompson','Liam Davis','Ava Martinez','Benjamin Clark','Charlotte Lewis','Henry Walker','Harper Allen','Elijah Young']
    clinicians = []
    for index, name in enumerate(names):
        role = 'MD' if index < 8 else ('CRNA' if index < 18 else ('CAA' if index == 18 else 'PA'))
        clinicians.append(dict(id=f'c{index+1:02}', name=name, role=role, specialties=['General','Orthopedics'] + (['OB'] if index < 12 else []), sites=['main','riverside'], availability={'weekdays':[0,1,2,3,4], 'start':'07:00','end':'19:00'}, fte=1.0, target_hours=144, preferred_shift_hours=[12] if index%3 else [24,12], preferred_specialties=['OB'] if index in [0,2,8,9] else ['Orthopedics'], call_eligible=role in ['MD','CRNA'], supervision_required=role in ['CRNA','CAA'], active=True))
    shifts, cases = [], []
    def stamp(day,hour):
        return datetime.combine(day,time(hour),tz).isoformat()
    def shift(day, site, spec, role, kind, hours):
        sid = f's-{day.isoformat()}-{site}-{spec}-{role}-{kind}'
        start = datetime.combine(day,time(7),tz)
        shifts.append(dict(id=sid,start=start.isoformat(),end=(start+timedelta(hours=hours)).isoformat(),site_id=site,specialty=spec,role=role,kind=kind,clinician_id=None,locked=False,status='draft'))
    for offset in range(days):
        day=first+timedelta(days=offset)
        shift(day,'main','OB','MD','primary_call',24)
        shift(day,'main','OB','CRNA','primary_call',24)
        if day.weekday()<5:
            for site,spec,role in [('main','General','MD'),('main','OB','CRNA'),('main','Orthopedics','CRNA'),('riverside','General','MD'),('riverside','Orthopedics','CRNA')]:
                shift(day,site,spec,role,'day',12)
            for site,room,spec in [('main','OR 1','OB'),('main','OR 2','Orthopedics'),('riverside','OR 1','Orthopedics')]:
                for n,(hour,duration) in enumerate([(8,3),(12,3)]):
                    cases.append(dict(id=f'case-{day}-{site}-{room}-{n}',title='Scheduled OB procedure' if spec=='OB' else ('Joint replacement' if n==0 else 'Arthroscopy'),room=room,start=stamp(day,hour),end=stamp(day,hour+duration),site_id=site,specialty=spec,role='CRNA',clinician_id=None,locked=False))
    leave_start = first + timedelta(days=7)
    leave_end = leave_start+timedelta(days=14)
    requests=[dict(id='vacation-demo',kind='leave',clinician_id='c09',start=stamp(leave_start,0),end=stamp(leave_end,0),note='Two-week vacation — review its capacity impact.',status='pending',details={},created_by='clinician',created_at=datetime.now(tz).isoformat())]
    return dict(settings=dict(timezone='America/New_York',min_rest_hours=10,post_call_rest_hours=12,max_weekly_hours=60,supervision_ratio=3,require_scheduler_approval=True,require_swap_acceptance=True,allow_leave_shortage=True,demo=True),sites=[dict(id='main',name='Memorial Hospital'),dict(id='riverside',name='Riverside Surgery Center')],clinicians=clinicians,shifts=shifts,cases=cases,requests=requests)
