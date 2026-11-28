from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from functools import lru_cache
import logging
import os
import re

from database import get_connection
from pantry import add_item, list_pantry
from planner import plan_from_pantry
from recommender import recommend_from_pantry

log = logging.getLogger(__name__)

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
PRIMARY_MODEL = "gemini-3.8-flash"
SKIP_MODEL_TAGS = (
    "image", "audio", "tts", "embed", "live", "veo", "imagen", "robotics",
    "omni", "transcribe", "computer",
)
LLM_TIMEOUT_SECS = 8.0
RETRY_ERRORS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND")
UNITS = (
    "butir|buah|ikat|kg|kilo|gram|gr|ons|liter|ml|"
    "botol|bungkus|bks|papan|potong|ekor|siung|batang|pcs"
)
NUM = r"\d+(?:[.,]\d+)?"
ADD_RE = re.compile(
    r"^(?:tolong\s+)?(?:catat|tambah(?:kan)?|beli|simpan|masuk(?:kan)?)\s+"
    r"(?:beli\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
DAYS_RE = re.compile(
    r"\s+(?:exp|expired|kadaluarsa|kedaluwarsa|tahan)\s+(?P<days>\d+)\s*hari(?:\s+lagi)?$",
    re.IGNORECASE,
)
QTY_FIRST = re.compile(
    rf"^(?P<qty>{NUM})\s*(?:(?P<unit>{UNITS})\b)?\s*(?P<name>.+)$", re.IGNORECASE
)
QTY_LAST = re.compile(rf"^(?P<name>.+?)\s+(?P<qty>{NUM})\s*(?P<unit>{UNITS})?$", re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z ]{1,38}$")
QUESTION_RE = re.compile(
    r"\?|\b(apa|apakah|kapan|mana|bagaimana|gimana|kenapa|mengapa|berapa)\b", re.IGNORECASE
)
NOT_NAME_RE = re.compile(
    r"\b(ke|di|yang|untuk|dengan|dan|atau|biar|supaya|kalau|jika)\b", re.IGNORECASE
)
ADD_HELP = (
    "Maaf, aku belum paham nama bahannya. "
    "Contoh: 'catat 2 ikat bayam' atau 'catat telur 1 kg tahan 5 hari'."
)
PLAN_RE = re.compile(
    r"\b(menu\s+(?:seminggu|7\s*hari|mingguan)|jadwal\s+masak|meal\s+plan|rencana\s+masak)\b",
    re.IGNORECASE,
)
CHECK_RE = re.compile(
    r"\b(cek\s+(?:kulkas|stok|bahan)|isi\s+kulkas|"
    r"hampir\s+(?:habis|kadaluarsa|kedaluwarsa|basi)|segera\s+olah|stok\s+kulkas)\b",
    re.IGNORECASE,
)
COOK_RE = re.compile(r"\b(masak|resep|ide|bikin|olah\s+jadi)\b", re.IGNORECASE)
SUB_RE = re.compile(
    r"\b(ganti(?:nya)?|pengganti|alternatif|tidak\s+ada|ga\s+ada|gak\s+ada|habis|busuk|basi)\b",
    re.IGNORECASE,
)
SUBSTITUTES = {
    "telur": "tahu/tempe (lauk protein), atau 1 sdm maizena/tapioka + air (pengikat adonan)",
    "ayam": "tahu, tempe, jamur tiram, atau telur sebagai sumber protein utama",
    "tahu": "tempe, telur dadar potong, atau jamur tiram",
    "tempe": "tahu putih/kuning, kacang tanah goreng, atau jamur",
    "garam": "kecap asin, saus tiram, kaldu bubuk (Royco/Masako), atau sedikit keju parut",
    "kecap manis": "gula merah/gula pasir cair + sedikit kecap asin atau garam",
    "santan": "susu UHT plain, fiber creme, atau kemiri sangrai halus",
    "bawang bombay": "bawang merah (3-4 siung untuk tiap 1/2 butir bombay)",
    "bawang putih": "bawang putih bubuk (1/4 sdt per siung) atau bawang merah",
    "daun bawang": "seledri, irisan bawang merah, atau kucai",
    "jeruk nipis": "cuka makan (1/2 sdt) atau air asam jawa",
    "saus tiram": "kecap asin + sedikit gula pasir dan kaldu jamur",
    "maizena": "tepung tapioka atau tepung terigu (takaran 1:1)",
    "mentega": "margarin atau minyak goreng/minyak kelapa",
    "margarin": "mentega atau minyak goreng (takaran 3/4 dari margarin)",
    "lada": "merica bubuk, cabai rawit, atau jahe parut sedikit untuk rasa hangat",
    "merica": "lada hitam, cabai bubuk, atau sedikit jahe",
}


def _load_env(path=ENV_PATH):
    """Memuat variabel .env lokal jika belum tersedia di environment."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _parse_add(rest):
    """Memecah isi perintah catat menjadi (jumlah, satuan, nama, hari tahan)."""
    days = 7
    if m := DAYS_RE.search(rest):
        days, rest = int(m["days"]), rest[: m.start()]
    m = QTY_FIRST.match(rest) or QTY_LAST.match(rest)
    qty, unit, name = (m["qty"], m["unit"], m["name"]) if m else ("1", None, rest)
    unit = (unit or "pcs").lower()
    return float(qty.replace(",", ".")), "kg" if unit == "kilo" else unit, name.strip(), days


def _handle_add(text):
    """Rule-based: mencatat stok masuk lewat chat; None jika bukan perintah catat."""
    m = ADD_RE.match(text.rstrip(" .!"))
    if not m or QUESTION_RE.search(text):
        return None
    qty, unit, name, days = _parse_add(m["rest"])
    if qty <= 0 or not NAME_RE.match(name) or NOT_NAME_RE.search(name) or len(name.split()) > 4:
        return ADD_HELP
    name = name.title()
    add_item(name, qty, unit, shelf_life_days=days)
    return f"Siap! Tercatat ke kulkas: {qty:g} {unit} {name} (tahan {days} hari)."


def _handle_check():
    """Rule-based: mengecek isi kulkas dan bahan yang hampir kedaluwarsa."""
    items = list_pantry()
    if not items:
        return "Kulkas masih kosong. Ketik 'catat 5 butir telur' untuk menambah stok."

    expiring = [i for i in items if i["status"] == "expiring_soon"]
    fresh = [i for i in items if i["status"] == "fresh"]
    expired = [i for i in items if i["status"] == "expired"]

    lines = [f"Ringkasan Stok Kulkas ({len(items)} item):"]
    if expiring:
        urg = ", ".join(f"{i['item_name']} ({i['days_left']} hr)" for i in expiring)
        lines.append(f"- Segera Olah: {urg}")
    if fresh:
        ok = ", ".join(f"{i['item_name']} ({i['quantity']:g} {i['unit']})" for i in fresh)
        lines.append(f"- Masih Segar: {ok}")
    if expired:
        lines.append("- Kedaluwarsa: " + ", ".join(i["item_name"] for i in expired))
    return "\n".join(lines)


def _handle_plan():
    """Rule-based: menyusun jadwal masak 7 hari dari stok kulkas via planner.py."""
    plan = plan_from_pantry()
    if not plan["days"]:
        return "Stok kulkas belum cukup untuk menu seminggu. Tambah bahan dulu ya!"
    lines = ["Rekomendasi Jadwal Masak 7 Hari (dari stok kulkasmu):"]
    for d in plan["days"]:
        rec = d["recipe"]
        lines.append(f"- {d['day_name']}: {rec['title']} ({rec['match_percent']}%)")
    lines.append(f"Bahan pelengkap yang perlu dibeli: {len(plan['shopping_list'])} item.")
    return "\n".join(lines)


def get_chat_history(session_id, limit=30):
    """Mengambil riwayat percakapan terakhir untuk satu session_id."""
    if not session_id:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT sender, message, route_used, created_at
            FROM chat_history WHERE session_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _save_chat(session_id, sender, message, route="rule_based"):
    """Menyimpan satu baris percakapan ke tabel chat_history."""
    if not session_id:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO chat_history (session_id, sender, message, route_used, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, sender, message, route, now),
        )


def _kitchen_context():
    """Menyusun konteks stok kulkas & rekomendasi menu untuk Gemini."""
    active = [i for i in list_pantry() if i["status"] != "expired"]
    pantry_txt = ", ".join(
        f"{i['item_name']} ({i['quantity']:g} {i['unit']}, sisa {i['days_left']} hr)"
        for i in active
    ) or "kosong"
    recs = recommend_from_pantry(limit=3) if active else []
    rec_txt = ", ".join(f"{r['title']} ({r['match_percent']}%)" for r in recs) or "belum ada"
    return (
        "Kamu Asisten Koki KulkasPintar yang ramah dan ringkas (maksimal 4 poin singkat). "
        f"Stok kulkas: {pantry_txt}. Rekomendasi terdekat: {rec_txt}. "
        "Jawab langsung pertanyaan pengguna; prioritaskan bahan yang sisa harinya sedikit."
    )


def _local_fallback(user_message):
    """Cadangan lokal jika server Gemini sedang penuh atau lambat."""
    low = user_message.lower()
    for bahan, ganti in SUBSTITUTES.items():
        if bahan in low:
            return f"Sebagai pengganti {bahan}, kamu bisa pakai {ganti}."
    if SUB_RE.search(low):
        return (
            "Gunakan bahan atau bumbu lain di kulkas yang punya fungsi mirip "
            "(misalnya tahu/tempe untuk protein, atau kecap asin/kaldu untuk rasa gurih)."
        )
    recs = recommend_from_pantry(limit=3)
    if recs:
        menu = ", ".join(f"{r['title']} ({r['match_percent']}%)" for r in recs)
        return f"Dari stok kulkasmu sekarang, menu terdekat yang bisa dimasak: {menu}."
    return "Server AI sedang sibuk sementara. Coba 'cek kulkas' atau 'catat [bahan]'."


@lru_cache(maxsize=1)
def _discover_fallbacks(api_key, primary):
    """Mencari model teks Gemini aktif di akun (di-cache agar cukup sekali)."""
    from google import genai

    client = genai.Client(api_key=api_key)
    usable = []
    for m in client.models.list():
        name = (m.name or "").replace("models/", "")
        actions = m.supported_actions or []
        if (
            "generateContent" in actions
            and name.startswith("gemini-")
            and "flash" in name
            and name != primary
            and not any(t in name for t in SKIP_MODEL_TAGS)
        ):
            usable.append(name)
    return tuple(sorted(usable, reverse=True)[:2])


def _generate_with_timeout(client, model, contents, config):
    """Menjalankan panggilan Gemini dengan batas waktu ketat agar tidak menggantung."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(
        client.models.generate_content, model=model, contents=contents, config=config
    )
    try:
        return future.result(timeout=LLM_TIMEOUT_SECS)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _build_contents(types, history, user_message):
    """Mengubah riwayat chat + pesan terbaru menjadi format konten Gemini."""
    contents = [
        types.Content(
            role="user" if h["sender"] == "user" else "model",
            parts=[types.Part.from_text(text=h["message"])],
        )
        for h in (history or [])
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))
    return contents


def _ask_gemini(user_message, history=None):
    """Bertanya ke Gemini beserta riwayat percakapan; mengembalikan (balasan, sumber)."""
    _load_env()
    api_key = (os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        hint = "LLM_API_KEY belum diisi di .env. Coba 'cek kulkas' atau 'catat 3 telur'."
        return hint, "local_fallback"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        hint = "Library google-genai belum terpasang: jalankan 'pip install google-genai'."
        return hint, "local_fallback"

    primary = os.getenv("LLM_MODEL", PRIMARY_MODEL).strip() or PRIMARY_MODEL
    models = [primary]
    try:
        client = genai.Client(api_key=api_key)
        cfg_kwargs = {
            "system_instruction": _kitchen_context(),
            "temperature": 0.6,
            "max_output_tokens": 220,
        }
        if hasattr(types, "ThinkingConfig"):
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        config = types.GenerateContentConfig(**cfg_kwargs)
        contents = _build_contents(types, history, user_message)

        idx = 0
        while idx < len(models):
            model = models[idx]
            idx += 1
            try:
                resp = _generate_with_timeout(client, model, contents, config)
            except FutureTimeout:
                log.warning("Model %s timeout > %.1fs", model, LLM_TIMEOUT_SECS)
                if idx == 1:
                    models.extend(_discover_fallbacks(api_key, primary))
                continue
            except Exception as err:
                log.warning("Model %s gagal: %s", model, err)
                if "thinking" in str(err).lower() and "thinking_config" in cfg_kwargs:
                    cfg_kwargs.pop("thinking_config", None)
                    config = types.GenerateContentConfig(**cfg_kwargs)
                    models.insert(idx, model)
                    continue
                if any(k in str(err) for k in RETRY_ERRORS):
                    if idx == 1:
                        models.extend(_discover_fallbacks(api_key, primary))
                    continue
                break
            if resp.text and resp.text.strip():
                return resp.text.strip(), "gemini"
    except Exception as err:
        log.warning("Gemini tidak bisa dipakai: %s", err)
    return _local_fallback(user_message), "local_fallback"


def ask_chef(message, session_id=None):
    """Router hibrida: rule-based SQLite dulu, lalu Gemini + riwayat chat, lalu lokal."""
    text = str(message).strip()
    if not text:
        return {
            "source": "rule_based",
            "reply": "Ketik ide masak atau perintah 'catat 2 ikat bayam' / 'cek kulkas'.",
        }

    history = get_chat_history(session_id, limit=6)
    if reply := _handle_add(text):
        source = "rule_based"
    elif PLAN_RE.search(text):
        reply, source = _handle_plan(), "rule_based"
    elif CHECK_RE.search(text) and not COOK_RE.search(text):
        reply, source = _handle_check(), "rule_based"
    else:
        reply, source = _ask_gemini(text, history=history)

    _save_chat(session_id, "user", text, source)
    _save_chat(session_id, "bot", reply, source)
    return {"source": source, "reply": reply}


if __name__ == "__main__":
    for sample in [
        "cek kulkas",
        "buatkan menu seminggu dari isi kulkas",
        "Kalau telur saya busuk, gantinya apa ya buat masak?",
    ]:
        res = ask_chef(sample)
        print(f"User : {sample}\nBot  [{res['source']}]:\n{res['reply']}\n")