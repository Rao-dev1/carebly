import sqlite3

DB_PATH = "carebly.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Add the column only if it does not exist
cur.execute("PRAGMA table_info(users);")
cols = [row[1] for row in cur.fetchall()]

if "patient_code" not in cols:
    cur.execute("ALTER TABLE users ADD COLUMN patient_code TEXT UNIQUE;")
    print("patient_code column added.")
else:
    print("patient_code column already exists.")

conn.commit()
conn.close()
