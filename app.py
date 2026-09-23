from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps
import os, json, hashlib, secrets, re
import firebase_admin
from firebase_admin import credentials, firestore
from flask_cors import CORS

from kira_services.ai_provider import create_chat_provider
from kira_services.config import KiraConfig
from kira_services.context_builder import build_context
from kira_services.knowledge import KnowledgeRepository
from kira_services.retrieval_gate import classify_retrieval
from kira_services.safety import evaluate_safety, urgent_response
from kira_services.session import (
    DEFAULT_SESSION_ID,
    add_fact_ids,
    get_recent_messages,
    load_session,
    update_summary_if_needed,
)
from kira_services.user_context import get_relevant_user_context

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Initialize Firebase

google_creds = os.getenv("GOOGLE_CREDENTIALS")
if google_creds:
    cred = credentials.Certificate(json.loads(google_creds))
else:
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not credential_path:
        raise RuntimeError("Set GOOGLE_APPLICATION_CREDENTIALS for your own test Firebase project. See README.md.")
    cred = credentials.Certificate(credential_path)

firebase_admin.initialize_app(cred)
db = firestore.client()

config = KiraConfig.from_env()
chat_provider = create_chat_provider(config)
print(
    "Kira AI configured:",
    f"provider={config.ai_provider}",
    f"model={config.chat_model}",
    f"api_key_present={bool(config.ai_api_key)}",
)

app = Flask(__name__)
CORS(app)
knowledge_repo = KnowledgeRepository(
    db=db,
    fallback_path=os.path.join(os.path.dirname(__file__), "knowledge_base", "rag_chunks.jsonl"),
)

# ─── Helper: Per-User Subcollection ─────────────────────────

def user_collection(user_id, collection_name):
    return db.collection('users').document(user_id).collection(collection_name)

USER_DATA_COLLECTIONS = [
    'chat_history',
    'chat_sessions',
    'diet_logs',
    'emotion_logs',
    'intervention_logs',
    'journal_logs',
    'sleep_logs',
]

def delete_collection_documents(collection_ref, batch_size=100):
    deleted = 0
    while True:
        docs = list(collection_ref.limit(batch_size).stream())
        if not docs:
            break
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)
    return deleted

def user_data_collections(user_ref):
    try:
        collections = list(user_ref.collections())
        if collections:
            return collections
    except Exception:
        pass
    return [user_ref.collection(collection_name) for collection_name in USER_DATA_COLLECTIONS]

def make_api_error(code, user_message, status=400):
    return jsonify({
        "success": False,
        "error": {
            "code": code,
            "userMessage": user_message
        }
    }), status

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def issue_auth_token(username):
    token = secrets.token_urlsafe(32)
    db.collection('accounts').document(username).set({
        'session_token_hash': hash_token(token),
        'session_token_updated_at': datetime.now().strftime("%Y-%m-%d %H:%M")
    }, merge=True)
    return token

def request_payload():
    data = request.get_json(silent=True)
    if isinstance(data, list):
        return {item[0]: item[1] for item in data}
    if isinstance(data, str):
        try:
            return json.loads(data)
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}

def request_user_id():
    if request.method == 'GET':
        return request.args.get('user_id', 'user_1')
    data = request_payload()
    return data.get('user_id', 'user_1')

def bearer_token():
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    return ''

def require_user_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not config.require_auth:
            return fn(*args, **kwargs)

        user_id = request_user_id()
        token = bearer_token()
        if not user_id or not token or user_id == 'user_1':
            return make_api_error("AUTH_REQUIRED", "Please log in again.", 401)

        account = db.collection('accounts').document(user_id).get()
        account_data = account.to_dict() if account.exists else {}
        expected_hash = account_data.get('session_token_hash')
        if not expected_hash or not secrets.compare_digest(expected_hash, hash_token(token)):
            return make_api_error("AUTH_REQUIRED", "Please log in again.", 401)

        return fn(*args, **kwargs)
    return wrapper

# ─── User Summary Functions ──────────────────────────────────

def get_user_summary(user_id='user_1'):
    doc = db.collection('users').document(user_id).get()
    base = {
        "last_updated": None,
        "cycle_summary": {
            "average_cycle_length": 28,
            "cycles_tracked": 0
        },
        "emotion_patterns": {
            "most_common_emotion": None,
            "highest_risk_phase": None,
            "highest_risk_days": []
        },
        "sleep_patterns": {
            "average_hours": None,
            "worst_phase": None,
            "average_quality": None
        },
        "diet_patterns": {
            "most_common_craving": None,
            "craving_phase": None
        },
        "effective_interventions": [],
        "intervention_summary": {
            "most_effective": None,
            "least_effective": None,
            "total_tried": 0,
            "favourite_category": None
        }
    }
    if doc.exists:
        data = doc.to_dict()
        for key in base:
            if key not in data:
                data[key] = base[key]
        return data
    return base

def update_user_summary(user_id='user_1'):
    emotion_logs = [d.to_dict() for d in user_collection(user_id, 'emotion_logs').stream()]
    sleep_logs = [d.to_dict() for d in user_collection(user_id, 'sleep_logs').stream()]
    diet_logs = [d.to_dict() for d in user_collection(user_id, 'diet_logs').stream()]

    summary = get_user_summary(user_id)

    if emotion_logs:
        emotions = [l['emotion'] for l in emotion_logs]
        summary['emotion_patterns']['most_common_emotion'] = max(set(emotions), key=emotions.count)

        luteal_logs = [l for l in emotion_logs if l['phase'] == 'luteal']
        if luteal_logs:
            summary['emotion_patterns']['highest_risk_phase'] = 'luteal'
            days = [l['cycle_day'] for l in luteal_logs]
            summary['emotion_patterns']['highest_risk_days'] = list(set(days))

    if sleep_logs:
        hours = [l['hours'] for l in sleep_logs]
        summary['sleep_patterns']['average_hours'] = round(sum(hours) / len(hours), 1)
        qualities = [l['quality'] for l in sleep_logs]
        summary['sleep_patterns']['average_quality'] = max(set(qualities), key=qualities.count)

    if diet_logs:
        cravings = [l['craving'] for l in diet_logs if l.get('craving')]
        if cravings:
            summary['diet_patterns']['most_common_craving'] = max(set(cravings), key=cravings.count)

    intervention_logs = [d.to_dict() for d in user_collection(user_id, 'intervention_logs').stream()]
    if intervention_logs:
        from collections import defaultdict
        ratings_by_id = defaultdict(list)
        categories_count = defaultdict(int)
        for log in intervention_logs:
            iid = log.get('intervention_id', log.get('intervention_name', ''))
            rating = log.get('rating', 0)
            if iid and rating:
                ratings_by_id[iid].append(rating)
            cat = log.get('intervention_type', '')
            if cat:
                categories_count[cat] += 1

        if ratings_by_id:
            avg_ratings = {k: sum(v) / len(v) for k, v in ratings_by_id.items()}
            best = max(avg_ratings, key=avg_ratings.get)
            worst = min(avg_ratings, key=avg_ratings.get)
            summary['intervention_summary'] = {
                'most_effective': best,
                'least_effective': worst,
                'total_tried': len(ratings_by_id),
                'favourite_category': max(categories_count, key=categories_count.get) if categories_count else None
            }

    summary['last_updated'] = datetime.now().strftime("%Y-%m-%d")
    db.collection('users').document(user_id).set(summary)
    return summary

# ─── Cycle Functions ─────────────────────────────────────────

def calculate_cycle_phase(last_period_date_str, cycle_length=28, period_length=5):
    last_period = datetime.strptime(last_period_date_str, "%Y-%m-%d")
    today = datetime.today()
    days_since_period = (today - last_period).days
    cycle_day = (days_since_period % cycle_length) + 1
    if cycle_day <= period_length:
        phase = "menstrual"
    elif cycle_day <= 13:
        phase = "follicular"
    elif cycle_day <= 16:
        phase = "ovulatory"
    else:
        phase = "luteal"
    return {
        "cycle_day": cycle_day,
        "phase": phase,
        "cycle_length": cycle_length,
        "period_length": period_length,
        "next_period_in_days": cycle_length - cycle_day + 1
    }

def format_date(dt):
    return dt.strftime("%Y-%m-%d")

def calculate_cycle_status(user_data):
    last_period = user_data.get('last_period_date')
    avg_cycle = user_data.get('average_cycle_length', 28)
    avg_period_length = user_data.get('average_period_length', 5)
    result = calculate_cycle_phase(last_period, avg_cycle, avg_period_length)

    today_str = format_date(datetime.today())
    last_period_dt = datetime.strptime(last_period, "%Y-%m-%d")
    predicted_start_dt = last_period_dt + timedelta(days=avg_cycle)
    predicted_start_str = format_date(predicted_start_dt)
    days_since_predicted = (datetime.today() - predicted_start_dt).days
    not_arrived_dates = user_data.get('period_not_arrived_dates', [])
    period_end_dates = user_data.get('period_end_dates', [])
    period_still_going = user_data.get('period_still_going', False)

    result['predicted_period_start_date'] = predicted_start_str
    result['last_period_date'] = last_period
    result['awaiting_period_confirmation'] = False
    result['period_confirmed'] = False
    result['period_active'] = result['phase'] == 'menstrual'

    if days_since_predicted >= 0 and predicted_start_str > last_period:
        period_confirmed = predicted_start_str in user_data.get('period_dates', [])
        missed_today = today_str in not_arrived_dates
        within_confirmation_window = days_since_predicted <= 14

        if not period_confirmed and within_confirmation_window:
            result['awaiting_period_confirmation'] = True
            result['period_active'] = False
            result['phase'] = 'luteal'
            result['phase_status'] = 'awaiting_period_confirmation'
            result['confirmation_date'] = today_str
            result['confirmation_dismissed_today'] = missed_today
            result['next_period_in_days'] = 0
        elif period_confirmed:
            result['period_confirmed'] = True

    latest_end_after_start = any(end_date >= last_period for end_date in period_end_dates)
    if period_still_going and not result['awaiting_period_confirmation'] and not latest_end_after_start:
        result['period_confirmed'] = True
        result['period_active'] = True
        result['period_still_going'] = True
        result['period_ended'] = False
        result['phase'] = 'menstrual'
    elif result['phase'] == 'menstrual' and not result['awaiting_period_confirmation']:
        result['period_confirmed'] = True
        period_ended = latest_end_after_start
        result['period_active'] = not period_ended
        result['period_still_going'] = period_still_going
        result['period_ended'] = period_ended
        if period_ended:
            result['phase'] = 'follicular'

    return result

def calculate_average_cycle(period_dates):
    if len(period_dates) < 2:
        return 28
    dates = sorted([datetime.strptime(d, "%Y-%m-%d") for d in period_dates])
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    return round(sum(gaps) / len(gaps))

def calculate_average_period_length(period_dates, period_end_dates):
    """Calculate average period length from confirmed start/end pairs."""
    if not period_end_dates or not period_dates:
        return 5  # default
    lengths = []
    for start_str, end_str in zip(sorted(period_dates), sorted(period_end_dates)):
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d")
            end = datetime.strptime(end_str, "%Y-%m-%d")
            length = (end - start).days + 1
            if 1 <= length <= 10:  # sanity check
                lengths.append(length)
        except Exception:
            pass
    return round(sum(lengths) / len(lengths)) if lengths else 5

def normalise_period_history(history):
    cleaned = []
    seen_starts = set()
    for item in history or []:
        if not isinstance(item, dict):
            continue
        start_date = item.get('start_date', '').strip()
        end_date = item.get('end_date', '').strip()
        if not start_date or start_date in seen_starts:
            continue
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None
        except Exception:
            continue
        if end and end < start:
            continue
        if start > datetime.today():
            continue
        cleaned.append({
            "start_date": start_date,
            "end_date": end_date if end else ""
        })
        seen_starts.add(start_date)
    cleaned.sort(key=lambda item: item["start_date"])
    return cleaned

def expand_period_history(period_dates, period_end_dates, period_still_going=False):
    today_date = datetime.today().date()
    starts = sorted(period_dates or [])
    ends = sorted(period_end_dates or [])
    latest_start = max(starts) if starts else None
    menstrual_dates = set()

    for idx, start_str in enumerate(starts):
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
        except Exception:
            continue

        end = start
        if idx < len(ends):
            try:
                parsed_end = datetime.strptime(ends[idx], "%Y-%m-%d").date()
                if parsed_end >= start:
                    end = parsed_end
            except Exception:
                pass
        elif period_still_going and start_str == latest_start:
            end = today_date

        end = min(end, today_date)
        day = start
        while day <= end:
            menstrual_dates.add(format_date(datetime.combine(day, datetime.min.time())))
            day += timedelta(days=1)

    return sorted(menstrual_dates)

def menstrual_dates_to_history(menstrual_dates):
    parsed = []
    today_date = datetime.today().date()
    for date_str in menstrual_dates or []:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if day <= today_date:
            parsed.append(day)

    parsed = sorted(set(parsed))
    if not parsed:
        return [], []

    starts = []
    ends = []
    group_start = parsed[0]
    prev = parsed[0]

    for day in parsed[1:]:
        if (day - prev).days == 1:
            prev = day
            continue
        starts.append(format_date(datetime.combine(group_start, datetime.min.time())))
        ends.append(format_date(datetime.combine(prev, datetime.min.time())))
        group_start = day
        prev = day

    starts.append(format_date(datetime.combine(group_start, datetime.min.time())))
    ends.append(format_date(datetime.combine(prev, datetime.min.time())))
    return starts, ends

def record_period_start(user_id, period_date):
    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    period_dates = user_data.get('period_dates', [])
    not_arrived_dates = user_data.get('period_not_arrived_dates', [])

    if period_date not in period_dates:
        period_dates.append(period_date)
        period_dates.sort()
    if period_date in not_arrived_dates:
        not_arrived_dates.remove(period_date)

    avg_cycle = calculate_average_cycle(period_dates)
    period_end_dates = user_data.get('period_end_dates', [])
    avg_period_length = calculate_average_period_length(period_dates, period_end_dates)

    db.collection('users').document(user_id).set({
        'period_dates': period_dates,
        'period_not_arrived_dates': not_arrived_dates,
        'average_cycle_length': avg_cycle,
        'average_period_length': avg_period_length,
        'last_period_date': max(period_dates),
        'period_still_going': True
    }, merge=True)

    return {
        "message": "Period reported successfully",
        "periods_tracked": len(period_dates),
        "average_cycle_length": avg_cycle,
        "last_period_date": max(period_dates)
    }

# ─── Routes ──────────────────────────────────────────────────

@app.route('/log_checkin', methods=['POST'])
@require_user_auth
def log_checkin():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    user_text = data.get('text', '')
    mood_rating = data.get('mood_rating', 0)
    cycle_phase = data.get('phase', 'unknown')
    cycle_day = data.get('cycle_day', 0)

    mood_map = {1: 'very_low', 2: 'low', 3: 'neutral', 4: 'good', 5: 'great'}
    emotion = mood_map.get(mood_rating, 'neutral')

    user_collection(user_id, 'emotion_logs').add({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "emotion": emotion,
        "mood_rating": mood_rating,
        "user_text": user_text,
        "phase": cycle_phase,
        "cycle_day": cycle_day
    })

    return jsonify({"status": "ok", "emotion": emotion})

@app.route('/log_sleep', methods=['POST'])
@require_user_auth
def log_sleep():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    hours = data.get('hours', 0)
    quality = data.get('quality', 'unknown')
    notes = data.get('notes', '')
    cycle_phase = data.get('phase', 'unknown')
    cycle_day = data.get('cycle_day', 0)
    bedtime = data.get('bedtime', '')
    wake_time = data.get('wake_time', '')

    summary = get_user_summary(user_id)

    recent_sleep = [d.to_dict() for d in user_collection(user_id, 'sleep_logs').order_by('date', direction='DESCENDING').limit(5).stream()]
    phase_sleep = [l for l in recent_sleep if l.get('phase') == cycle_phase]
    phase_avg = round(sum(l.get('hours', 0) for l in phase_sleep) / len(phase_sleep), 1) if phase_sleep else None
    personalised = len(recent_sleep) >= 5

    prompt = f"""
    You are a compassionate women's health assistant.

    The user is in their {cycle_phase} phase (day {cycle_day}).
    They slept {hours} hours and rated their sleep quality as {quality}.
    {f'Additional notes: {notes}' if notes else ''}
    Their historical average sleep is {summary['sleep_patterns']['average_hours']} hours.
    {f'Their average sleep during {cycle_phase} phase is {phase_avg} hours.' if phase_avg else ''}

    Based on their cycle phase, sleep data, and history:
    1. Explain in 1-2 sentences why their sleep may be affected hormonally right now
    2. Give one specific actionable tip to improve their sleep tonight

    Respond in JSON only, no other text:
    {{
        "insight": "hormonal explanation here",
        "tip": "actionable tip here"
    }}
    """

    try:
        content = chat_provider.generate(
            [{"role": "user", "content": prompt}],
            model=config.sleep_model,
            max_tokens=300
        )
        result = json.loads(content)
    except Exception:
        result = {
            "insight": "Your sleep is important during your " + cycle_phase + " phase.",
            "tip": "Try to keep a consistent bedtime tonight."
        }

    result['personalised'] = personalised

    today_str = datetime.now().strftime("%Y-%m-%d")
    existing = list(
        user_collection(user_id, 'sleep_logs')
        .where('date', '>=', today_str)
        .where('date', '<', today_str + 'z')
        .limit(1)
        .stream()
    )

    log_data = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "hours": hours,
        "quality": quality,
        "notes": notes,
        "phase": cycle_phase,
        "cycle_day": cycle_day,
        "bedtime": bedtime,
        "wake_time": wake_time
    }

    if existing:
        existing[0].reference.update(log_data)
    else:
        user_collection(user_id, 'sleep_logs').add(log_data)

    update_user_summary(user_id)
    return jsonify(result)

@app.route('/log_diet', methods=['POST'])
@require_user_auth
def log_diet():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    cravings = data.get('cravings', [])
    craving = data.get('craving', '')
    if not cravings and craving:
        cravings = [craving]
    craving_str = ', '.join(cravings) if cravings else craving
    ate = data.get('ate', '')
    body_feel = data.get('body_feel', '')
    cycle_phase = data.get('phase', 'unknown')
    cycle_day = data.get('cycle_day', 0)

    summary = get_user_summary(user_id)

    recent_diet = [d.to_dict() for d in user_collection(user_id, 'diet_logs').order_by('date', direction='DESCENDING').limit(5).stream()]
    personalised = len(recent_diet) >= 5
    phase_cravings = [l.get('craving', '') for l in recent_diet if l.get('phase') == cycle_phase and l.get('craving')]

    prompt = f"""
    You are a compassionate women's health assistant.

    The user is in their {cycle_phase} phase (day {cycle_day}).
    They are craving: "{craving_str}"
    They ate: "{ate}"
    They feel: "{body_feel}"
    Their most common historical craving is: {summary['diet_patterns']['most_common_craving']}
    {f'Common cravings in their {cycle_phase} phase: {", ".join(phase_cravings[:3])}' if phase_cravings else ''}

    Based on their cycle phase, dietary input, and history:
    1. Explain in 1-2 sentences why they might be experiencing these cravings hormonally
    2. Give one specific food swap or mindful eating tip for this phase

    Respond in JSON only, no other text:
    {{
        "insight": "hormonal explanation here",
        "tip": "actionable tip here"
    }}
    """

    try:
        content = chat_provider.generate(
            [{"role": "user", "content": prompt}],
            model=config.diet_model,
            max_tokens=300
        )
        result = json.loads(content)
    except Exception:
        result = {
            "insight": "Cravings during your " + cycle_phase + " phase are completely normal and driven by hormones.",
            "tip": "Try pairing your craving with something nutrient-dense."
        }

    result['personalised'] = personalised

    user_collection(user_id, 'diet_logs').add({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "craving": craving_str,
        "cravings": cravings,
        "ate": ate,
        "body_feel": body_feel,
        "phase": cycle_phase,
        "cycle_day": cycle_day
    })

    update_user_summary(user_id)
    return jsonify(result)

@app.route('/report_period', methods=['POST'])
@require_user_auth
def report_period():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    period_date = data.get('period_date')
    # arrived: True = period confirmed arrived, False = predicted but not yet arrived
    arrived = data.get('arrived', True)

    if not period_date:
        return jsonify({"error": "period_date is required"}), 400

    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    not_arrived_dates = user_data.get('period_not_arrived_dates', [])

    if not arrived:
        # User says period has NOT arrived yet despite prediction
        if period_date not in not_arrived_dates:
            not_arrived_dates.append(period_date)
        db.collection('users').document(user_id).set({
            'period_not_arrived_dates': not_arrived_dates
        }, merge=True)
        return jsonify({"message": "Noted — Kira will adjust future predictions"})

    return jsonify(record_period_start(user_id, period_date))

@app.route('/confirm_period_arrived', methods=['POST'])
@require_user_auth
def confirm_period_arrived():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    date = data.get('date') or data.get('period_date') or datetime.now().strftime("%Y-%m-%d")
    arrived = data.get('arrived', True)

    if arrived:
        return jsonify(record_period_start(user_id, date))

    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    not_arrived_dates = user_data.get('period_not_arrived_dates', [])
    if date not in not_arrived_dates:
        not_arrived_dates.append(date)
        not_arrived_dates.sort()

    db.collection('users').document(user_id).set({
        'period_not_arrived_dates': not_arrived_dates
    }, merge=True)

    return jsonify({"status": "ok", "message": "Noted — Kira will wait for confirmation"})

@app.route('/cycle_status', methods=['GET'])
@require_user_auth
def cycle_status():
    user_id = request.args.get('user_id', 'user_1')
    user_doc = db.collection('users').document(user_id).get()
    if not user_doc.exists:
        return jsonify({"error": "No period data recorded yet"}), 400

    user_data = user_doc.to_dict()
    last_period = user_data.get('last_period_date')
    avg_cycle = user_data.get('average_cycle_length', 28)
    avg_period_length = user_data.get('average_period_length', 5)

    if not last_period:
        return jsonify({"error": "No period data recorded yet"}), 400

    result = calculate_cycle_status(user_data)
    result['periods_tracked'] = len(user_data.get('period_dates', []))
    return jsonify(result)

@app.route('/report_period_end', methods=['POST'])
@require_user_auth
def report_period_end():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    end_date = data.get('end_date')
    # still_going: True = user says period has NOT ended yet despite prediction
    still_going = data.get('still_going', False)

    if not end_date:
        return jsonify({"error": "end_date is required"}), 400

    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    if still_going:
        db.collection('users').document(user_id).set({
            'period_still_going': True
        }, merge=True)
        return jsonify({"message": "Noted — Kira will adjust future predictions"})

    period_end_dates = user_data.get('period_end_dates', [])
    if end_date not in period_end_dates:
        period_end_dates.append(end_date)
        period_end_dates.sort()

    period_dates = user_data.get('period_dates', [])
    avg_period_length = calculate_average_period_length(period_dates, period_end_dates)

    db.collection('users').document(user_id).set({
        'period_end_dates': period_end_dates,
        'average_period_length': avg_period_length,
        'period_still_going': False
    }, merge=True)

    return jsonify({
        "message": "Period end logged — predictions updated",
        "average_period_length": avg_period_length
    })

@app.route('/period_history', methods=['GET'])
@require_user_auth
def period_history():
    user_id = request.args.get('user_id', 'user_1')
    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    period_dates = sorted(user_data.get('period_dates', []))
    period_end_dates = sorted(user_data.get('period_end_dates', []))
    history = []
    for idx, start_date in enumerate(period_dates):
        history.append({
            "start_date": start_date,
            "end_date": period_end_dates[idx] if idx < len(period_end_dates) else ""
        })

    return jsonify({
        "history": history,
        "menstrual_dates": expand_period_history(
            period_dates,
            period_end_dates,
            user_data.get('period_still_going', False)
        ),
        "average_cycle_length": user_data.get('average_cycle_length', 28),
        "average_period_length": user_data.get('average_period_length', 5),
        "last_period_date": user_data.get('last_period_date', max(period_dates) if period_dates else "")
    })

@app.route('/period_day', methods=['POST'])
@require_user_auth
def update_period_day():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    date_str = data.get('date')
    menstrual = bool(data.get('menstrual', True))

    if not date_str:
        return jsonify({"error": "date is required"}), 400

    try:
        selected = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    if selected >= datetime.today().date():
        return jsonify({"error": "Only past dates can be edited from the calendar"}), 400

    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    menstrual_dates = set(expand_period_history(
        user_data.get('period_dates', []),
        user_data.get('period_end_dates', []),
        user_data.get('period_still_going', False)
    ))

    if menstrual:
        menstrual_dates.add(date_str)
    else:
        menstrual_dates.discard(date_str)

    period_dates, period_end_dates = menstrual_dates_to_history(menstrual_dates)
    today_str = datetime.today().strftime("%Y-%m-%d")
    period_still_going = user_data.get('period_still_going', False)
    if period_still_going and period_dates and period_end_dates:
        latest_start = max(period_dates)
        latest_end = period_end_dates[-1]
        if latest_start <= today_str and latest_end == today_str:
            period_end_dates = period_end_dates[:-1]

    avg_cycle = calculate_average_cycle(period_dates)
    avg_period_length = calculate_average_period_length(period_dates, period_end_dates)

    payload = {
        'period_dates': period_dates,
        'period_end_dates': period_end_dates,
        'average_cycle_length': avg_cycle,
        'average_period_length': avg_period_length,
        'period_history_updated_at': datetime.now().strftime("%Y-%m-%d %H:%M"),
        'period_still_going': period_still_going
    }
    if period_dates:
        payload['last_period_date'] = max(period_dates)
    else:
        payload['last_period_date'] = ''
        payload['period_still_going'] = False

    user_ref.set(payload, merge=True)

    return jsonify({
        "status": "ok",
        "menstrual_dates": sorted(menstrual_dates),
        "history": [
            {"start_date": start, "end_date": period_end_dates[idx] if idx < len(period_end_dates) else ""}
            for idx, start in enumerate(period_dates)
        ],
        "periods_tracked": len(period_dates),
        "average_cycle_length": avg_cycle,
        "average_period_length": avg_period_length,
        "last_period_date": max(period_dates) if period_dates else user_data.get('last_period_date', '')
    })

@app.route('/period_history', methods=['POST'])
@require_user_auth
def save_period_history():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    history = normalise_period_history(data.get('history', []))

    if not history:
        return jsonify({"error": "Add at least one valid period start date"}), 400

    period_dates = [item['start_date'] for item in history]
    period_end_dates = [item['end_date'] for item in history if item.get('end_date')]
    avg_cycle = calculate_average_cycle(period_dates)
    avg_period_length = calculate_average_period_length(period_dates, period_end_dates)
    last_period_date = max(period_dates)

    db.collection('users').document(user_id).set({
        'period_dates': period_dates,
        'period_end_dates': period_end_dates,
        'average_cycle_length': avg_cycle,
        'average_period_length': avg_period_length,
        'last_period_date': last_period_date,
        'period_history_updated_at': datetime.now().strftime("%Y-%m-%d %H:%M")
    }, merge=True)

    return jsonify({
        "status": "ok",
        "history": history,
        "periods_tracked": len(period_dates),
        "average_cycle_length": avg_cycle,
        "average_period_length": avg_period_length,
        "last_period_date": last_period_date
    })

@app.route('/rate_intervention', methods=['POST'])
@require_user_auth
def rate_intervention():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')
    intervention_id = data.get('intervention_id', '')
    intervention_name = data.get('intervention_name', data.get('intervention', ''))
    intervention_type = data.get('intervention_type', '')
    rating = data.get('rating', 0)
    phase = data.get('phase', 'unknown')
    cycle_day = data.get('cycle_day', 0)
    duration_seconds = data.get('duration_seconds', 0)

    user_collection(user_id, 'intervention_logs').add({
        'date': datetime.now().strftime("%Y-%m-%d %H:%M"),
        'intervention_id': intervention_id,
        'intervention_name': intervention_name,
        'intervention_type': intervention_type,
        'rating': rating,
        'phase': phase,
        'cycle_day': cycle_day,
        'duration_seconds': duration_seconds
    })

    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    if rating >= 4:
        effective = user_data.get('effective_interventions', [])
        if intervention_name not in effective:
            effective.append(intervention_name)
        db.collection('users').document(user_id).set({
            'effective_interventions': effective
        }, merge=True)

    if rating <= 2:
        ineffective = user_data.get('ineffective_interventions', [])
        if intervention_name not in ineffective:
            ineffective.append(intervention_name)
        db.collection('users').document(user_id).set({
            'ineffective_interventions': ineffective
        }, merge=True)

    return jsonify({"status": "ok", "message": "Thanks for rating — Kira will remember this"})

@app.route('/', methods=['GET'])
def root():
    return jsonify({"status": "running", "service": "kira-backend"})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "running"})

def get_cycle_context_for_chat(user_id, fallback_phase='unknown', fallback_day=0):
    user_doc = db.collection('users').document(user_id).get()
    if not user_doc.exists:
        return {
            "estimatedPhase": fallback_phase,
            "estimatedCycleDay": fallback_day,
            "phaseConfidence": "low",
            "daysUntilExpectedPeriod": None,
            "basedOnLoggedCycles": 0,
            "note": "Cycle context came from the client fallback because no user cycle document was available."
        }
    user_data = user_doc.to_dict()
    last_period = user_data.get('last_period_date')
    if not last_period:
        return {
            "estimatedPhase": fallback_phase,
            "estimatedCycleDay": fallback_day,
            "phaseConfidence": "low",
            "daysUntilExpectedPeriod": None,
            "basedOnLoggedCycles": len(user_data.get('period_dates', [])),
            "note": "Cycle phase is uncertain because no period date has been logged."
        }
    cycle = calculate_cycle_status(user_data)
    cycles_tracked = len(user_data.get('period_dates', []))
    return {
        "estimatedPhase": cycle.get('phase'),
        "estimatedCycleDay": cycle.get('cycle_day'),
        "phaseConfidence": "high" if cycles_tracked >= 3 else "medium" if cycles_tracked else "low",
        "daysUntilExpectedPeriod": cycle.get('next_period_in_days'),
        "basedOnLoggedCycles": cycles_tracked,
        "awaitingPeriodConfirmation": cycle.get('awaiting_period_confirmation', False),
        "note": "Estimated from logged dates; not confirmed ovulation or measured hormone levels."
    }

def parse_ai_response(full_response):
    detected_emotion = "unknown"
    ai_message = full_response or ""
    if "EMOTION:" in ai_message:
        parts = ai_message.split("EMOTION:", 1)
        ai_message = parts[0].strip()
        detected_emotion = parts[1].strip().lower().split()[0] if parts[1].strip() else "unknown"

    intervention_trigger = None
    clean_message = ai_message
    tag_map = {
        '[BREATHING]': 'BREATHING',
        '[URGE_SURF]': 'URGE_SURF',
        '[HAPTIC]': 'HAPTIC',
        '[GROUNDING]': 'GROUNDING',
        '[JOURNAL]': 'JOURNAL',
    }
    for tag, trigger in tag_map.items():
        if tag in clean_message:
            intervention_trigger = trigger
            clean_message = clean_message.replace(tag, '').strip()
    return clean_message, detected_emotion, intervention_trigger

@app.route('/chat', methods=['POST'])
@app.route('/api/v1/chat', methods=['POST'])
@require_user_auth
def chat():
    data = request.get_json(force=True)

    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}
    elif isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, dict):
        data = {}

    user_id = data.get('user_id', 'user_1')
    user_message = data.get('message', '')
    cycle_phase = data.get('phase', 'unknown')
    cycle_day = data.get('cycle_day', 0)
    session_id = data.get('session_id', DEFAULT_SESSION_ID)

    safety = evaluate_safety(user_message) if config.enable_safety_router else {
        "level": "none",
        "reasons": [],
        "suppressCycleExplanation": False
    }
    retrieval_decision = classify_retrieval(user_message)
    cycle_context = get_cycle_context_for_chat(user_id, cycle_phase, cycle_day)
    session = load_session(db, user_id, session_id) if config.enable_session_cache else {}
    recent_messages = get_recent_messages(db, user_id)
    summary = get_user_summary(user_id)
    personal_context = get_relevant_user_context(db, user_id, retrieval_decision)

    cached_used = []
    new_facts = []
    scientific_facts = []
    provider_failed = False
    if config.enable_rag and retrieval_decision.get("needsScientificRetrieval") and safety.get("level") != "urgent":
        try:
            cached_facts = knowledge_repo.resolve_facts(session.get("retrievedFactIds", []))
            cached_used = knowledge_repo.select_relevant_cached_facts(user_message, cached_facts)
            if knowledge_repo.needs_additional_retrieval(user_message, cached_used, retrieval_decision.get("topics", [])):
                new_facts = knowledge_repo.retrieve(
                    user_message,
                    topics=retrieval_decision.get("topics", []),
                    limit=5,
                    include_safety=safety.get("level") != "none",
                )
                add_fact_ids(
                    db,
                    user_id,
                    session_id,
                    [fact.get("fact_id") for fact in new_facts],
                    retrieval_decision.get("topics", []),
                )
            scientific_facts = []
            for fact in cached_used + new_facts:
                if fact.get("fact_id") not in [item.get("fact_id") for item in scientific_facts]:
                    scientific_facts.append(fact)
            scientific_facts = scientific_facts[:5]
        except Exception as exc:
            print("Kira retrieval failed:", type(exc).__name__)
            scientific_facts = []

    if safety.get("level") == "urgent":
        clean_message = urgent_response()
        detected_emotion = "unknown"
        intervention_trigger = None
    else:
        messages = build_context(
            message=user_message,
            safety=safety,
            retrieval_decision=retrieval_decision,
            recent_messages=recent_messages,
            session_summary=session.get("summary", ""),
            cycle_context=cycle_context,
            personal_context=personal_context,
            scientific_facts=scientific_facts,
            user_summary=summary,
        )

        try:
            content = chat_provider.generate(
                messages,
                model=config.chat_model,
                max_tokens=300
            )
            clean_message, detected_emotion, intervention_trigger = parse_ai_response(content)
        except Exception as exc:
            print(
                "Kira chat provider failed:",
                type(exc).__name__,
                f"provider={config.ai_provider}",
                f"model={config.chat_model}",
            )
            provider_failed = True
            clean_message = "Sorry, I'm having a little trouble connecting right now. Try again in a moment."
            detected_emotion = "unknown"
            intervention_trigger = None

    user_collection(user_id, 'chat_history').add({
        'role': 'user',
        'content': user_message,
        'timestamp': firestore.SERVER_TIMESTAMP
    })

    user_collection(user_id, 'chat_history').add({
        'role': 'assistant',
        'content': clean_message,
        'timestamp': firestore.SERVER_TIMESTAMP
    })

    valid_emotions = ['anxiety', 'rumination', 'irritability', 'craving', 'low_motivation', 'shame', 'social_sensitivity', 'positive']
    if detected_emotion in valid_emotions:
        user_collection(user_id, 'emotion_logs').add({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "emotion": detected_emotion,
            "user_text": user_message,
            "phase": cycle_phase,
            "cycle_day": cycle_day
        })

    update_user_summary(user_id)
    if config.enable_session_cache:
        update_summary_if_needed(db, user_id, session_id, get_recent_messages(db, user_id))

    payload = {
        "success": not provider_failed,
        "message": clean_message,
        "emotion": detected_emotion,
        "intervention_trigger": intervention_trigger
    }
    if provider_failed:
        payload["error"] = {
            "code": "AI_PROVIDER_UNAVAILABLE",
            "userMessage": clean_message
        }
    if config.debug_chat:
        payload["debug"] = {
            "safetyLevel": safety.get("level"),
            "intent": retrieval_decision.get("intent"),
            "retrievalNeeded": retrieval_decision.get("needsScientificRetrieval"),
            "topics": retrieval_decision.get("topics"),
            "cachedFactIdsUsed": [fact.get("fact_id") for fact in cached_used],
            "newFactIdsRetrieved": [fact.get("fact_id") for fact in new_facts],
            "cycleContextIncluded": bool(cycle_context),
            "personalPatternIncluded": bool(personal_context),
            "promptVersion": config.prompt_version,
            "ragVersion": config.rag_version,
        }
    return jsonify(payload)

@app.route('/clear_chat', methods=['POST'])
@require_user_auth
def clear_chat():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}
    user_id = data.get('user_id', 'user_1')
    docs = user_collection(user_id, 'chat_history').stream()
    for doc in docs:
        doc.reference.delete()
    return jsonify({"message": "Chat cleared"})

# ─── Authentication ─────────────────────────────────────────

def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return salt, hashed

@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    username = data.get('username', '').strip().lower()
    password = data.get('password', '')

    if not username or len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if len(username) > 20:
        return jsonify({"error": "Username must be 20 characters or fewer"}), 400
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return jsonify({"error": "Username can only contain letters, numbers, and underscores"}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    existing = db.collection('accounts').document(username).get()
    if existing.exists:
        return jsonify({"error": "Username already taken"}), 409

    salt, hashed = hash_password(password)
    db.collection('accounts').document(username).set({
        'username': username,
        'password_hash': hashed,
        'salt': salt,
        'created_at': datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    db.collection('users').document(username).set({
        'username': username,
        'onboarding_complete': False
    })

    auth_token = issue_auth_token(username)
    return jsonify({
        "status": "ok",
        "username": username,
        "user_id": username,
        "auth_token": auth_token
    })

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    username = data.get('username', '').strip().lower()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    account = db.collection('accounts').document(username).get()
    if not account.exists:
        return jsonify({"error": "Invalid username or password"}), 401

    account_data = account.to_dict()
    salt = account_data.get('salt', '')
    stored_hash = account_data.get('password_hash', '')

    _, check_hash = hash_password(password, salt)
    if check_hash != stored_hash:
        return jsonify({"error": "Invalid username or password"}), 401

    user_doc = db.collection('users').document(username).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    onboarding_complete = user_data.get('onboarding_complete', False)

    auth_token = issue_auth_token(username)
    return jsonify({
        "status": "ok",
        "username": username,
        "user_id": username,
        "auth_token": auth_token,
        "onboarding_complete": onboarding_complete
    })

@app.route('/delete_account', methods=['POST'])
@require_user_auth
def delete_account():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', '').strip().lower()
    if not user_id or user_id == 'user_1':
        return make_api_error("INVALID_USER", "Please log in again before deleting your account.", 400)

    user_ref = db.collection('users').document(user_id)
    deleted_documents = 0
    for collection_ref in user_data_collections(user_ref):
        deleted_documents += delete_collection_documents(collection_ref)

    user_ref.delete()
    db.collection('accounts').document(user_id).delete()

    return jsonify({
        "status": "ok",
        "message": "Account deleted",
        "deleted_documents": deleted_documents
    })

@app.route('/save_onboarding', methods=['POST'])
@require_user_auth
def save_onboarding():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', '').strip()
    name = data.get('name', '')
    age = data.get('age', 0)
    last_period_date = data.get('last_period_date', '')
    cycle_length = data.get('cycle_length', 28)
    current_moods = data.get('current_moods', [])

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    db.collection('users').document(user_id).set({
        'name': name,
        'age': age,
        'last_period_date': last_period_date,
        'average_cycle_length': cycle_length,
        'period_dates': [last_period_date] if last_period_date else [],
        'current_moods': current_moods,
        'onboarding_complete': True
    }, merge=True)

    return jsonify({"status": "ok", "name": name, "user_id": user_id})

# ─── Intervention Library ───────────────────────────────────

INTERVENTION_LIBRARY = {
    "breathing": [
        {"id": "box_breathing", "name": "Box Breathing", "description": "4-4-4-4 breath pattern to calm your nervous system", "duration": "4 min", "best_for": ["anxiety", "stress", "overwhelmed"], "icon": "\U0001f32c\ufe0f"}
    ],
    "urge_surfing": [
        {"id": "urge_surf", "name": "Urge Surfing", "description": "Ride out cravings, angry-text impulses, outbursts, or checking urges without acting immediately", "duration": "5–15 min", "best_for": ["craving", "irritability", "anger", "impulses", "rumination"], "icon": "\U0001f30a"}
    ],
    "haptic": [
        {"id": "haptic_reset", "name": "Haptic Reset", "description": "Rhythmic vibration patterns to calm or energise your nervous system", "duration": "2 min", "best_for": ["anxiety", "stress", "low_motivation"], "icon": "\U0001f4f3"}
    ],
    "grounding": [
        {"id": "54321", "name": "5-4-3-2-1 Grounding", "description": "Use your senses to anchor yourself to the present moment", "duration": "3 min", "best_for": ["anxiety", "rumination", "overwhelmed"], "icon": "\U0001f33f"}
    ],
    "journaling": [
        {"id": "phase_reflect", "name": "Phase Reflection", "description": "Guided journaling prompts tailored to your current cycle phase", "duration": "5 min", "best_for": ["low_motivation", "rumination", "social_sensitivity"], "icon": "\U0001f4d3"}
    ]
}

@app.route('/intervention_library', methods=['GET'])
@require_user_auth
def intervention_library():
    user_id = request.args.get('user_id', 'user_1')
    logs = [d.to_dict() for d in user_collection(user_id, 'intervention_logs').stream()]
    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    effective = user_data.get('effective_interventions', [])

    from collections import defaultdict
    ratings_by_id = defaultdict(list)
    for log in logs:
        iid = log.get('intervention_id', '')
        rating = log.get('rating', 0)
        if iid and rating:
            ratings_by_id[iid].append(rating)

    avg_ratings = {k: round(sum(v) / len(v), 1) for k, v in ratings_by_id.items()}

    library = {}
    for category, items in INTERVENTION_LIBRARY.items():
        enriched = []
        for item in items:
            entry = dict(item)
            entry['user_rating'] = avg_ratings.get(item['id'])
            entry['times_used'] = len(ratings_by_id.get(item['id'], []))
            entry['effective'] = item['name'] in effective
            enriched.append(entry)
        library[category] = enriched

    return jsonify(library)

@app.route('/personalised_intervention', methods=['GET'])
@require_user_auth
def personalised_intervention():
    user_id = request.args.get('user_id', 'user_1')
    emotion = request.args.get('emotion', '')
    phase = request.args.get('phase', '')

    logs = [d.to_dict() for d in user_collection(user_id, 'intervention_logs').stream()]

    from collections import defaultdict
    good_for_emotion = defaultdict(list)
    for log in logs:
        rating = log.get('rating', 0)
        iid = log.get('intervention_id', '')
        if rating >= 4 and iid:
            good_for_emotion[iid].append(rating)

    personalised = []
    all_interventions = {}
    for category, items in INTERVENTION_LIBRARY.items():
        for item in items:
            all_interventions[item['id']] = {**item, 'category': category}

    for iid, ratings in good_for_emotion.items():
        if iid in all_interventions:
            info = all_interventions[iid]
            if emotion in info.get('best_for', []):
                personalised.append({
                    **info,
                    'avg_rating': round(sum(ratings) / len(ratings), 1),
                    'reason': "Based on what's helped you before"
                })

    personalised.sort(key=lambda x: x['avg_rating'], reverse=True)

    if len(personalised) < 3:
        for iid, info in all_interventions.items():
            if emotion in info.get('best_for', []) and iid not in [p['id'] for p in personalised]:
                personalised.append({
                    **info,
                    'avg_rating': None,
                    'reason': "Popular for this phase"
                })
            if len(personalised) >= 3:
                break

    return jsonify({"recommendations": personalised[:3]})

@app.route('/home_data', methods=['GET'])
@require_user_auth
def home_data():
    user_id = request.args.get('user_id', 'user_1')
    user_doc = db.collection('users').document(user_id).get()
    if not user_doc.exists:
        return jsonify({"error": "No user data"}), 400

    user_data = user_doc.to_dict()
    last_period = user_data.get('last_period_date')
    avg_cycle = user_data.get('average_cycle_length', 28)
    avg_period_length = user_data.get('average_period_length', 5)

    if not last_period:
        return jsonify({"error": "No period data"}), 400

    cycle = calculate_cycle_status(user_data)
    phase = cycle['phase']

    default_tendencies = {
        "menstrual": ["Low energy", "Introspective", "Rest needed", "High sensitivity"],
        "follicular": ["Rising energy", "Creative clarity", "Motivated", "Social openness"],
        "ovulatory": ["Peak confidence", "High energy", "Communicative", "Assertive"],
        "luteal": ["Higher sensitivity", "Craving intensity", "Lower energy", "Introspective"]
    }

    result = {**cycle, 'personalised': False, 'name': user_data.get('name', '')}

    emotion_logs = [d.to_dict() for d in user_collection(user_id, 'emotion_logs').stream()]
    if len(emotion_logs) >= 14:
        phase_emotions = [l['emotion'] for l in emotion_logs if l.get('phase') == phase]
        if phase_emotions:
            from collections import Counter
            top_emotions = [e for e, _ in Counter(phase_emotions).most_common(3)]
            result['tendencies'] = top_emotions
            result['tendencies_note'] = "Based on your patterns"
            result['personalised'] = True
        else:
            result['tendencies'] = default_tendencies.get(phase, [])
            result['tendencies_note'] = "Typical for this phase"
    else:
        result['tendencies'] = default_tendencies.get(phase, [])
        result['tendencies_note'] = "Typical for this phase"

    sleep_logs = [d.to_dict() for d in user_collection(user_id, 'sleep_logs').stream()]
    phase_sleep = [l for l in sleep_logs if l.get('phase') == phase]
    if phase_sleep:
        avg_sleep = round(sum(l.get('hours', 0) for l in phase_sleep) / len(phase_sleep), 1)
        result['sleep'] = {'average_hours': avg_sleep, 'personalised': True}
    else:
        phase_averages = {"menstrual": 6.5, "follicular": 7.2, "ovulatory": 7.0, "luteal": 6.8}
        result['sleep'] = {'average_hours': phase_averages.get(phase, 7.0), 'personalised': False}

    diet_logs = [d.to_dict() for d in user_collection(user_id, 'diet_logs').stream()]
    phase_diet = [l for l in diet_logs if l.get('phase') == phase]
    if phase_diet:
        cravings = [l.get('craving', '') for l in phase_diet if l.get('craving')]
        if cravings:
            from collections import Counter
            top_cravings = [c for c, _ in Counter(cravings).most_common(3)]
            result['diet'] = {'top_cravings': top_cravings, 'personalised': True}
        else:
            result['diet'] = {'top_cravings': [], 'personalised': False}
    else:
        result['diet'] = {'top_cravings': [], 'personalised': False}

    return jsonify(result)

@app.route('/save_journal', methods=['POST'])
@require_user_auth
def save_journal():
    data = request.get_json(force=True)
    if isinstance(data, list):
        data = {item[0]: item[1] for item in data}

    user_id = data.get('user_id', 'user_1')

    user_collection(user_id, 'journal_logs').add({
        'date': datetime.now().strftime("%Y-%m-%d %H:%M"),
        'prompt': data.get('prompt', ''),
        'entry': data.get('entry', ''),
        'responses': data.get('responses', []),
        'phase': data.get('phase', 'unknown'),
        'cycle_day': data.get('cycle_day', 0)
    })

    return jsonify({"status": "ok", "message": "Journal entry saved"})

@app.route('/recent_logs', methods=['GET'])
@require_user_auth
def recent_logs():
    user_id = request.args.get('user_id', 'user_1')
    log_type = request.args.get('type', 'sleep')
    limit_count = int(request.args.get('limit', 3))

    collection_map = {
        'sleep': 'sleep_logs',
        'diet': 'diet_logs',
        'emotion': 'emotion_logs',
        'journal': 'journal_logs',
        'intervention': 'intervention_logs'
    }

    coll_name = collection_map.get(log_type)
    if not coll_name:
        return jsonify({"error": "Invalid log type"}), 400

    try:
        docs = user_collection(user_id, coll_name).order_by('date', direction='DESCENDING').limit(limit_count).stream()
        logs = [d.to_dict() for d in docs]
    except Exception:
        docs = user_collection(user_id, coll_name).limit(limit_count).stream()
        logs = sorted([d.to_dict() for d in docs], key=lambda x: x.get('date', ''), reverse=True)

    return jsonify({"logs": logs})

@app.route('/insights', methods=['GET'])
@require_user_auth
def insights():
    user_id = request.args.get('user_id', 'user_1')

    result = {}

    emotion_logs = [d.to_dict() for d in user_collection(user_id, 'emotion_logs').stream()]
    result['emotion_history'] = sorted(emotion_logs, key=lambda x: x.get('date', ''), reverse=True)[:90]

    if emotion_logs:
        from collections import Counter
        emotions = [l.get('emotion', '') for l in emotion_logs if l.get('emotion') and l['emotion'] != 'unknown']
        counts = Counter(emotions)
        total = sum(counts.values())
        result['emotion_breakdown'] = [
            {'emotion': e, 'count': c, 'percent': round(c / total * 100)}
            for e, c in counts.most_common(6)
        ]
    else:
        result['emotion_breakdown'] = []

    mood_logs = [l for l in emotion_logs if l.get('mood_rating')]
    result['mood_trend'] = sorted(mood_logs, key=lambda x: x.get('date', ''), reverse=True)[:7]

    sleep_logs = [d.to_dict() for d in user_collection(user_id, 'sleep_logs').stream()]
    result['sleep_history'] = sorted(sleep_logs, key=lambda x: x.get('date', ''), reverse=True)[:30]

    diet_logs = [d.to_dict() for d in user_collection(user_id, 'diet_logs').stream()]
    result['diet_history'] = sorted(diet_logs, key=lambda x: x.get('date', ''), reverse=True)[:30]

    journal_logs = [d.to_dict() for d in user_collection(user_id, 'journal_logs').stream()]
    result['journal_entries'] = sorted(journal_logs, key=lambda x: x.get('date', ''), reverse=True)[:100]

    intervention_logs = [d.to_dict() for d in user_collection(user_id, 'intervention_logs').stream()]
    if intervention_logs:
        from collections import defaultdict
        stats = defaultdict(lambda: {'ratings': [], 'count': 0})
        for log in intervention_logs:
            name = log.get('intervention_name', '')
            if name:
                stats[name]['ratings'].append(log.get('rating', 0))
                stats[name]['count'] += 1
        result['intervention_stats'] = [
            {'name': k, 'avg_rating': round(sum(v['ratings']) / len(v['ratings']), 1), 'times_used': v['count']}
            for k, v in stats.items()
        ]
        result['intervention_stats'].sort(key=lambda x: x['avg_rating'], reverse=True)
    else:
        result['intervention_stats'] = []

    return jsonify(result)

@app.route('/notification_check', methods=['GET'])
@require_user_auth
def notification_check():
    user_id = request.args.get('user_id', 'user_1')
    hour = datetime.now().hour

    user_doc = db.collection('users').document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    notifications = []

    if 7 <= hour <= 9:
        notifications.append({
            "type": "morning",
            "title": "Good morning ✦",
            "message": "How are you feeling today? Take a moment to check in.",
            "action": "navigate:home"
        })

    if 21 <= hour <= 22:
        notifications.append({
            "type": "sleep",
            "title": "Wind down 🌙",
            "message": "Ready to log your sleep? Your body will thank you.",
            "action": "navigate:sleep"
        })

    if 12 <= hour <= 13:
        notifications.append({
            "type": "water",
            "title": "Stay hydrated 💧",
            "message": "Have you had enough water today? Check your intake.",
            "action": "navigate:diet"
        })

    last_period = user_data.get('last_period_date')
    avg_cycle = user_data.get('average_cycle_length', 28)
    avg_period_length = user_data.get('average_period_length', 5)
    if last_period:
        try:
            cycle_info = calculate_cycle_phase(last_period, avg_cycle, avg_period_length)
            if cycle_info['next_period_in_days'] <= 2:
                notifications.append({
                    "type": "period",
                    "title": "Period approaching",
                    "message": "Your period is predicted in " + str(cycle_info['next_period_in_days']) + " day(s). Be gentle with yourself.",
                    "action": "navigate:cycle"
                })
        except Exception:
            pass

    return jsonify({"notifications": notifications})

if __name__ == '__main__':
    app.run(host='127.0.0.1', debug=False, port=5000)
