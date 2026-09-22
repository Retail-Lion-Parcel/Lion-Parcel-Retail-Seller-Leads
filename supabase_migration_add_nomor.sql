-- Jalankan sekali di Supabase SQL Editor sebelum memakai input lead production.
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS nomor text;

-- Field berikut menyimpan beberapa pilihan sekaligus, misalnya beberapa ekspedisi.
ALTER TABLE public.leads
	ALTER COLUMN current_courier TYPE text,
	ALTER COLUMN top_country TYPE text,
	ALTER COLUMN top_city TYPE text;
