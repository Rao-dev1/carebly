DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS appointments;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT NOT NULL,              -- 'patient','doctor','admin'
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    age           INTEGER,
    gender        TEXT,
    blood_group   TEXT
);

CREATE TABLE appointments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id  INTEGER NOT NULL,
    patient_id INTEGER NOT NULL,
    date_time  TEXT NOT NULL,
    status     TEXT NOT NULL,   -- 'pending','booked','completed','rejected'
    FOREIGN KEY (doctor_id) REFERENCES users(id),
    FOREIGN KEY (patient_id) REFERENCES users(id)
);
ALTER TABLE appointments ADD COLUMN reminder_sent INTEGER DEFAULT 0;
DROP TABLE IF EXISTS medical_history;

CREATE TABLE medical_history (
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
DROP TABLE IF EXISTS medicines;

CREATE TABLE medicines (
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
ALTER TABLE users ADD COLUMN patient_code TEXT UNIQUE;

