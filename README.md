# KulkasPintar

Aplikasi web asisten dapur pribadi berbasis **Python Flask**, **SQLite**, **NLP (TF-IDF)**, dan **Hybrid Chatbot** untuk mencatat stok bahan makanan, memantau masa kedaluwarsa, serta merekomendasikan menu harian dan rencana masak mingguan.

## Fitur Utama
- Pencatat & pemantau kesegaran stok kulkas (Hijau, Kuning, Merah)
- Rekomendasi menu harian otomatis berbasis isi kulkas (TF-IDF + Cosine Similarity)
- Perencana menu 7 hari (*Weekly Meal Planner*) & generator daftar belanja
- Chatbot Koki hybrid (*Rule-Based* + Small LLM)
- Prediksi anggaran belanja mingguan (Regresi Linear)