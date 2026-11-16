from datetime import date

from database import get_connection

EXPIRING_SOON_DAYS = 3


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


if __name__ == "__main__":
    summary = get_pantry_summary()
    print(f"Total stok: {len(summary['all'])} | Segera olah: {len(summary['expiring_soon'])}")
    for item in summary["all"]:
        print(
            f"- {item['item_name']} ({item['quantity']} {item['unit']}) "
            f"-> {item['label']} ({item['days_left']} hari)"
        )