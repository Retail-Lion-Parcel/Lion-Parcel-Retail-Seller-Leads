-- =============================================================
-- Migrasi Skema: Target Frekuensi Kunjungan Lead & Kuota Harian Sales
-- =============================================================
-- Fitur:
-- 1. Setiap lead dapat ditentukan target berapa sering dikunjungi
--    (jumlah kunjungan per bulan, misal 4x/bulan = tiap minggu sekali).
-- 2. Setiap sales dapat diberikan kuota berapa kali visit dalam sehari,
--    beserta tanggalnya masing-masin (misal Sales A: 15 visit/hari).
-- 3. Sistem merekomendasikan leads yang dapat dikunjungi beserta
--    usulan tanggal kunjungannya (disebar tiap minggu sekali).
--
-- Jalankan di SQL Editor Supabase, lalu restart aplikasi.

-- -------------------------------------------------------------
-- 1. Kolom target kunjungan per bulan pada tabel leads
-- -------------------------------------------------------------
ALTER TABLE public.leads
  ADD COLUMN IF NOT EXISTS visit_target_per_month INT NOT NULL DEFAULT 1;

COMMENT ON COLUMN public.leads.visit_target_per_month IS
  'Target frekuensi kunjungan per bulan (misal 4 = tiap minggu sekali)';

-- -------------------------------------------------------------
-- 2. Tabel kuota kunjungan harian per sales
--    (berapa kali visit yang boleh dilakukan sales pada tanggal tertentu)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sales_visit_quotas (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sales_id    UUID NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
  quota_date  DATE NOT NULL,
  max_visits  INT  NOT NULL DEFAULT 1,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sales_id, quota_date)
);

COMMENT ON TABLE public.sales_visit_quotas IS
  'Kuota visit harian per sales: berapa kali kunjungan pada tanggal tertentu';

CREATE INDEX IF NOT EXISTS idx_sales_visit_quotas_date
  ON public.sales_visit_quotas (quota_date);

CREATE INDEX IF NOT EXISTS idx_sales_visit_quotas_sales
  ON public.sales_visit_quotas (sales_id);

-- -------------------------------------------------------------
-- 3. Row Level Security (opsional)
--    Aplikasi memakai service role key yang selalu melewati RLS,
--    kebijakan ini hanya jika tabel diakses langsung oleh klien anon.
-- -------------------------------------------------------------
ALTER TABLE public.sales_visit_quotas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sales_visit_quotas_read" ON public.sales_visit_quotas;
CREATE POLICY "sales_visit_quotas_read"
  ON public.sales_visit_quotas FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "sales_visit_quotas_write" ON public.sales_visit_quotas;
CREATE POLICY "sales_visit_quotas_write"
  ON public.sales_visit_quotas FOR ALL
  TO authenticated
  USING (true)
  WITH CHECK (true);
