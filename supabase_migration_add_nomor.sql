-- Jalankan sekali di Supabase SQL Editor sebelum memakai input lead production.
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS nomor text;
