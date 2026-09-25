-- Jalankan sekali di Supabase SQL Editor sebelum memakai input lead production.
ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS commodity text;

-- Field berikut menyimpan beberapa pilihan sekaligus, misalnya beberapa ekspedisi.
ALTER TABLE public.leads
	ALTER COLUMN current_courier TYPE text,
	ALTER COLUMN top_country TYPE text,
	ALTER COLUMN top_city TYPE text;

-- Field berikut menyimpan beberapa pilihan sekaligus, misalnya beberapa ekspedisi.
ALTER TABLE public.leads
	ALTER COLUMN current_courier TYPE text,
	ALTER COLUMN top_country TYPE text,
	ALTER COLUMN top_city TYPE text;

-- Target jumlah routing per bulan (format month: YYYY-MM).
CREATE TABLE IF NOT EXISTS public.routing_targets (
	id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
	month text NOT NULL UNIQUE,
	target_count integer NOT NULL DEFAULT 0 CHECK (target_count >= 0),
	updated_by uuid REFERENCES public.users(id),
	created_at timestamptz NOT NULL DEFAULT now()
);
