from collections import Counter
from functools import lru_cache

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler

from database import clean_text, get_connection
from pantry import list_pantry

MODIFIERS = {
    "putih", "merah", "hijau", "hitam", "kuning", "manis", "asin",
    "bubuk", "segar", "kering", "cair", "goreng", "rebus", "bakar",
}
CORE_PROTEINS = {"ayam", "telur", "tahu", "tempe", "ikan", "udang", "sapi", "kambing"}
STAPLES = {
    "garam", "gula", "minyak", "merica", "lada",
    "masako", "micin", "penyedap", "royco", "kaldu",
}
VARIANT_WORDS = {"asin", "puyuh", "bebek"}
KILAT_LABEL = "Masakan Kilat"
KILAT_MAX_MINS = 15
CLUSTER_NAMES = ("Menu Hemat", "Menu Lengkap")
RESULT_FIELDS = (
    "id", "title", "category", "cluster_label", "prep_time_mins", "steps",
)
MIN_SCORE = 0.05
EXPIRING_BONUS = 0.15
CANDIDATE_POOL = 30

ALIASES = {"cabe": "cabai", "telor": "telur", "bombai": "bombay", "jahe": "jahe"}

@lru_cache(maxsize=1)
def load_index():
    """Memuat katalog resep dan membangun matriks TF-IDF (di-cache di memori)."""
    with get_connection() as conn:
        recipes = [dict(r) for r in conn.execute("SELECT * FROM recipes")]
    if not recipes:
        return recipes, None, None
    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2))
    matrix = vectorizer.fit_transform([r["ingredients_clean"] for r in recipes])
    return recipes, vectorizer, matrix


def build_matchers(names):
    """Memisahkan frasa bahan majemuk dan kata inti protein untuk pencocokan."""
    phrases, tokens = set(), set()
    for name in names:
        words = clean_text(name).split()
        if len(words) == 1 and words[0] not in MODIFIERS:
            tokens.add(words[0])
        elif len(words) > 1:
            phrases.add(" ".join(words))
            if words[0] in CORE_PROTEINS:
                tokens.add(words[0])
    return phrases, tokens


def _ingredient_items(raw_str):
    """Memecah bahan resep jadi daftar item tanpa judul seksi (berakhiran ':')."""
    items = (i.strip() for i in raw_str.split(","))
    return [i for i in items if i and not i.endswith(":")]


def _is_staple(item):
    """Mengecek apakah bahan resep termasuk bumbu dasar dapur."""
    words = clean_text(item).split()
    return words == ["air"] or not STAPLES.isdisjoint(words)


def _matches(item, phrases, tokens):
    """Mengecek kecocokan satu bahan resep dengan frasa atau kata inti."""
    cleaned = clean_text(item)
    words = cleaned.split()
    if any(p in cleaned for p in phrases):
        return True
    return not tokens.isdisjoint(words) and VARIANT_WORDS.isdisjoint(words)


def core_ingredients(raw_str):
    """Daftar bahan inti resep: tanpa judul seksi dan tanpa bumbu dasar."""
    return [i for i in _ingredient_items(raw_str) if not _is_staple(i)]


def split_ingredients(raw_str, phrases, tokens):
    """Mengelompokkan bahan inti resep menjadi tersedia dan kurang."""
    matched, missing = [], []
    for item in core_ingredients(raw_str):
        (matched if _matches(item, phrases, tokens) else missing).append(item)
    return matched, missing


def _coverage(matched, missing):
    return len(matched) / max(1, len(matched) + len(missing))


def build_result(rec, matched, missing, uses_exp=False):
    """Menyusun dict hasil satu resep beserta persentase bahan yang tersedia."""
    return {
        **{k: rec[k] for k in RESULT_FIELDS},
        "match_percent": round(100 * _coverage(matched, missing)),
        "uses_expiring": uses_exp,
        "matched_ingredients": matched,
        "missing_ingredients": missing,
    }


def _kmeans_labels(recipes):
    """Mengelompokkan resep dengan K-Means; klaster termurah = Hemat, lainnya = Lengkap."""
    features = np.array(
        [
            [r["prep_time_mins"], r["ingredients_raw"].count(",") + 1]
            for r in recipes
        ],
        dtype=float,
    )
    scaler = StandardScaler()
    km = KMeans(n_clusters=len(CLUSTER_NAMES), random_state=42, n_init=10)
    labels = km.fit_predict(scaler.fit_transform(features))
    simpler_first = np.argsort(scaler.inverse_transform(km.cluster_centers_)[:, 1])
    names = dict(zip(simpler_first.tolist(), CLUSTER_NAMES))
    return [names[int(lbl)] for lbl in labels]


def run_kmeans_clustering():
    """Memberi label menu: Kilat lewat aturan waktu masak, Hemat/Lengkap lewat K-Means."""
    recipes, _, _ = load_index()
    slow = [r for r in recipes if r["prep_time_mins"] > KILAT_MAX_MINS]
    if len(slow) < len(CLUSTER_NAMES):
        return {}

    labels = {r["id"]: KILAT_LABEL for r in recipes if r["prep_time_mins"] <= KILAT_MAX_MINS}
    labels.update(zip((r["id"] for r in slow), _kmeans_labels(slow)))
    with get_connection() as conn:
        conn.executemany(
            "UPDATE recipes SET cluster_label = ? WHERE id = ?",
            [(label, rid) for rid, label in labels.items()],
        )

    load_index.cache_clear()
    return dict(Counter(labels.values()))


def match_recipes(items, limit=5, cluster=None):
    """Mencari kandidat resep via TF-IDF + Cosine Similarity, lalu urutkan per % bahan tersedia."""
    recipes, vectorizer, matrix = load_index()
    items = [i for i in items if not _is_staple(i["name"])]
    weighted = [(clean_text(i["name"]), 2 if i.get("expiring") else 1) for i in items]
    query = " ".join(" ".join([text] * w) for text, w in weighted if text)
    if not recipes or not query:
        return []

    scores = cosine_similarity(vectorizer.transform([query]), matrix).ravel()
    all_phrases, all_tokens = build_matchers([i["name"] for i in items])
    exp_phrases, exp_tokens = build_matchers(
        [i["name"] for i in items if i.get("expiring")]
    )

    ranked = []
    for idx in np.flatnonzero(scores > MIN_SCORE):
        rec = recipes[idx]
        if cluster and rec["cluster_label"].lower() != cluster.lower():
            continue
        uses_exp = any(
            _matches(i, exp_phrases, exp_tokens)
            for i in _ingredient_items(rec["ingredients_raw"])
        )
        score = float(scores[idx]) + (EXPIRING_BONUS if uses_exp else 0.0)
        ranked.append((score, uses_exp, rec))
    ranked.sort(key=lambda x: x[0], reverse=True)

    candidates, seen = [], set()
    for score, uses_exp, rec in ranked:
        title_key = rec["title"].strip().lower()
        if title_key in seen:
            continue
        seen.add(title_key)
        matched, missing = split_ingredients(rec["ingredients_raw"], all_phrases, all_tokens)
        rank = (_coverage(matched, missing) + (EXPIRING_BONUS if uses_exp else 0.0), score)
        candidates.append((rank, build_result(rec, matched, missing, uses_exp)))
        if len(candidates) >= max(limit, CANDIDATE_POOL):
            break

    candidates.sort(key=lambda c: c[0], reverse=True)
    return [result for _, result in candidates[:limit]]


def recommend_from_pantry(limit=5, cluster=None, ref_date=None):
    """Memberikan rekomendasi menu dari stok aktif kulkas (prioritas kuning)."""
    active = [
        {"name": i["item_name"], "expiring": i["status"] == "expiring_soon"}
        for i in list_pantry(ref_date)
        if i["status"] != "expired"
    ]
    return match_recipes(active, limit=limit, cluster=cluster)


def recommend_from_text(ingredients_text, limit=5, cluster=None):
    """Mencocokkan daftar bahan teks bebas (untuk Coba Cepat di Landing Page)."""
    items = [
        {"name": p.strip(), "expiring": False}
        for p in str(ingredients_text).split(",")
        if p.strip()
    ]
    return match_recipes(items, limit=limit, cluster=cluster)


if __name__ == "__main__":
    print("Hasil K-Means Clustering:", run_kmeans_clustering())
    print("\nTop 3 Rekomendasi Menu dari Isi Kulkas:")
    for rec in recommend_from_pantry(limit=3):
        print(f"- {rec['title']} [{rec['cluster_label']}] ({rec['match_percent']}%)")
        print(f"  Tersedia : {', '.join(rec['matched_ingredients'])}")
        print(f"  Kurang   : {', '.join(rec['missing_ingredients'][:4])}")