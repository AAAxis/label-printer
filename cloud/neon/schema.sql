-- Canonical schema for the printer service on Neon. Server-side access only:
-- the Windows agent reaches these functions through the dashboard's /api/agent endpoint.
create table if not exists public.print_products (
 sku text primary key check (length(sku)>0),
 name text not null check(length(name)>0),
 active boolean not null default true,
 synced_at timestamptz not null default now()
);
create table if not exists public.print_printers (
 id uuid primary key default gen_random_uuid(),
 name text not null,
 machine_name text,
 device_name text,
 token_hash text unique,
 last_heartbeat timestamptz,
 enabled boolean not null default false,
 status text not null default 'unregistered',
 last_error text,
 created_at timestamptz not null default now(),
 telemetry jsonb not null default '{}'::jsonb
);
create table if not exists public.print_batches (
 id uuid primary key default gen_random_uuid(),
 source text not null,
 source_id text not null,
 sender text,
 status text not null default 'queued' check(status in ('queued','blocked','complete','cancelled')),
 error text,
 created_at timestamptz not null default now(),
 unique(source,source_id)
);
create table if not exists public.print_jobs (
 id uuid primary key default gen_random_uuid(),
 batch_id uuid not null references public.print_batches(id),
 position integer not null check(position>=0),
 printer_id uuid references public.print_printers(id),
 sku text not null,
 product_name text not null,
 status text not null default 'queued' check(status in ('queued','claimed','submitting','submitted','failed','uncertain','cancelled')),
 claim_token uuid,
 lease_until timestamptz,
 attempts integer not null default 0,
 error text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 submitted_at timestamptz,
 unique(batch_id,position)
);
create index if not exists print_jobs_pending on public.print_jobs(created_at,position) where status='queued';
create index if not exists print_jobs_printer on public.print_jobs(printer_id,status);
create index if not exists print_printers_heartbeat on public.print_printers(last_heartbeat);
create table if not exists public.print_events (
 id bigint generated always as identity primary key,
 job_id uuid references public.print_jobs(id),
 printer_id uuid references public.print_printers(id),
 event text not null,
 details jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now()
);
create index if not exists print_events_job on public.print_events(job_id,created_at);

create or replace function public.print_agent_heartbeat(p_token text,p_machine text,p_device text,p_status text,p_error text,p_telemetry jsonb)
returns uuid language plpgsql set search_path=pg_catalog,public as $$
declare result uuid;
begin
 if length(p_token)<32 then raise exception 'Invalid agent token' using errcode='42501'; end if;
 update public.print_printers set last_heartbeat=now(),machine_name=left(p_machine,200),device_name=left(p_device,200),status=left(p_status,80),last_error=left(p_error,500),telemetry=p_telemetry
 where token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex') returning id into result;
 if result is null then raise exception 'Invalid agent token' using errcode='42501'; end if;
 return result;
end $$;

create or replace function public.print_agent_products(p_token text,p_start integer default 0)
returns table(sku text,name text) language plpgsql set search_path=pg_catalog,public as $$
begin
 if length(p_token)<32 or not exists(select 1 from public.print_printers where token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex')) then
 raise exception 'Invalid agent token' using errcode='42501'; end if;
 if p_start<0 then raise exception 'Invalid offset'; end if;
 return query select p.sku,p.name from public.print_products p where p.active order by p.sku offset p_start limit 1000;
end $$;

-- Claims serialize per printer and lock a job so two workers cannot claim it.
create or replace function public.print_claim_job(p_printer uuid)
returns setof public.print_jobs language plpgsql set search_path=public,pg_temp as $$
declare job public.print_jobs;
begin
 perform 1 from public.print_printers where id=p_printer and enabled and last_heartbeat>now()-interval '90 seconds' for update;
 if not found then return; end if;
 -- Expired print attempts need human review; they must never be replayed automatically.
 update public.print_jobs set status='uncertain',error='Agent disconnected during print submission',updated_at=now()
 where printer_id=p_printer and status='submitting' and lease_until<now();
 update public.print_jobs set status='queued',claim_token=null,lease_until=null,updated_at=now()
 where printer_id=p_printer and status='claimed' and lease_until<now();
 if exists(select 1 from public.print_jobs where printer_id=p_printer and status in ('claimed','submitting','uncertain')) then return; end if;
 select j.* into job from public.print_jobs j join public.print_batches b on b.id=j.batch_id
 where j.status='queued' and b.status='queued' and (j.printer_id is null or j.printer_id=p_printer)
 order by j.created_at,j.position for update of j skip locked limit 1;
 if not found then return; end if;
 return query update public.print_jobs set status='claimed',printer_id=p_printer,claim_token=gen_random_uuid(),lease_until=now()+interval '2 minutes',attempts=attempts+1,updated_at=now() where id=job.id returning *;
end $$;
