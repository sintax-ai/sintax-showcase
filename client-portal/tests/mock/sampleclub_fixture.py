"""Fictional sales examples, not verified SAMPLECLUB offerings or real phone calls."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def seed_sampleclub(inquiry, business):
    now = datetime.now(ZoneInfo('America/New_York'))
    prior_month = now.replace(day=1) - timedelta(days=2)
    # Received dates are relative so Today / Week / Month remain useful in demos.
    examples = [
        ('Alex Example', 'private_event', 0, 'Friday evening; exact date to confirm', 'About 2 hours',
         'Corporate group of 20; mostly beginners. Asked about paddles and an instructor. Team must confirm availability, package options and price.',
         'new', '', 'I am organizing a corporate event for 20 people. Most of us are beginners. Could we get paddles and some instruction?'),
        ('Taylor Example', 'lesson', 0, 'A weekday after 5 PM', 'To discuss with the instructor',
         'Two adults who have never played. Wants to compare a private lesson with a beginner clinic. Asked what equipment to bring; options and price need confirmation.',
         'new', '', 'My partner and I have never played. Could someone help us choose between a lesson and a beginner clinic?'),
        ('Morgan Example', 'open_play', 1, 'An upcoming weekday evening; date unconfirmed', 'One session; length to confirm',
         'Beginner visiting alone. Asked how players are matched and whether membership is required. No live schedule or available spaces checked.',
         'new', '', 'I would like to try beginner open play on a weekday evening. Can I come alone, and do I need a membership?'),
        ('Jordan Example', 'court_rental', 3, 'A weekend morning; flexible on date', '90 minutes requested',
         'Four friends; two need rental paddles. Wants court pricing and availability. Staff must confirm both before any reservation.',
         'contacted', 'Sample staff note: left a voicemail asking which weekend works.',
         'Four of us would like a court for 90 minutes one weekend morning. Two of us need paddles.'),
        ('Casey Example', 'membership', 8, 'Callback during lunch, noon to 1 PM', 'Not applicable',
         'Expects to play twice a week. Asked about membership benefits, guest access and cancellation terms. Interested in comparing options before deciding.',
         'contacted', 'Sample staff note: sent an example comparison; awaiting reply.',
         'I would probably play twice a week. What should I know about membership and bringing a guest?'),
        ('Riley Example', 'general_inquiry', 15, 'Callback after 4 PM', 'Not applicable',
         'Asked whether there are programs suitable for a 12-year-old beginner. Team must verify age eligibility, supervision and current program availability.',
         'closed', 'Sample staff note: answered the question by phone; no booking made.',
         'My child is 12 and has never played. Could someone tell me whether you have anything appropriate?'),
        ('Sam Example', 'private_event', None, 'A Saturday afternoon; date to discuss', '2 hours requested',
         'Birthday gathering for 12 adults. Asked about space, food policy and total cost. No package, date or reservation confirmed.',
         'closed', 'Sample staff note: caller postponed the event; closed for now.',
         'I am thinking about a birthday gathering for 12 adults. Could we discuss the space and food rules?'),
        ('Jamie Example', 'general_inquiry', 2, 'Callback as soon as staff can review', 'Not applicable',
         'Caller says an online booking is not showing in their account. No account access or verification performed. Staff needs to investigate with the caller.',
         'new', '', 'I thought I made a booking online, but I cannot see it in my account. Can someone help me check?'),
        ('Avery Example', 'membership', 0, 'Callback after 5 PM on a weekday', 'Not applicable',
         'New player interested in joining and playing two or three evenings per week. Asked about monthly versus annual options, joining fees, guest privileges and whether open play is included. Wants to start next month. Team must confirm current plans, prices and terms; no membership or payment arranged.',
         'new', '', 'I am interested in joining and playing two or three evenings a week. Could someone explain monthly versus annual membership, any joining fees, and whether I can bring a guest? I would like to start next month. Please call after 5 PM on a weekday.'),
    ]
    rows = []
    for i, (name, service, days, requested, duration, details, status, notes, spoken) in enumerate(examples, 6):
        received = (prior_month if days is None else now - timedelta(days=days)).isoformat()
        transcript = {'saved_at': received, 'turns': [
            {'speaker': 'receptionist', 'text': 'Hi, this is Sophia at the pickleball club. How can I help?'},
            {'speaker': 'caller', 'text': spoken},
            {'speaker': 'receptionist', 'text': 'What timing works for you?'},
            {'speaker': 'caller', 'text': requested},
            {'speaker': 'receptionist', 'text': 'May I have your name and the best callback number?'},
            {'speaker': 'caller', 'text': f'{name}. My example number is 202-555-{100 + i:04d}.'},
            {'speaker': 'receptionist', 'text': 'Thank you. The team will need to confirm the details with you. This is an inquiry, not a confirmed booking.'},
        ]}
        rows.append(inquiry(i, business, name, f'+1202555{100 + i:04d}', service,
                            requested, duration, details, received, status, notes, transcript))
    return rows
