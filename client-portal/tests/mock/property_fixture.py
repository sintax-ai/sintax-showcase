"""Generic FICTIONAL property-service inquiries; not calls, alerts or dispatches."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def seed_property(inquiry, business):
    now = datetime.now(ZoneInfo('America/New_York'))
    previous_month = now.replace(day=1) - timedelta(days=2)
    examples = [
        ('Alex Example', 'water_damage', 0, 'Caller requests urgent direct contact',
         'Active basement water reported at 100 Example Lane, New City (FICTIONAL ADDRESS). Started about 30 minutes ago; caller reports no immediate life danger. Ground-floor entry; caller is homeowner. No crew or arrival time confirmed.', 'new', ''),
        ('Taylor Example', 'sewage_cleanup', 0, 'Caller requests direct assistance now',
         'Sewage backing up from basement drain at 200 Sample Court, Nanuet (FICTIONAL ADDRESS). Two fixtures affected; caller reports backup still active. Tenant; landlord contact to be arranged. No dispatch performed.', 'new', ''),
        ('Morgan Example', 'drain_cleaning', 1, 'Callback after 3 PM',
         'Recurring slow kitchen sink at 300 Example Way, Congers (FICTIONAL ADDRESS). No active overflow reported. Wants an estimate; camera inspection may be discussed by staff. No cause diagnosed or visit scheduled.', 'new', ''),
        ('Casey Example', 'mold_inquiry', 4, 'Weekday morning callback',
         'Reports a musty smell near a previously repaired leak at 400 Sample Lane, Valley Cottage (FICTIONAL ADDRESS). Requests a professional assessment discussion. No mold diagnosis, health assessment or coverage approval.', 'contacted', 'FICTIONAL staff note: called back and requested more context; appointment not confirmed.'),
        ('Riley Example', 'fire_smoke', 8, 'Callback before noon',
         'Smoke-odor cleanup estimate after a past kitchen incident at 500 Example Court, Nyack (FICTIONAL ADDRESS). Caller reports no active fire. Wants to discuss scope and insurance paperwork with staff.', 'contacted', 'FICTIONAL staff note: awaiting property manager reply.'),
        ('Sam Example', 'storm_damage', 15, 'Callback next weekday',
         'Water stain after a past storm at 600 Sample Way, Pearl River (FICTIONAL ADDRESS). No active leak reported. Wants cleanup and repair options. Site condition and estimate require review.', 'closed', 'FICTIONAL staff note: caller postponed request.'),
        ('Jamie Example', 'camera_inspection', None, 'Flexible weekday callback',
         'Requests camera inspection estimate for recurring sewer blockage at 700 Example Lane, Suffern (FICTIONAL ADDRESS). No current backup reported. Access through outside cleanout according to caller.', 'closed', 'FICTIONAL staff note: question answered; no appointment booked.'),
        ('Avery Example', 'general_inquiry', 2, 'Callback after 5 PM',
         'Property manager asks about multi-unit service coverage and billing at 800 Sample Court, New City (FICTIONAL ADDRESS). Wants a point of contact for future incidents; no current emergency.', 'new', ''),
    ]
    rows = []
    for i, (name, service, days, timing, details, status, notes) in enumerate(examples, 101):
        received = (previous_month if days is None else now - timedelta(days=days)).isoformat()
        phone = f'+1202555{i:04d}'
        transcript = {'saved_at': received, 'turns': [
            {'speaker': 'receptionist', 'text': 'Hi, this is Sophia at the service desk. How can I help?'},
            {'speaker': 'caller', 'text': details},
            {'speaker': 'receptionist', 'text': 'What is your name and best callback number?'},
            {'speaker': 'caller', 'text': f'{name}, {phone}. {timing}.'},
            {'speaker': 'receptionist', 'text': 'Thank you. I cannot confirm a dispatch or arrival time from this line.'},
        ]}
        rows.append(inquiry(i, business, name, phone, service, timing, 'Not applicable',
                            'FICTIONAL intake example. ' + details, received, status, notes, transcript))
    return rows
