from datetime import date, timedelta

from database import get_connection

EXPIRING_SOON_DAYS = 3


def _clean(value, default=None):
    """Merapikan teks input; mengembalikan default jika kosong atau None."""
    return str(value or "").strip() or default 


def get_expiry_info(expiry_date_str, ref_date=None):
    """Menghitung sisa hari simpan dan status warna kesegaran bahan."""
    days_left = (date.fromisoformat(expiry_date_str) - (ref_date or date.today())).days
    if days_left < 0:
        status, color, label = "expired", "red", "Kedaluwarsa"
    elif days_left <= EXPIRING_SOON_DAYS:
        status, color, label = "expiring_soon", "yellow", "Segera Olah"
    else:
        status, color, label = "fresh", "green", "Segar"
    return {"days_left": days_left, "status": status, "color": color, "label": label}


def list_pantry(ref_date=None):
    """Mengambil seluruh stok kulkas terurut dari yang paling cepat kedaluwarsa."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pantry_items ORDER BY expiry_date ASC, id DESC"
        ).fetchall()
    return [{**dict(r), **get_expiry_info(r["expiry_date"], ref_date)} for r in rows]


def get_pantry_summary(ref_date=None):
    """Mengelompokkan stok bahan berdasarkan status kesegaran."""
    items = list_pantry(ref_date)
    by_status = {
        s: [i for i in items if i["status"] == s]
        for s in ("expiring_soon", "fresh", "expired")
    }
    return {"all": items, **by_status}

def add_item(item_name, quantity, unit, category="Umum", expiry_date=None, shelf_life_days=7):
    """Menambahkan bahan baru ke stok kulkas dengan validasi input."""
    name, qty = _clean(item_name), float(quantity)
    if not name or qty <= 0:
        raise ValueError("Nama bahan wajib diisi dan jumlah harus lebih dari 0.")

    today = date.today()
    expiry = expiry_date or str(today + timedelta(days=int(shelf_life_days)))
    date.fromisoformat(expiry)

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO pantry_items
                (item_name, quantity, unit, category, added_date, expiry_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, qty, _clean(unit, "pcs"), _clean(category, "Umum"), str(today), expiry),
        )
        return cur.lastrowid


def update_item(item_id, quantity=None, expiry_date=None, item_name=None, unit=None, category=None):
    """Memperbarui hanya field yang diisi; jumlah <= 0 otomatis menghapus item."""
    if quantity is not None and float(quantity) <= 0:
        return delete_item(item_id)
    if expiry_date is not None:
        date.fromisoformat(expiry_date)

    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE pantry_items
            SET item_name = COALESCE(?, item_name),
                quantity = COALESCE(?, quantity),
                unit = COALESCE(?, unit),
                category = COALESCE(?, category),
                expiry_date = COALESCE(?, expiry_date)
            WHERE id = ?
            """,
            (
                _clean(item_name),
                float(quantity) if quantity is not None else None,
                _clean(unit),
                _clean(category),
                expiry_date,
                item_id,
            ),
        )
        return cur.rowcount > 0


def consume_item(item_id, amount=1):
    """Mengurangi stok setelah dipakai memasak; item dihapus otomatis jika habis."""
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Jumlah yang dipakai harus lebih dari 0.")
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE pantry_items SET quantity = quantity - ? WHERE id = ?", (amount, item_id)
        )
        conn.execute("DELETE FROM pantry_items WHERE id = ? AND quantity <= 0", (item_id,))
        return cur.rowcount > 0


def delete_item(item_id):
    """Menghapus item bahan dari stok kulkas."""
    with get_connection() as conn:
        return conn.execute("DELETE FROM pantry_items WHERE id = ?", (item_id,)).rowcount > 0


if __name__ == "__main__":
    summary = get_pantry_summary()
    print(f"Total stok: {len(summary['all'])} | Segera olah: {len(summary['expiring_soon'])}")
    for item in summary["all"]:
        print(
            f"- {item['item_name']} ({item['quantity']} {item['unit']}) "
            f"-> {item['label']} ({item['days_left']} hari)"
        )