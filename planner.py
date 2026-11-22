import random
from collections import Counter, defaultdict
from datetime import date
from itertools import zip_longest

from database import clean_text, get_connection
from pantry import list_pantry
from recommender import (
    build_matchers,
    build_result,
    core_ingredients,
    load_index,
    recommend_from_pantry,
    split_ingredients,
)

DAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
NOISE_TOKENS = {"bahan", "bumbu", "pelengkap", "pelapis", "olesan", "taburan", "saus", "kuah"}
MAX_PER_CATEGORY = 2
PANTRY_CANDIDATES = 25
NON_INGREDIENTS = {
    "tiriskan", "goreng", "rebus", "kukus", "panggang", "tumis", "bakar",
    "rendam", "aduk", "cuci", "kupas", "potong", "cincang", "haluskan",
    "sajikan", "pelengkap", "bahan", "bumbu", "saus", "olesan", "taburan",
    "celupan", "larutan", "isian", "topping", "air", "es",
}


def _normalize_shopping_key(raw):
    """Membersihkan teks bahan kurang dari kata kerja masak & duplikat berurutan."""
    words = clean_text(raw).split()
    if not words or words[0] == "air":
        return ""
    if len(words) <= 2 and words[0] in NOISE_TOKENS:
        return ""
    filtered = [w for w in words if w not in NON_INGREDIENTS]
    dedup = []
    for w in filtered:
        if not dedup or dedup[-1] != w:
            dedup.append(w)
    key = " ".join(dedup).strip()
    return key if len(key) >= 3 else ""


def _aggregate_shopping_list(missing_lists):
    """Menggabungkan bahan kurang dan melebur kata umum jika versi spesifik sudah ada."""
    grouped = {}
    for missing in missing_lists:
        counted = set()
        for raw in missing:
            key = _normalize_shopping_key(raw)
            if not key:
                continue
            entry = grouped.setdefault(
                key, {"name": key.title(), "examples": [], "recipe_count": 0}
            )
            if key not in counted:
                counted.add(key)
                entry["recipe_count"] += 1
            if raw not in entry["examples"] and len(entry["examples"]) < 2:
                entry["examples"].append(raw)

    all_keys = set(grouped)
    merged = {}
    for key, entry in grouped.items():
        if " " not in key:
            specifics = [k for k in all_keys if k != key and key in k.split()]
            if specifics:
                for sp in specifics:
                    grouped[sp]["recipe_count"] += entry["recipe_count"]
                continue
        merged[key] = entry

    return sorted(merged.values(), key=lambda x: (-x["recipe_count"], x["name"]))



def _pick_diverse(candidates, count):
    """Memilih resep dengan batas per kategori; kelebihan dipakai hanya jika masih kurang."""
    selected, spill, counts = [], [], Counter()
    for rec in candidates:
        if counts[rec["category"]] < MAX_PER_CATEGORY:
            selected.append(rec)
            counts[rec["category"]] += 1
        else:
            spill.append(rec)
    return (selected + spill)[:count]


def _save_plan(plan_type, days):
    """Menyimpan jadwal menu 7 hari terbaru ke tabel weekly_plans."""
    today = str(date.today())
    rows = [
        (
            plan_type,
            d["day_name"],
            d["recipe"]["id"],
            d["recipe"]["title"],
            ", ".join(d["recipe"]["missing_ingredients"]),
            today,
        )
        for d in days
    ]
    with get_connection() as conn:
        conn.execute("DELETE FROM weekly_plans WHERE plan_type = ?", (plan_type,))
        conn.executemany(
            """
            INSERT INTO weekly_plans
                (plan_type, day_name, recipe_id, recipe_title, missing_items, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _build_plan_payload(plan_type, days):
    """Menyusun paket respons jadwal mingguan beserta daftar belanja dan total biaya."""
    recipes = [d["recipe"] for d in days]
    return {
        "plan_type": plan_type,
        "days": days,
        "shopping_list": _aggregate_shopping_list([r["missing_ingredients"] for r in recipes]),
    }


def _make_plan(plan_type, recipes):
    """Memasangkan resep dengan hari, menyimpannya, lalu menyusun paket respons."""
    days = [{"day_name": day, "recipe": rec} for day, rec in zip(DAYS, recipes)]
    _save_plan(plan_type, days)
    return _build_plan_payload(plan_type, days)


def plan_from_pantry(cluster=None, ref_date=None):
    """Alur 1: Menyusun jadwal menu 7 hari berdasarkan stok aktif di kulkas."""
    candidates = recommend_from_pantry(limit=PANTRY_CANDIDATES, cluster=cluster, ref_date=ref_date)
    chosen = _pick_diverse(candidates, len(DAYS))
    chosen.sort(key=lambda r: not r["uses_expiring"])  # bahan kuning dimasak di awal minggu
    return _make_plan("from_pantry", chosen)


def plan_for_shopping(cluster=None, seed=None, ref_date=None):
    """Alur 2: Membuat paket menu 7 hari dulu, lalu merangkum daftar belanja & biayanya."""
    recipes, _, _ = load_index()
    pantry_names = [i["item_name"] for i in list_pantry(ref_date) if i["status"] != "expired"]
    phrases, tokens = build_matchers(pantry_names)

    by_category = defaultdict(list)
    for rec in recipes:
        if not cluster or rec["cluster_label"].lower() == cluster.lower():
            by_category[rec["category"]].append(rec)

    rng = random.Random(seed)
    groups = list(by_category.values())
    rng.shuffle(groups)
    for group in groups:
        rng.shuffle(group)

    # Ambil bergiliran dari tiap kategori supaya menu seminggu bervariasi.
    chosen, seen_titles = [], set()
    for rec in (r for batch in zip_longest(*groups) for r in batch if r):
        title = rec["title"].strip().lower()
        if title not in seen_titles:
            seen_titles.add(title)
            chosen.append(rec)
            if len(chosen) == len(DAYS):
                break

    results = [
        build_result(rec, *split_ingredients(rec["ingredients_raw"], phrases, tokens))
        for rec in chosen
    ]
    return _make_plan("for_shopping", results)


def get_weekly_plan(plan_type="from_pantry"):
    """Mengambil jadwal terakhir dari database (uses_expiring tidak ikut tersimpan)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT w.day_name, w.missing_items, r.*
            FROM weekly_plans w
            JOIN recipes r ON w.recipe_id = r.id
            WHERE w.plan_type = ?
            ORDER BY w.id ASC
            """,
            (plan_type,),
        ).fetchall()

    days = []
    for row in rows:
        missing = [m.strip() for m in row["missing_items"].split(",") if m.strip()]
        matched = [i for i in core_ingredients(row["ingredients_raw"]) if i not in missing]
        days.append({"day_name": row["day_name"], "recipe": build_result(row, matched, missing)})
    return _build_plan_payload(plan_type, days)


if __name__ == "__main__":
    p1 = plan_from_pantry()
    print("=== ALUR 1: STOK -> MENU 7 HARI ===")
    for d in p1["days"]:
        print(f"- {d['day_name']:6s}: {d['recipe']['title']} ({d['recipe']['match_percent']}%)")
    print(f"Pelengkap dibeli: {len(p1['shopping_list'])} item")

    p2 = plan_for_shopping(cluster="Menu Hemat", seed=42)
    print("\n=== ALUR 2: MENU 7 HARI (MENU HEMAT) -> DAFTAR BELANJA ===")
    for d in p2["days"]:
        print(f"- {d['day_name']:6s}: {d['recipe']['title']} [{d['recipe']['category']}]")
    print(f"Total belanja: {len(p2['shopping_list'])} item")
    top_shop = ", ".join(f"{x['name']} ({x['recipe_count']}x)" for x in p2["shopping_list"][:5])
    print(f"Contoh item belanja: {top_shop}")