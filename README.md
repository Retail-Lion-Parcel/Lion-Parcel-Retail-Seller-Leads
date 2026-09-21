# 📦 Lion Parcel - Tanah Abang Leads & Route Management

Sistem Web End-to-End untuk pendataan *leads* potensi kiriman toko-toko Tanah Abang dan *routing visit sales* berdasarkan rekomendasi klaster lokasi.

---

## 🌟 Fitur Utama

1. **Role-Based Access Control (RBAC)**:
   - **Data Entry**: Input data toko & ekspedisi competitor.
   - **Router**: Plotting jadwal sales & algoritma rekomendasi *Blok/Lantai/Los* sejenis.
   - **Sales**: Melihat jadwal visit & update status kunjungan.
   - **Admin**: Manajemen user & hak akses.
2. **Algoritma Recommendation Engine**: Otomatis menyarankan toko di Blok, Lantai, atau Los yang sama saat Router melakukan plotting visit.
3. **Google Sheets Sync**: Integrasi otomatis *Google Sheets API* untuk mengkloning data Leads dan Routing Sales secara real-time.
4. **Dashboard Analytics**: Statistik total potensi tonase, penggunaan kurir pesaing, dan progress visit.

---

## 🚀 Cara Menjalankan Project

### 1. Pre-requisites
- Python 3.10+
- Akun Supabase (PostgreSQL)
- Google Cloud Service Account Credentials (`credentials.json`) & Spreadsheet ID.

### 2. Install Dependencies
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

pip install -r requirements.txt

Jika akun Supabase `admin` sudah ada tetapi passwordnya lupa, reset dari Supabase SQL Editor (aktifkan extension `pgcrypto` bila diperlukan):

```sql
create extension if not exists pgcrypto;
update public.users
set password_hash = crypt('PasswordBaruYangKuat', gen_salt('bf'))
where username = 'admin';
```

Login menerima `username` legacy (`admin`) maupun email. Jangan menyimpan password asli di source code.