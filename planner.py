import random
import re
from collections import Counter, defaultdict
from datetime import date
from itertools import zip_longest

from database import clean_text, get_connection
from pantry import list_pantry
from recommender import (
    STAPLES,
    build_matchers,
    build_result,
    core_ingredients,
    load_index,
    recommend_from_pantry,
    split_ingredients,
)

DAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
MAX_PER_CATEGORY = 2
PANTRY_CANDIDATES = 25

EXTRA_STOPWORDS = {
    "sckpnya", "bh", "btg", "lbr", "btr", "grm", "kilo", "ons",
    "pottong", "ptg", "bks", "bgks", "sachet", "sch", "plastik",
    "diiris", "gprek", "geprak", "digeprek", "keprek",
    "papan", "kotak", "balok", "lonjor", "genggam", "biji", "pcs", "bulatan",
    "jari", "tangkai", "kuntum", "helai", "keping", "sedikit", "ukuran", "seujung",
    "seiris", "jempol", "penuh", "setengah", "satu", "dua", "tiga", "empat", "lima",
}

SHOPPING_STAPLES = STAPLES | {
    "garem", "mecin", "vetsin", "sasa", "ajinomoto", "miwon", "totole",
    "royko", "knorr", "magic", "lezat",
}

WORD_ALIASES = {
    "cabai": "cabe",
    "cili": "cabe",
    "saos": "saus",
    "sereh": "serai",
    "laos": "lengkuas",
    "laja": "lengkuas",
    "telor": "telur",
    "bombai": "bombay",
    "toge": "tauge",
    "kecambah": "tauge",
    "sledri": "seledri",
    "kmangi": "kemangi",
    "santal": "santan",
    "putin": "putih",
    "merch": "merah",
    "mersh": "merah",
    "bamer": "bawang merah",
    "baput": "bawang putih",
    "gulmer": "gula merah",
    "prey": "prei",
}

EXACT_PHRASE_ALIASES = {
    "bawah putih": "bawang putih",
    "bawah merah": "bawang merah",
    "bawang daun": "daun bawang",
    "bawang prei": "daun bawang",
    "daun prei": "daun bawang",
    "daun onclang": "daun bawang",
    "daun solong": "daun bawang",
    "daun serai": "serai",
    "daun kol": "kol",
    "fillet ayam": "ayam fillet",
    "daging ayam": "ayam",
    "telur ayam": "telur",
    "tomat merah": "tomat",
    "santan kental": "santan",
    "santan kelapa": "santan",
    "kemangi": "daun kemangi",
    "kecap bango": "kecap manis",
    "terigu": "tepung terigu",
    "maizena": "tepung maizena",
    "tapioka": "tepung tapioka",
    "kanji": "tepung tapioka",
    "sagu": "tepung sagu",
    "salam": "daun salam",
    "salam koja": "daun kari",
    "saori tiram": "saus tiram",
    "sun kara": "santan instan",
    "santan kara": "santan instan",
}

NOISE_TOKENS = {"bahan", "bumbu", "pelengkap", "pelapis", "olesan", "taburan", "saus", "kuah"}
AMBIGUOUS_SINGLE_WORDS = {"bawang", "kulit", "daun", "daging", "tepung", "saus"}

NON_INGREDIENTS = {
    # Kata kerja masak & persiapan
    "tiriskan", "goreng", "menggoreng", "digoreng", "rebus", "merebus", "direbus",
    "kukus", "mengukus", "dikukus", "panggang", "dipanggang", "tumis", "menumis", "ditumis",
    "bakar", "dibakar", "sangrai", "disangrai", "ungkep", "ukep", "presto", "dipresto",
    "rendam", "direndam", "aduk", "diaduk", "campur", "campurkan", "dicampur",
    "cuci", "dicuci", "bersih", "bersihkan", "dibersihkan", "kupas", "dikupas",
    "potong", "dipotong", "cincang", "dicincang", "haluskan", "dihaluskan",
    "ulek", "diulek", "giling", "digiling", "blender", "diblender", "tumbuk", "ditumbuk",
    "rajang", "dirajang", "suwir", "disuwir", "belah", "dibelah", "buang", "dibuang",
    "memarkan", "dimemarkan", "parut", "diparut", "peras", "diperas", "saring", "disaring",
    "kocok", "dikocok", "larutkan", "cairkan", "lelehkan", "panaskan", "dinginkan",
    "diamkan", "sisihkan", "sajikan", "hidangkan", "masukkan", "masukan", "tambahkan",
    "siapkan", "buat", "bikin", "pakai", "pake", "gunakan", "ganti", "diganti",
    "sobek", "disobek", "ambil", "diambil", "lepas", "dadar", "ceplok", "orak", "arik",
    # Kata sifat bentuk, kondisi & konteks masakan
    "dadu", "kasar", "tipis", "tebal", "memanjang", "serong", "utuh", "segar",
    "kering", "basah", "matang", "mentah", "muda", "tua", "cina", "sedang",
    "kecil", "besar", "lemes", "lembut", "empuk", "renyah", "krispi", "crispy",
    "rawon", "soto", "opor", "rendang", "balado", "geprek", "penyet", "korek",
    "siap", "bagian", "bgian", "negeri", "tanpa", "garnish", "hiasan", "tabur", "pewarna",
    # Bagian non-konsumsi
    "kepala", "ekor", "duri", "sisik", "insang", "putihnya", "kuningnya", "airnya", "santannya",
    # Kata sambung, keterangan & noise resep
    "pelengkap", "bahan", "bumbu", "saus", "olesan", "taburan", "celupan",
    "larutan", "isian", "topping", "air", "es", "kuah", "rebusan",
    "lain", "lainnya", "dll", "dsb", "sesuai", "selera", "opsional", "optional",
    "atau", "ataupun", "dan", "serta", "dengan", "dgn", "untuk", "tuk", "utk",
    "dari", "yang", "yg", "ini", "itu", "nya", "saja", "aja", "doang",
    "supaya", "agar", "biar", "hingga", "sampai", "smpe", "smpai", "lalu", "lau",
    "kemudian", "boleh", "bisa", "dapat", "jangan", "lupa", "harus", "wajib",
    "kalo", "kalau", "klo", "jika", "bila", "misal", "misalnya", "karena", "krn",
    "tapi", "tetapi", "lebi", "lebih", "banyak", "cukup", "kurleb", "sekitar", "kira",
    "rasa", "koreksi", "tes", "sdh", "sudah", "telah", "belum", "blm", "ada",
    "aku", "saya", "beli", "ribu", "suka", "ingin", "pedas", "pedes", "juga",
}

FOOD_HEADS = {
    # Sayur, umbi & buah masak
    "bawang", "cabe", "rawit", "tomat", "kentang", "wortel", "kol", "kubis", "sawi",
    "bayam", "kangkung", "timun", "mentimun", "terong", "labu", "jagung", "kacang",
    "buncis", "tauge", "pare", "oyong", "brokoli", "kembang", "jamur", "lobak",
    "bunga", "pepaya", "nanas", "pisang", "belimbing", "mangga", "apel", "jeruk", "lemon",
    "paprika", "pete", "petai", "jengkol", "melinjo", "rebung", "selada", "pakcoy", "pokcoy",
    # Protein hewani & nabati
    "ayam", "telur", "tahu", "tempe", "ikan", "udang", "sapi", "kambing", "bebek",
    "daging", "hati", "ampela", "ceker", "sayap", "dada", "paha", "fillet",
    "iga", "buntut", "tetelan", "kikil", "paru", "babat", "lidah",
    "cumi", "kerang", "kepiting", "tongkol", "gurame", "nila", "tuna", "patin",
    "gabus", "bandeng", "tenggiri", "kembung", "pindang", "lele", "bawal", "kakap", "salmon",
    "bakso", "baso", "sosis", "nugget", "kornet", "ham", "ebi", "teri", "rebon",
    # Tepung, karbo, mie & kulit
    "tepung", "terigu", "maizena", "tapioka", "sagu", "kanji", "panir",
    "beras", "nasi", "mie", "bihun", "soun", "kwetiau", "makaroni", "spaghetti", "roti",
    "kulit", "lumpia", "pangsit",
    # Bumbu, rempah, saus & dairy
    "kecap", "saus", "santan", "kelapa", "ketumbar", "kunyit", "jahe",
    "lengkuas", "serai", "daun", "salam", "kemiri", "asam",
    "seledri", "kencur", "pala", "cengkeh", "kapulaga", "jinten", "wijen", "kayu",
    "pandan", "kemangi", "kucai", "kari", "terasi", "petis", "tauco", "taousi",
    "keju", "susu", "mentega", "margarin", "butter", "cream", "yogurt",
    "gula", "olive", "oil", "samin", "baking", "ragi", "coklat", "vanili",
}


def _normalize_shopping_keys(raw):
    """Memecah dan menstandarkan satu baris bahan menjadi daftar nama bahan belanja bersih."""
    subparts = re.split(r",|;|\bdan\b|\bataupun\b|\batau\b|&|\+", str(raw), flags=re.IGNORECASE)
    results = []
    for part in subparts:
        raw_words = [w for w in clean_text(part).split() if w not in EXTRA_STOPWORDS]
        if not raw_words:
            continue

        words = []
        for w in raw_words:
            mapped = WORD_ALIASES.get(w, w)
            words.extend(mapped.split())

        if not words or words[0] == "air" or not SHOPPING_STAPLES.isdisjoint(words):
            continue
        if words[0] == "gula" and (len(words) == 1 or words[1] in {"pasir", "putih", "halus"}):
            continue
        if words[0] == "minyak" and (len(words) == 1 or words[1] in {"goreng", "sayur", "kelapa"}):
            continue
        if len(words) <= 2 and words[0] in NOISE_TOKENS:
            continue

        filtered = [w for w in words if w not in NON_INGREDIENTS]
        if not filtered:
            continue

        # Konsolidasi semua variasi cabe & rawit
        if "cabe" in filtered or "rawit" in filtered:
            if "rawit" in filtered:
                filtered = ["cabe", "rawit"]
            elif "hijau" in filtered or "ijo" in filtered:
                filtered = ["cabe", "hijau"]
            else:
                filtered = ["cabe", "merah"]
        else:
            if not any(w in FOOD_HEADS for w in filtered):
                continue

            head_idx = next((i for i, w in enumerate(filtered) if w in FOOD_HEADS), None)
            if head_idx is not None and head_idx > 0:
                filtered = filtered[head_idx:]
            if len(filtered) > 2:
                filtered = filtered[:2]

        dedup = []
        for w in filtered:
            if not dedup or dedup[-1] != w:
                dedup.append(w)

        key = " ".join(dedup).strip()
        key = EXACT_PHRASE_ALIASES.get(key, key)
        if len(key) >= 3 and key not in AMBIGUOUS_SINGLE_WORDS:
            results.append(key)
    return results


def _aggregate_shopping_list(missing_lists):
    """Menggabungkan bahan kurang dan melebur kata umum jika versi spesifik sudah ada."""
    grouped = {}
    for missing in missing_lists:
        counted = set()
        for raw in missing:
            for key in _normalize_shopping_keys(raw):
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

    p2 = plan_for_shopping(cluster="Menu Sederhana", seed=42)
    print("\n=== ALUR 2: MENU 7 HARI (MENU SEDERHANA) -> DAFTAR BELANJA ===")
    for d in p2["days"]:
        print(f"- {d['day_name']:6s}: {d['recipe']['title']} [{d['recipe']['category']}]")
    print(f"Total belanja: {len(p2['shopping_list'])} item")
    top_shop = ", ".join(f"{x['name']} ({x['recipe_count']}x)" for x in p2["shopping_list"][:5])
    print(f"Contoh item belanja: {top_shop}")