import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest import mock

import app as carebly
from conftest import make_user, login
from whatsapp import normalize_phone, classify_reply


def query(app, sql, args=()):
    with app.app_context():
        return carebly.get_db().execute(sql, args).fetchall()


def add_appt(app, doctor_id, patient_id, when, status="pending"):
    with app.app_context():
        db = carebly.get_db()
        cur = db.execute(
            "INSERT INTO appointments (doctor_id, patient_id, date_time, status) VALUES (?, ?, ?, ?)",
            (doctor_id, patient_id, when.isoformat(timespec="minutes"), status),
        )
        db.commit()
        return cur.lastrowid


def test_home_page_lists_doctors(app, client):
    make_user(app, "doctor", "Dr. Mehta", "mehta@example.com")
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Dr. Mehta" in resp.data


def test_patient_registration_hashes_password_and_stores_phone(app, client):
    resp = client.post("/register/patient", data={
        "name": "Asha", "email": "Asha@Example.com", "password": "secretpass",
        "phone": "98765 43210", "whatsapp_opt_in": "1",
    })
    assert resp.status_code == 302
    row = query(app, "SELECT * FROM users WHERE email = 'asha@example.com'")[0]
    assert row["password"] != "secretpass"
    assert row["phone"] == "919876543210"
    assert row["patient_code"] == f"P{row['id']:05d}"
    assert login(client, "asha@example.com", "patient", "secretpass").headers["Location"].endswith("/dashboard")


def test_legacy_plaintext_password_is_upgraded_on_login(app, client):
    with app.app_context():
        db = carebly.get_db()
        db.execute("INSERT INTO users (role, name, email, password) VALUES ('patient', 'Old', 'old@example.com', 'plain123')")
        db.commit()
    assert login(client, "old@example.com", "patient", "plain123").headers["Location"].endswith("/dashboard")
    assert query(app, "SELECT password FROM users WHERE email='old@example.com'")[0][0].startswith(("scrypt:", "pbkdf2:"))


def test_wrong_password_rejected(app, client):
    make_user(app, "patient", "P", "p@example.com")
    resp = login(client, "p@example.com", "patient", "wrong-password")
    assert resp.headers["Location"].endswith("/login")


def test_public_cannot_register_doctor(app, client):
    resp = client.post("/register/doctor", data={"name": "X", "email": "x@example.com", "password": "password123"})
    assert resp.status_code == 302
    assert not query(app, "SELECT 1 FROM users WHERE email='x@example.com'")


def test_admin_signup_closed_after_first_admin_without_code(app, client):
    resp = client.post("/register/admin", data={"name": "A", "email": "a@example.com", "password": "password123"})
    assert resp.status_code == 302
    assert query(app, "SELECT 1 FROM users WHERE role='admin'")
    client.post("/register/admin", data={"name": "B", "email": "b@example.com", "password": "password123"})
    assert len(query(app, "SELECT 1 FROM users WHERE role='admin'")) == 1


def test_doctor_cannot_approve_another_doctors_appointment(app, client):
    d1 = make_user(app, "doctor", "D1", "d1@example.com")
    d2 = make_user(app, "doctor", "D2", "d2@example.com")
    p = make_user(app, "patient", "P", "p@example.com")
    appt = add_appt(app, d2, p, datetime.now() + timedelta(days=2))
    login(client, "d1@example.com", "doctor")
    client.post("/appointments/doctor", data={"appt_id": appt, "action": "approve"})
    assert query(app, "SELECT status FROM appointments WHERE id=?", (appt,))[0][0] == "pending"
    # The old unauthenticated GET approve link is gone.
    assert client.get(f"/appointments/{appt}/approve").status_code == 404


def test_approval_sends_whatsapp_confirmation(app, client):
    d = make_user(app, "doctor", "Dr. Rao", "rao@example.com")
    p = make_user(app, "patient", "Asha", "asha@example.com", phone="919876543210")
    appt = add_appt(app, d, p, datetime.now() + timedelta(days=2))
    login(client, "rao@example.com", "doctor")
    with mock.patch("whatsapp.WhatsAppClient.send_template", return_value=("sent", "wamid.1", None)) as send:
        client.post("/appointments/doctor", data={"appt_id": appt, "action": "approve"})
    assert query(app, "SELECT status FROM appointments WHERE id=?", (appt,))[0][0] == "booked"
    to, template, params = send.call_args.args
    assert (to, template) == ("919876543210", "appointment_confirmed")
    assert params[:2] == ["Asha", "Dr. Rao"]
    assert query(app, "SELECT status FROM whatsapp_messages WHERE wa_message_id='wamid.1'")[0][0] == "sent"


def test_booking_rejects_past_time_and_unknown_doctor(app, client):
    d = make_user(app, "doctor", "D", "d@example.com")
    make_user(app, "patient", "P", "p@example.com")
    login(client, "p@example.com", "patient")
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    client.post("/appointments/book", data={"doctor_id": d, "date_time": past})
    client.post("/appointments/book", data={"doctor_id": 9999, "date_time": future})
    assert not query(app, "SELECT 1 FROM appointments")
    client.post("/appointments/book", data={"doctor_id": d, "date_time": future})
    assert len(query(app, "SELECT 1 FROM appointments")) == 1


def test_reminders_only_for_appointments_inside_window(app):
    d = make_user(app, "doctor", "D", "d@example.com")
    p = make_user(app, "patient", "P", "p@example.com", phone="919876543210")
    now = datetime(2026, 11, 1, 9, 0)
    soon = add_appt(app, d, p, now + timedelta(hours=5), status="booked")
    later = add_appt(app, d, p, now + timedelta(days=3), status="booked")
    pending = add_appt(app, d, p, now + timedelta(hours=6), status="pending")
    with app.app_context(), mock.patch("whatsapp.WhatsAppClient.send_template", return_value=("sent", "w", None)) as send:
        assert carebly.send_due_reminders(carebly.get_db(), now=now) == 1
        assert carebly.send_due_reminders(carebly.get_db(), now=now) == 0
    assert send.call_count == 1
    sent = {r[0]: r[1] for r in query(app, "SELECT id, reminder_sent FROM appointments")}
    assert sent == {soon: 1, later: 0, pending: 0}


def signed_post(client, payload, secret="test-secret"):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": sig})


def wa_text(phone, text):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": phone, "id": "wamid.in", "type": "text", "text": {"body": text}}]}}]}]}


def test_webhook_verification(client):
    ok = client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42")
    assert ok.status_code == 200 and ok.data == b"42"
    assert client.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42").status_code == 403


def test_webhook_rejects_bad_signature(client):
    assert signed_post(client, wa_text("919876543210", "2"), secret="wrong").status_code == 403


def test_patient_can_cancel_by_replying_2(app, client):
    d = make_user(app, "doctor", "D", "d@example.com")
    p = make_user(app, "patient", "P", "p@example.com", phone="919876543210")
    appt = add_appt(app, d, p, datetime.now() + timedelta(days=1), status="booked")
    with mock.patch("whatsapp.WhatsAppClient.send_text", return_value=("sent", "w", None)) as send:
        assert signed_post(client, wa_text("919876543210", "2")).status_code == 200
    assert query(app, "SELECT status FROM appointments WHERE id=?", (appt,))[0][0] == "cancelled"
    assert "cancelled" in send.call_args.args[1]


def test_normalize_phone():
    assert normalize_phone("98765 43210") == "919876543210"
    assert normalize_phone("098765-43210") == "919876543210"
    assert normalize_phone("+44 7700 900123") == "447700900123"
    assert normalize_phone("123") is None


def test_classify_reply():
    assert classify_reply(" YES ") == "confirm"
    assert classify_reply("2") == "cancel"
    assert classify_reply("hello") is None
