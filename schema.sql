CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT NOT NULL,              -- 'patient','doctor','admin'
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password      TEXT NOT NULL,              -- werkzeug password hash
    status        TEXT NOT NULL DEFAULT 'active',
    age           INTEGER,
    gender        TEXT,
    blood_group   TEXT,
    patient_code  TEXT UNIQUE,
    phone         TEXT,                       -- E.164 without '+', e.g. 919876543210
    specialty     TEXT,
    whatsapp_opt_in INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS appointments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id     INTEGER NOT NULL,
    patient_id    INTEGER NOT NULL,
    date_time     TEXT NOT NULL,
    status        TEXT NOT NULL,   -- 'pending','booked','completed','rejected','cancelled'
    reminder_sent INTEGER DEFAULT 0,
    FOREIGN KEY (doctor_id) REFERENCES users(id),
    FOREIGN KEY (patient_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS medical_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id     INTEGER NOT NULL,
    appointment_id INTEGER,
    doctor_id      INTEGER,
    diagnosis      TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES users(id),
    FOREIGN KEY (appointment_id) REFERENCES appointments(id),
    FOREIGN KEY (doctor_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS medicines (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    patient_id     INTEGER NOT NULL,
    medicine_name  TEXT NOT NULL,
    dosage         TEXT,
    frequency      TEXT,
    duration       TEXT,
    prescribed_at  TEXT NOT NULL,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id),
    FOREIGN KEY (patient_id) REFERENCES users(id)
);

-- Log of every WhatsApp message sent or received, for debugging and audit.
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    direction      TEXT NOT NULL,   -- 'out' or 'in'
    phone          TEXT NOT NULL,
    appointment_id INTEGER,
    kind           TEXT,            -- template name, 'text', 'button', ...
    body           TEXT,
    wa_message_id  TEXT,
    status         TEXT,            -- 'sent','failed','logged','received', delivery status updates
    error          TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appt_doctor_time ON appointments(doctor_id, date_time);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
