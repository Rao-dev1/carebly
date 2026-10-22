from flask import Flask, render_template, request, redirect, url_for, session, g, flash, abort, current_app
from datetime import datetime, timedelta
from functools import wraps
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

import logging
import sqlite3
import os

from config import Config, BASE_DIR
from whatsapp import WhatsAppClient, normalize_phone, verify_signature, parse_webhook, classify_reply

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("carebly")

app = Flask(__name__)
app.config.from_object(Config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
csrf = CSRFProtect(app)

# Appointment slot length in minutes.
SLOT_MINUTES = 30


@app.context_processor
def inject_clinic():
    return {"clinic": {
        "name": app.config["CLINIC_NAME"],
        "tagline": app.config["CLINIC_TAGLINE"],
        "address": app.config["CLINIC_ADDRESS"],
        "phone": app.config["CLINIC_PHONE"],
        "email": app.config["CLINIC_EMAIL"],
        "hours": app.config["CLINIC_HOURS"],
        "map_embed_url": app.config["CLINIC_MAP_EMBED_URL"],
        "whatsapp": app.config["CLINIC_WHATSAPP"],
    }}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# Columns added after the first release; added to older databases on startup.
MIGRATION_COLUMNS = {
    "users": {
        "patient_code": "TEXT",
        "phone": "TEXT",
        "specialty": "TEXT",
        "whatsapp_opt_in": "INTEGER NOT NULL DEFAULT 1",
    },
    "appointments": {"reminder_sent": "INTEGER DEFAULT 0"},
}

def init_db(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        for table, columns in MIGRATION_COLUMNS.items():
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # new database, schema.sql creates it
            for col, ddl in columns.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        with open(os.path.join(BASE_DIR, "schema.sql")) as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()

@app.cli.command("init-db")
def init_db_command():
    """Create or upgrade the database tables."""
    init_db(app.config["DATABASE"])
    print(f"Database ready at {app.config['DATABASE']}")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_password(password):
    return generate_password_hash(password)

def password_matches(stored, given):
    """Check a password. Accepts legacy plain-text rows so old data still works."""
    if stored.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored, given)
    return stored == given

def login_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session or session.get("role") != role:
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# WhatsApp notifications
# ---------------------------------------------------------------------------

def get_whatsapp():
    c = current_app.config
    return WhatsAppClient(
        c["WHATSAPP_TOKEN"], c["WHATSAPP_PHONE_NUMBER_ID"],
        api_version=c["WHATSAPP_API_VERSION"], lang=c["WHATSAPP_TEMPLATE_LANG"],
    )

def log_whatsapp(db, direction, phone, kind, body, status, appointment_id=None, wa_message_id=None, error=None):
    db.execute(
        """
        INSERT INTO whatsapp_messages
            (direction, phone, appointment_id, kind, body, wa_message_id, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (direction, phone, appointment_id, kind, body, wa_message_id, status, error,
         datetime.now().isoformat(timespec="seconds")),
    )

def format_slot(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%a %d %b %Y, %I:%M %p")
    except (TypeError, ValueError):
        return iso

# Template name -> body parameters, in order. Create these templates in
# WhatsApp Manager with matching {{1}}, {{2}}... placeholders (see README).
TEMPLATES = {
    "appointment_received": ("patient_name", "doctor_name", "when"),
    "appointment_confirmed": ("patient_name", "doctor_name", "when"),
    "appointment_rejected": ("patient_name", "doctor_name", "when"),
    "appointment_reminder": ("patient_name", "doctor_name", "when"),
}

def notify_appointment(db, appt_id, template):
    """Send a WhatsApp template about an appointment to its patient.

    Never raises: a messaging failure must not break booking.
    """
    try:
        row = db.execute(
            """
            SELECT a.id, a.date_time, p.name AS patient_name, p.phone, p.whatsapp_opt_in,
                   d.name AS doctor_name
            FROM appointments a
            JOIN users p ON a.patient_id = p.id
            JOIN users d ON a.doctor_id = d.id
            WHERE a.id = ?
            """,
            (appt_id,),
        ).fetchone()
        if not row or not row["phone"] or not row["whatsapp_opt_in"]:
            return False
        values = {
            "patient_name": row["patient_name"],
            "doctor_name": row["doctor_name"],
            "when": format_slot(row["date_time"]),
        }
        params = [values[k] for k in TEMPLATES[template]]
        status, msg_id, error = get_whatsapp().send_template(row["phone"], template, params)
        log_whatsapp(db, "out", row["phone"], template, " | ".join(params), status,
                     appointment_id=appt_id, wa_message_id=msg_id, error=error)
        db.commit()
        return status != "failed"
    except Exception:
        log.exception("WhatsApp notification %s for appointment %s failed", template, appt_id)
        return False


def send_due_reminders(db, now=None):
    """Send reminders for booked appointments starting within the reminder window."""
    now = now or datetime.now()
    window_end = now + timedelta(hours=current_app.config["REMINDER_HOURS_BEFORE"])
    rows = db.execute(
        """
        SELECT id FROM appointments
        WHERE status = 'booked' AND reminder_sent = 0
          AND date_time > ? AND date_time <= ?
        """,
        (now.isoformat(timespec="minutes"), window_end.isoformat(timespec="minutes")),
    ).fetchall()
    for r in rows:
        # Mark first so a crash mid-send can't cause a reminder storm.
        db.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (r["id"],))
        db.commit()
        notify_appointment(db, r["id"], "appointment_reminder")
    return len(rows)

def check_reminders():
    with app.app_context():
        try:
            sent = send_due_reminders(get_db())
            if sent:
                log.info("Sent %d appointment reminders", sent)
        except Exception:
            log.exception("Reminder job failed")

def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(check_reminders, "interval", seconds=60, id="check_reminders",
                      max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler


def get_next_available_slot(db, doctor_id):
    """Very simple next-slot logic: next 30-min slot from now that is free."""
    now = datetime.now().replace(second=0, microsecond=0)
    # Start from next half hour
    minute = 30 if now.minute < 30 else 0
    base = now.replace(minute=minute) + (timedelta(hours=1) if minute == 0 else timedelta())

    # check next N slots
    for i in range(20):  # next 10 hours
        slot = base + timedelta(minutes=30 * i)
        iso = slot.isoformat(timespec="minutes")
        row = db.execute(
            "SELECT 1 FROM appointments WHERE doctor_id=? AND date_time=? "
            "AND status IN ('pending', 'booked')",
            (doctor_id, iso),
        ).fetchone()
        if not row:
            return iso

    return None  # no slot found
@app.route("/appointments/autobook/<int:doctor_id>", methods=["POST"])
@login_required("patient")
def autobook_appointment(doctor_id):
    db = get_db()
    doctor = db.execute(
        "SELECT id FROM users WHERE id = ? AND role = 'doctor' AND status = 'active'", (doctor_id,)
    ).fetchone()
    if not doctor:
        abort(404)
    next_slot = get_next_available_slot(db, doctor_id)
    if not next_slot:
        flash("No free slot available for this doctor.", "error")
        return redirect(url_for("patient_appointments"))

    cur = db.execute(
        "INSERT INTO appointments (doctor_id, patient_id, date_time, status) VALUES (?, ?, ?, 'pending')",
        (doctor_id, session["user_id"], next_slot),
    )
    db.commit()
    notify_appointment(db, cur.lastrowid, "appointment_received")
    flash(f"Auto-booked next slot at {next_slot}", "success")
    return redirect(url_for("patient_appointments"))


# ---------------------------------------------------------------------------
# Public website
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    doctors = get_db().execute(
        "SELECT id, name, specialty FROM users WHERE role = 'doctor' AND status = 'active' ORDER BY name"
    ).fetchall()
    return render_template("home.html", doctors=doctors)

@app.route("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return {"status": "ok"}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role", "")
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE lower(email) = ? AND role = ?",
            (email, role),
        ).fetchone()

        if not user or not password_matches(user["password"], password):
            flash("Invalid email / password / role", "error")
            return redirect(url_for("login"))

        if user["status"] != "active":
            flash("Account not active", "error")
            return redirect(url_for("login"))

        # Upgrade legacy plain-text passwords to a hash on successful login.
        if not user["password"].startswith(("scrypt:", "pbkdf2:")):
            db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user["id"]))
            db.commit()

        session.clear()
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["name"] = user["name"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def email_taken(db, email):
    return db.execute("SELECT 1 FROM users WHERE lower(email) = ?", (email,)).fetchone() is not None

@app.route("/register/patient", methods=["GET", "POST"])
def register_patient():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone_raw = request.form.get("phone", "").strip()
        age = request.form.get("age") or None
        gender = request.form.get("gender") or None
        blood = request.form.get("blood_group") or None
        opt_in = 1 if request.form.get("whatsapp_opt_in") else 0

        if not name or not email or not password or not phone_raw:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_patient"))

        if len(password) < 8:
            flash("Password must be at least 8 characters", "error")
            return redirect(url_for("register_patient"))

        phone = normalize_phone(phone_raw, app.config["DEFAULT_COUNTRY_CODE"])
        if not phone:
            flash("Please enter a valid mobile number", "error")
            return redirect(url_for("register_patient"))

        db = get_db()
        if email_taken(db, email):
            flash("Email already registered", "error")
            return redirect(url_for("register_patient"))

        cur = db.execute(
            """
            INSERT INTO users (role, name, email, password, phone, whatsapp_opt_in,
                               age, gender, blood_group, status)
            VALUES ('patient', ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (name, email, hash_password(password), phone, opt_in, age, gender, blood),
        )
        db.execute("UPDATE users SET patient_code = ? WHERE id = ?", (f"P{cur.lastrowid:05d}", cur.lastrowid))
        db.commit()
        flash("Patient registered successfully. You can login now.", "success")
        return redirect(url_for("login"))

    return render_template("register_patient.html")

@app.route("/register/doctor", methods=["GET", "POST"])
@login_required("admin")
def register_doctor():
    """Doctors are added by an admin, never by public signup."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        specialty = request.form.get("specialty", "").strip() or None

        if not name or not email or not password:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_doctor"))

        if len(password) < 8:
            flash("Password must be at least 8 characters", "error")
            return redirect(url_for("register_doctor"))

        db = get_db()
        if email_taken(db, email):
            flash("Email already registered", "error")
            return redirect(url_for("register_doctor"))

        db.execute(
            """
            INSERT INTO users (role, name, email, password, specialty, status)
            VALUES ('doctor', ?, ?, ?, ?, 'active')
            """,
            (name, email, hash_password(password), specialty),
        )
        db.commit()
        flash(f"Doctor {name} added. They can login now.", "success")
        return redirect(url_for("admin_users"))

    return render_template("register_doctor.html")

@app.route("/register/admin", methods=["GET", "POST"])
def register_admin():
    db = get_db()
    has_admin = db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone() is not None
    signup_code = app.config["ADMIN_SIGNUP_CODE"]
    if has_admin and not signup_code:
        flash("Admin signup is closed. Ask an existing admin.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin_code = request.form.get("admin_code", "").strip()

        if signup_code and admin_code != signup_code:
            flash("Invalid admin code", "error")
            return redirect(url_for("register_admin"))

        if not name or not email or not password:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_admin"))

        if len(password) < 8:
            flash("Password must be at least 8 characters", "error")
            return redirect(url_for("register_admin"))

        if email_taken(db, email):
            flash("Email already registered", "error")
            return redirect(url_for("register_admin"))

        db.execute(
            """
            INSERT INTO users (role, name, email, password, status)
            VALUES ('admin', ?, ?, ?, 'active')
            """,
            (name, email, hash_password(password)),
        )
        db.commit()
        flash("Admin registered successfully. You can login now.", "success")
        return redirect(url_for("login"))

    return render_template("register_admin.html", needs_code=bool(signup_code))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    user_id = session["user_id"]
    role = session.get("role")

    # PATIENT DASHBOARD
    if role == "patient":
        # Upcoming appointments
        appts = db.execute(
            "SELECT id, doctor_id, date_time, status FROM appointments "
            "WHERE patient_id = ? AND status = 'booked' ORDER BY date_time",
            (user_id,)
        ).fetchall()

        # Medicines count
        medicines = db.execute(
            "SELECT COUNT(*) as cnt FROM medicines WHERE patient_id = ?",
            (user_id,)
        ).fetchone()

        # Last visit
        last_visit_data = db.execute(
            "SELECT doctor_id, date_time FROM appointments "
            "WHERE patient_id = ? AND status IN ('booked', 'completed') "
            "ORDER BY date_time DESC LIMIT 1",
            (user_id,)
        ).fetchone()

        last_visit = None
        if last_visit_data:
            doctor = db.execute("SELECT name FROM users WHERE id = ?", (last_visit_data["doctor_id"],)).fetchone()
            last_visit = {
                "doctor_name": doctor["name"] if doctor else "Unknown",
                "appointment_datetime": last_visit_data["date_time"]
            }

        # Get full appointment details
        appointments = []
        for appt in appts:
            doctor = db.execute("SELECT name FROM users WHERE id = ?", (appt["doctor_id"],)).fetchone()
            appointments.append({
                "id": appt["id"],
                "doctor_name": doctor["name"] if doctor else "Unknown",
                "appointment_datetime": appt["date_time"],
                "status": appt["status"]
            })

        return render_template(
            "patient_dashboard.html",
            name=session.get("name"),
            upcoming_count=len(appts),
            medicines_count=medicines["cnt"] if medicines else 0,
            last_visit=last_visit,
            appointments=appointments
        )

    # DOCTOR DASHBOARD
    elif role == "doctor":
        # Pending appointments
        pending = db.execute(
            "SELECT COUNT(*) as cnt FROM appointments "
            "WHERE doctor_id = ? AND status = 'pending'",
            (user_id,)
        ).fetchone()

        # Total unique patients
        patients = db.execute(
            "SELECT COUNT(DISTINCT patient_id) as cnt FROM appointments "
            "WHERE doctor_id = ?",
            (user_id,)
        ).fetchone()

        # Today's appointments
        today_appts = db.execute(
            "SELECT COUNT(*) as cnt FROM appointments "
            "WHERE doctor_id = ? AND DATE(date_time) = DATE('now')",
            (user_id,)
        ).fetchone()

        # All appointments
        appts = db.execute(
            "SELECT id, patient_id, date_time, status FROM appointments "
            "WHERE doctor_id = ? ORDER BY date_time DESC",
            (user_id,)
        ).fetchall()

        appointments = []
        for appt in appts:
            patient = db.execute("SELECT name FROM users WHERE id = ?", (appt["patient_id"],)).fetchone()
            appointments.append({
                "id": appt["id"],
                "patient_name": patient["name"] if patient else "Unknown",
                "appointment_datetime": appt["date_time"],
                "status": appt["status"]
            })

        return render_template(
            "doctor_dashboard.html",
            name=session.get("name"),
            pending_count=pending["cnt"] if pending else 0,
            total_patients=patients["cnt"] if patients else 0,
            today_count=today_appts["cnt"] if today_appts else 0,
            appointments=appointments
        )

    # ADMIN DASHBOARD
    elif role == "admin":
        # Total users
        total_users = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()

        # Total patients
        total_patients = db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'patient'"
        ).fetchone()

        # Total doctors
        total_doctors = db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE role = 'doctor'"
        ).fetchone()

        # Total appointments
        total_appts = db.execute(
            "SELECT COUNT(*) as cnt FROM appointments"
        ).fetchone()

        # Recent appointments
        appts = db.execute(
            "SELECT a.id, p.name as patient_name, d.name as doctor_name, "
            "a.date_time, a.status FROM appointments a "
            "JOIN users p ON a.patient_id = p.id "
            "JOIN users d ON a.doctor_id = d.id "
            "ORDER BY a.date_time DESC LIMIT 10"
        ).fetchall()

        # All users
        users = db.execute(
            "SELECT id, name, email, role FROM users ORDER BY name"
        ).fetchall()

        appointments = [dict(appt) for appt in appts]
        users_list = [dict(user) for user in users]

        return render_template(
            "admin_dashboard.html",
            total_users=total_users["cnt"] if total_users else 0,
            total_patients=total_patients["cnt"] if total_patients else 0,
            total_doctors=total_doctors["cnt"] if total_doctors else 0,
            total_appointments=total_appts["cnt"] if total_appts else 0,
            appointments=appointments,
            users=users_list
        )

    return redirect(url_for("login"))    
@app.route("/admin/dashboard")
def admin_dashboard():
    if "user_id" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    db = get_db()

    total_users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    total_patients = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role='patient'"
    ).fetchone()["c"]
    total_doctors = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role='doctor'"
    ).fetchone()["c"]

    # appointments table may or may not exist yet; if you already added it:
    total_appointments = 0
    try:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM appointments"
        ).fetchone()
        total_appointments = row["c"]
    except sqlite3.OperationalError:
        # table not created yet
        total_appointments = 0

    stats = {
        "total_users": total_users,
        "total_patients": total_patients,
        "total_doctors": total_doctors,
        "total_appointments": total_appointments,
    }

    return render_template(
        "admin_dashboard.html",
        name=session["name"],
        stats=stats,
    )
@app.route("/admin/users")
def admin_users():
    if "user_id" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    db = get_db()
    patients = db.execute(
        "SELECT id, name, email FROM users WHERE role='patient' ORDER BY id DESC"
    ).fetchall()
    doctors = db.execute(
        "SELECT id, name, email FROM users WHERE role='doctor' ORDER BY id DESC"
    ).fetchall()

    return render_template(
        "admin_users.html",
        name=session["name"],
        patients=patients,
        doctors=doctors,
    )
@app.route("/appointments/patient")
def patient_appointments():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()
    appts = db.execute(
        """
        SELECT a.id, a.date_time, a.status,
               u.name AS doctor_name, u.email AS doctor_email
        FROM appointments a
        JOIN users u ON a.doctor_id = u.id
        WHERE a.patient_id = ?
        ORDER BY a.date_time DESC
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template(
        "patient_appointments.html",
        name=session["name"],
        appointments=appts,
    )
@app.route("/appointments/doctor", methods=["GET", "POST"])
@login_required("doctor")
def doctor_appointments():
    db = get_db()

    if request.method == "POST":
        appt_id = request.form.get("appt_id", type=int)
        action = request.form.get("action")
        if action not in ("approve", "reject") or appt_id is None:
            abort(400)
        new_status = "booked" if action == "approve" else "rejected"
        # Only the appointment's own doctor can change it, and only while pending.
        cur = db.execute(
            "UPDATE appointments SET status = ? WHERE id = ? AND doctor_id = ? AND status = 'pending'",
            (new_status, appt_id, session["user_id"]),
        )
        db.commit()
        if cur.rowcount:
            notify_appointment(db, appt_id,
                               "appointment_confirmed" if new_status == "booked" else "appointment_rejected")
            flash(f"Appointment {new_status}.", "success")
        else:
            flash("Appointment not found or already handled.", "error")
        return redirect(url_for("doctor_appointments"))

    appts = db.execute(
        """
        SELECT a.id, a.date_time, a.status,
               u.name AS patient_name, u.email AS patient_email
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.doctor_id = ?
        ORDER BY a.date_time DESC
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template("doctor_appointments.html", name=session["name"], appointments=appts)
@app.route("/appointments/<int:appt_id>/add_notes", methods=["GET", "POST"])
def add_notes(appt_id):
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("login"))

    db = get_db()
    appt = db.execute(
        """
        SELECT a.id, a.patient_id, a.date_time,
               u.name AS patient_name
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.id = ? AND a.doctor_id = ?
        """,
        (appt_id, session["user_id"]),
    ).fetchone()

    if not appt:
        flash("Appointment not found.", "error")
        return redirect(url_for("doctor_appointments"))

    if request.method == "POST":
        diagnosis = request.form["diagnosis"].strip()
        notes = request.form["notes"].strip()
        followup_days_raw = request.form.get("followup_days", "0").strip()
        try:
            followup_days = int(followup_days_raw)
        except ValueError:
            followup_days = 0

        now = datetime.now().isoformat(timespec="minutes")

        # Save medical history
        db.execute(
            """
            INSERT INTO medical_history
                (patient_id, appointment_id, doctor_id, diagnosis, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (appt["patient_id"], appt["id"], session["user_id"], diagnosis, notes, now),
        )

        # Mark this appointment as completed
        db.execute(
            "UPDATE appointments SET status = 'completed' WHERE id = ?",
            (appt_id,),
        )

        # Auto-book next appointment if followup_days > 0
        if followup_days > 0:
            # Compute ideal next datetime
            ideal_next = compute_followup_datetime(appt["date_time"], followup_days)

            # Find the next free slot (avoid overlaps)
            free_slot = find_next_free_slot(db, session["user_id"], ideal_next)

            if free_slot:
                cur = db.execute(
                    """
                    INSERT INTO appointments (doctor_id, patient_id, date_time, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (session["user_id"], appt["patient_id"], free_slot),
                )
                followup_id = cur.lastrowid
                flash(f"Next appointment auto-booked on {free_slot} (next free slot)", "success")
            else:
                flash("Visit notes saved. Could not find a free slot for follow-up.", "error")
        else:
            flash("Visit notes saved. No follow-up scheduled.", "success")

        db.commit()
        if followup_days > 0 and free_slot:
            notify_appointment(db, followup_id, "appointment_received")
        return redirect(url_for("doctor_appointments"))

    return render_template("add_notes.html", appt=appt)
@app.route("/appointments/<int:appt_id>/prescribe", methods=["GET", "POST"])
def prescribe_medicine(appt_id):
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("login"))

    db = get_db()
    appt = db.execute(
        """
        SELECT a.id, a.patient_id, a.date_time,
               u.name AS patient_name
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.id = ? AND a.doctor_id = ?
        """,
        (appt_id, session["user_id"]),
    ).fetchone()

    if not appt:
        flash("Appointment not found.", "error")
        return redirect(url_for("doctor_appointments"))

    if request.method == "POST":
        medicine_name = request.form["medicine_name"].strip()
        dosage = request.form.get("dosage", "").strip()
        frequency = request.form.get("frequency", "").strip()
        duration = request.form.get("duration", "").strip()

        if not medicine_name:
            flash("Medicine name is required", "error")
            return redirect(url_for("prescribe_medicine", appt_id=appt_id))

        now = datetime.now().isoformat(timespec="minutes")

        db.execute(
            """
            INSERT INTO medicines
                (appointment_id, patient_id, medicine_name, dosage, frequency, duration, prescribed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (appt["id"], appt["patient_id"], medicine_name, dosage, frequency, duration, now),
        )
        db.commit()
        flash("Medicine prescribed successfully.", "success")
        return redirect(url_for("prescribe_medicine", appt_id=appt_id))

    # GET: show existing prescriptions for this appointment
    medicines = db.execute(
        """
        SELECT medicine_name, dosage, frequency, duration, prescribed_at
        FROM medicines
        WHERE appointment_id = ?
        ORDER BY prescribed_at DESC
        """,
        (appt_id,),
    ).fetchall()

    return render_template("prescribe_medicine.html", appt=appt, medicines=medicines)
@app.route("/medicines")
def patient_medicines():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()
    medicines = db.execute(
        """
        SELECT m.medicine_name, m.dosage, m.frequency, m.duration, m.prescribed_at,
               u.name AS doctor_name
        FROM medicines m
        JOIN appointments a ON m.appointment_id = a.id
        JOIN users u ON a.doctor_id = u.id
        WHERE m.patient_id = ?
        ORDER BY m.prescribed_at DESC
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template("patient_medicines.html", name=session["name"], medicines=medicines)

@app.route("/appointments/book", methods=["GET", "POST"])
def book_appointment():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id", type=int)
        date_time_str = request.form.get("date_time", "").strip()

        if not doctor_id or not date_time_str:
            flash("Please select doctor and date/time", "error")
            return redirect(url_for("book_appointment"))

        # Validate datetime format
        try:
            dt = datetime.fromisoformat(date_time_str)
            iso_time = dt.isoformat(timespec="minutes")
        except ValueError:
            flash("Invalid date/time format. Use YYYY-MM-DD HH:MM", "error")
            return redirect(url_for("book_appointment"))

        if dt <= datetime.now():
            flash("Please choose a time in the future.", "error")
            return redirect(url_for("book_appointment"))

        doctor = db.execute(
            "SELECT 1 FROM users WHERE id = ? AND role = 'doctor' AND status = 'active'", (doctor_id,)
        ).fetchone()
        if not doctor:
            flash("Please select a valid doctor.", "error")
            return redirect(url_for("book_appointment"))

        # Check if doctor already has an appointment at this exact time
        conflict = db.execute(
            "SELECT 1 FROM appointments WHERE doctor_id = ? AND date_time = ? "
            "AND status IN ('pending', 'booked')",
            (doctor_id, iso_time),
        ).fetchone()

        if conflict:
            flash("This time slot is already booked with this doctor. Please choose another time.", "error")
            return redirect(url_for("book_appointment"))

        # Slot is free, create appointment
        cur = db.execute(
            """
            INSERT INTO appointments (doctor_id, patient_id, date_time, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (doctor_id, session["user_id"], iso_time),
        )
        db.commit()
        notify_appointment(db, cur.lastrowid, "appointment_received")
        flash("Appointment request sent!", "success")
        return redirect(url_for("patient_appointments"))

    # GET: show form with list of doctors
    doctors = db.execute(
        "SELECT id, name, email, specialty FROM users WHERE role = 'doctor' AND status = 'active'"
    ).fetchall()

    return render_template("book_appointment.html", doctors=doctors)

@app.route("/admin/appointments")
def admin_appointments():
    if "user_id" not in session or session["role"] != "admin":
        return redirect(url_for("login"))

    db = get_db()
    appts = db.execute(
        """
        SELECT a.id, a.date_time, a.status,
               d.name AS doctor_name,
               p.name AS patient_name
        FROM appointments a
        JOIN users d ON a.doctor_id = d.id
        JOIN users p ON a.patient_id = p.id
        ORDER BY a.date_time DESC
        """
    ).fetchall()

    return render_template(
        "admin_appointments.html",
        name=session["name"],
        appointments=appts,
    )
@app.route("/history")
def patient_history():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()
    rows = db.execute(
        """
        SELECT h.diagnosis, h.notes, h.created_at,
               u.name AS doctor_name
        FROM medical_history h
        JOIN users u ON h.doctor_id = u.id
        WHERE h.patient_id = ?
        ORDER BY h.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()

    return render_template("patient_history.html", name=session["name"], history=rows)
def compute_followup_datetime(current_dt_str, days):
    """
    Given current appointment datetime string (ISO from DB)
    and a number of days, return the next appointment datetime
    at a suitable hour (e.g. 10:00 AM) as ISO string.
    """
    try:
        current_dt = datetime.fromisoformat(current_dt_str)
    except ValueError:
        current_dt = datetime.now()

    # Base next date = current date + given days
    target_date = (current_dt + timedelta(days=days)).date()

    # Define a suitable default time (e.g. 10:00 AM)
    followup_dt = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=10,
        minute=0,
    )

    return followup_dt.isoformat(timespec="minutes")
def find_next_free_slot(db, doctor_id, start_datetime_str, max_attempts=20):
    """
    Given a doctor and a starting datetime, find the next free 30-minute slot.
    Checks if the slot is already booked; if yes, tries the next 30-min slot.
    Returns ISO datetime string of the free slot, or None if none found.
    """
    try:
        current = datetime.fromisoformat(start_datetime_str)
    except ValueError:
        return None

    for i in range(max_attempts):
        candidate = current + timedelta(minutes=30 * i)
        iso_str = candidate.isoformat(timespec="minutes")

        # Check if doctor already has an appointment at this time
        row = db.execute(
            "SELECT 1 FROM appointments WHERE doctor_id = ? AND date_time = ? "
            "AND status IN ('pending', 'booked')",
            (doctor_id, iso_str),
        ).fetchone()

        if not row:
            # Slot is free
            return iso_str

    return None  # No free slot found in max_attempts

# ---------------------------------------------------------------------------
# WhatsApp webhook (Meta calls this when patients reply)
# ---------------------------------------------------------------------------

@app.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_verify():
    """Meta's one-time verification handshake when the webhook URL is saved."""
    token = app.config["WHATSAPP_VERIFY_TOKEN"]
    if (token and request.args.get("hub.mode") == "subscribe"
            and request.args.get("hub.verify_token") == token):
        return request.args.get("hub.challenge", ""), 200
    abort(403)

@app.route("/webhooks/whatsapp", methods=["POST"])
@csrf.exempt
def whatsapp_webhook():
    if not verify_signature(app.config["WHATSAPP_APP_SECRET"], request.get_data(),
                            request.headers.get("X-Hub-Signature-256")):
        abort(403)

    db = get_db()
    messages, statuses = parse_webhook(request.get_json(silent=True) or {})

    for s in statuses:
        if s["id"]:
            db.execute("UPDATE whatsapp_messages SET status = ? WHERE wa_message_id = ?",
                       (s["status"], s["id"]))

    for m in messages:
        log_whatsapp(db, "in", m["from"], m["type"], m["text"], "received", wa_message_id=m["id"])
        handle_patient_reply(db, m["from"], m["text"])

    db.commit()
    # Always 200 quickly, otherwise Meta retries the delivery.
    return "", 200

def handle_patient_reply(db, phone, text):
    """Let a patient confirm (1) or cancel (2) their next upcoming appointment."""
    intent = classify_reply(text)
    client = get_whatsapp()
    if intent is None:
        clinic_phone = app.config["CLINIC_PHONE"]
        reply = ("Thanks for your message. Reply 1 to confirm or 2 to cancel your next appointment."
                 + (f" For anything else please call {clinic_phone}." if clinic_phone else ""))
        status, msg_id, error = client.send_text(phone, reply)
        log_whatsapp(db, "out", phone, "text", reply, status, wa_message_id=msg_id, error=error)
        return None

    appt = db.execute(
        """
        SELECT a.id, a.date_time, d.name AS doctor_name
        FROM appointments a
        JOIN users p ON a.patient_id = p.id
        JOIN users d ON a.doctor_id = d.id
        WHERE p.phone = ? AND a.status IN ('pending', 'booked') AND a.date_time > ?
        ORDER BY a.date_time
        LIMIT 1
        """,
        (phone, datetime.now().isoformat(timespec="minutes")),
    ).fetchone()

    if not appt:
        reply = "We couldn't find an upcoming appointment for this number."
    elif intent == "cancel":
        db.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt["id"],))
        reply = f"Your appointment with {appt['doctor_name']} on {format_slot(appt['date_time'])} has been cancelled."
    else:
        reply = f"Thank you! See you on {format_slot(appt['date_time'])} with {appt['doctor_name']}."

    status, msg_id, error = client.send_text(phone, reply)
    log_whatsapp(db, "out", phone, "text", reply, status,
                 appointment_id=appt["id"] if appt else None, wa_message_id=msg_id, error=error)
    return intent if appt else None


# Create/upgrade tables on startup so a fresh deploy works without manual steps.
init_db(app.config["DATABASE"])
if app.config["ENABLE_SCHEDULER"]:
    start_scheduler()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
