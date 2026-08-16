from flask import Flask, render_template, request, redirect, url_for, session, g, flash
from datetime import datetime, timedelta
from flask_apscheduler import APScheduler

import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "carebly.db")

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
app.secret_key = "x9F!2kLmPq#7zV1@2026"
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
@scheduler.task('interval', id='check_reminders', seconds=60)
def check_reminders():
    with app.app_context():
        db = get_db()
        now = datetime.now().isoformat(timespec="minutes")
        rows = db.execute(
            """
            SELECT a.id, a.date_time, u.email
            FROM appointments a
            JOIN users u ON a.patient_id = u.id
            WHERE a.status='booked' AND a.reminder_sent=0
            """
        ).fetchall()
        for r in rows:
            # TODO: compare times & send notification (email/SMS)
            # For now, just mark as sent and log
            print(f"Reminder would be sent to {r['email']} for {r['date_time']}")
            db.execute(
                "UPDATE appointments SET reminder_sent=1 WHERE id=?",
                (r["id"],),
            )
        db.commit()


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
            "SELECT 1 FROM appointments WHERE doctor_id=? AND date_time=?",
            (doctor_id, iso),
        ).fetchone()
        if not row:
            return iso

    return None  # no slot found
@app.route("/appointments/autobook/<int:doctor_id>", methods=["POST"])
def autobook_appointment(doctor_id):
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()
    next_slot = get_next_available_slot(db, doctor_id)
    if not next_slot:
        flash("No free slot available for this doctor.", "error")
        return redirect(url_for("patient_appointments"))

    db.execute(
        "INSERT INTO appointments (doctor_id, patient_id, date_time, status) VALUES (?, ?, ?, 'pending')",
        (doctor_id, session["user_id"], next_slot),
    )
    db.commit()
    flash(f"Auto-booked next slot at {next_slot}", "success")
    return redirect(url_for("patient_appointments"))

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form["role"]
        email = request.form["email"].strip()
        password = request.form["password"]

        db = get_db()
        cur = db.execute(
            "SELECT * FROM users WHERE email = ? AND password = ? AND role = ?",
            (email, password, role),
        )
        user = cur.fetchone()

        if not user:
            flash("Invalid email / password / role", "error")
            return redirect(url_for("login"))

        if user["status"] != "active":
            flash("Account not active", "error")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["name"] = user["name"]
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register/patient", methods=["GET", "POST"])
def register_patient():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        age = request.form.get("age")
        gender = request.form.get("gender")
        blood = request.form.get("blood_group")

        if not name or not email or not password:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_patient"))

        db = get_db()
        cur = db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            flash("Email already registered", "error")
            return redirect(url_for("register_patient"))

        db.execute(
            """
            INSERT INTO users (role, name, email, password, age, gender, blood_group, status)
            VALUES ('patient', ?, ?, ?, ?, ?, ?, 'active')
            """,
            (name, email, password, age, gender, blood),
        )
        db.commit()
        flash("Patient registered successfully. You can login now.", "success")
        return redirect(url_for("login"))

    return render_template("register_patient.html")

@app.route("/register/doctor", methods=["GET", "POST"])
def register_doctor():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]

        if not name or not email or not password:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_doctor"))

        db = get_db()
        cur = db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            flash("Email already registered", "error")
            return redirect(url_for("register_doctor"))

        db.execute(
            """
            INSERT INTO users (role, name, email, password, status)
            VALUES ('doctor', ?, ?, ?, 'active')
            """,
            (name, email, password),
        )
        db.commit()
        flash("Doctor registered successfully. You can login now.", "success")
        return redirect(url_for("login"))

    return render_template("register_doctor.html")

@app.route("/register/admin", methods=["GET", "POST"])
def register_admin():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        admin_code = request.form["admin_code"].strip()

        # simple fixed code (you can change it)
        if admin_code != "ADMIN2026":
            flash("Invalid admin code", "error")
            return redirect(url_for("register_admin"))

        if not name or not email or not password:
            flash("All required fields must be filled", "error")
            return redirect(url_for("register_admin"))

        db = get_db()
        cur = db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cur.fetchone():
            flash("Email already registered", "error")
            return redirect(url_for("register_admin"))

        db.execute(
            """
            INSERT INTO users (role, name, email, password, status)
            VALUES ('admin', ?, ?, ?, 'active')
            """,
            (name, email, password),
        )
        db.commit()
        flash("Admin registered successfully. You can login now.", "success")
        return redirect(url_for("login"))

    return render_template("register_admin.html")

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
@app.route("/appointments/<int:appt_id>/approve")
def approve_appointment(appt_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect(url_for("login"))

    db = get_db()
    db.execute(
        "UPDATE appointments SET status = 'booked' WHERE id = ?",
        (appt_id,)
    )
    db.commit()
    flash("Appointment approved successfully.", "success")
    return redirect(url_for("doctor_appointments"))


@app.route("/appointments/<int:appt_id>/reject")
def reject_appointment(appt_id):
    if "user_id" not in session or session.get("role") != "doctor":
        return redirect(url_for("login"))

    db = get_db()
    db.execute(
        "UPDATE appointments SET status = 'rejected' WHERE id = ?",
        (appt_id,)
    )
    db.commit()
    flash("Appointment rejected.", "warning")
    return redirect(url_for("doctor_appointments"))

@app.route("/appointments/doctor", methods=["GET", "POST"])
def doctor_appointments():
    if "user_id" not in session or session["role"] != "doctor":
        return redirect(url_for("login"))

    db = get_db()

    if request.method == "POST":
        appt_id = request.form["appt_id"]
        action = request.form["action"]
        new_status = "booked" if action == "approve" else "rejected"
        db.execute(
            "UPDATE appointments SET status = ? WHERE id = ? AND doctor_id = ?",
            (new_status, appt_id, session["user_id"]),
        )
        db.commit()
        flash(f"Appointment {new_status}.", "success")

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
                db.execute(
                    """
                    INSERT INTO appointments (doctor_id, patient_id, date_time, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (session["user_id"], appt["patient_id"], free_slot),
                )
                flash(f"Next appointment auto-booked on {free_slot} (next free slot)", "success")
            else:
                flash("Visit notes saved. Could not find a free slot for follow-up.", "error")
        else:
            flash("Visit notes saved. No follow-up scheduled.", "success")

        db.commit()
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

from datetime import datetime  # make sure this import is at the top
@app.route("/appointments/book", methods=["GET", "POST"])
def book_appointment():
    if "user_id" not in session or session["role"] != "patient":
        return redirect(url_for("login"))

    db = get_db()

    if request.method == "POST":
        doctor_id = request.form["doctor_id"]
        date_time_str = request.form["date_time"].strip()

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

        # Check if doctor already has an appointment at this exact time
        conflict = db.execute(
            "SELECT 1 FROM appointments WHERE doctor_id = ? AND date_time = ?",
            (doctor_id, iso_time),
        ).fetchone()

        if conflict:
            flash("This time slot is already booked with this doctor. Please choose another time.", "error")
            return redirect(url_for("book_appointment"))

        # Slot is free, create appointment
        db.execute(
            """
            INSERT INTO appointments (doctor_id, patient_id, date_time, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (doctor_id, session["user_id"], iso_time),
        )
        db.commit()
        flash("Appointment request sent!", "success")
        return redirect(url_for("patient_appointments"))

    # GET: show form with list of doctors
    doctors = db.execute(
        "SELECT id, name, email FROM users WHERE role = 'doctor'"
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
            "SELECT 1 FROM appointments WHERE doctor_id = ? AND date_time = ?",
            (doctor_id, iso_str),
        ).fetchone()

        if not row:
            # Slot is free
            return iso_str

    return None  # No free slot found in max_attempts

if __name__ == "__main__":
    app.run(debug=True)
