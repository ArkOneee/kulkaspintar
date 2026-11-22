import os
import re
import sqlite3
from datetime import date, timedelta

import pandas as pd

DB_PATH = os.path.join("data", "kulkaspintar.db")
CSV_PATHS = [os.path.join("data", "all_cleaned_data.csv"), "all_cleaned_data.csv"]

STOPWORDS = {
    "secukupnya", "sdm", "sdt", "buah", "siung", "batang", "lembar", "butir",
    "gram", "gr", "kg", "ekor", "potong", "ruas", "ikat", "bungkus", "ml",
    "liter", "gelas", "sendok", "makan", "teh", "iris", "halus", "geprek",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pantry_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL,
    category TEXT DEFAULT 'Umum', added_date TEXT NOT NULL, expiry_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, ingredients_raw TEXT NOT NULL, ingredients_clean TEXT NOT NULL,
    steps TEXT NOT NULL, category TEXT DEFAULT 'Umum', prep_time_mins INTEGER DEFAULT 20,
    cluster_label TEXT DEFAULT 'Menu Hemat'
);
CREATE TABLE IF NOT EXISTS weekly_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_type TEXT NOT NULL, day_name TEXT NOT NULL, recipe_id INTEGER,
    recipe_title TEXT NOT NULL, missing_items TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL, sender TEXT NOT NULL, message TEXT NOT NULL,
    route_used TEXT DEFAULT 'rule_based', created_at TEXT NOT NULL
);
"""


def get_connection():
    """Membuka koneksi SQLite dengan akses kolom berbasis nama."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Membuat seluruh skema tabel SQLite aplikasi."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def is_empty(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def insert(conn, table, columns, rows):
    marks = ", ".join("?" * len(columns))
    conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", rows)


def split_parts(raw):
    """Memecah teks berpemisah '--' menjadi daftar item non-kosong."""
    return [p.strip() for p in raw.split("--") if p.strip()]


def clean_text(text):
    """Membersihkan teks bahan dari takaran dan kata umum."""
    text = re.sub(r"\([^)]*\)|--", " ", text.lower())
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(w for w in text.split() if len(w) > 2 and w not in STOPWORDS)


def recipe_record(row):
    """Mengubah satu baris CSV menjadi tuple siap insert ke tabel recipes."""
    category = row.SourceFile.replace("dataset-", "").capitalize()
    steps = split_parts(row.Steps)
    return (
        row.Title.strip(),
        ", ".join(split_parts(row.Ingredients)),
        clean_text(row.Ingredients),
        "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)),
        category,
        min(60, max(10, len(steps) * 5)),
    )


def seed_recipes(conn):
    """Mengimpor dataset resep ke tabel recipes jika masih kosong."""
    if not is_empty(conn, "recipes"):
        return
    csv_file = next((p for p in CSV_PATHS if os.path.exists(p)), None)
    if not csv_file:
        raise FileNotFoundError("File all_cleaned_data.csv tidak ditemukan.")

    df = (
        pd.read_csv(csv_file)
        .dropna(subset=["Title", "Ingredients", "Steps"])
        .drop_duplicates(subset=["URL"])
    )
    columns = [
        "title", "ingredients_raw", "ingredients_clean",
        "steps", "category", "prep_time_mins",
    ]
    insert(conn, "recipes", columns, [recipe_record(r) for r in df.itertuples()])


def seed_sample_user_data(conn):
    """Mengisi contoh awal stok kulkas dan log belanja untuk pengujian."""
    def day(offset=0):
        return str(date.today() + timedelta(days=offset))

    if is_empty(conn, "pantry_items"):
        insert(
            conn,
            "pantry_items",
            ["item_name", "quantity", "unit", "category", "added_date", "expiry_date"],
            [
                ("Telur Ayam", 6, "butir", "Telur", day(), day(10)),
                ("Tahu Putih", 4, "buah", "Tahu", day(-2), day(1)),
                ("Daun Bawang", 2, "batang", "Sayur", day(-1), day(2)),
                ("Kecap Manis", 1, "botol", "Bumbu", day(-10), day(60)),
            ],
        )

    


if __name__ == "__main__":
    init_db()
    with get_connection() as conn:
        seed_recipes(conn)
        seed_sample_user_data(conn)
        total = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    print(f"Database siap! Total resep dimuat: {total}")