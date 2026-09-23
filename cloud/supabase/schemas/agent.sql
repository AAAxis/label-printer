-- Narrow agent access: the Windows machine never receives a database/service key.
alter table public.print_printers add column if not exists telemetry jsonb not null default '{}'::jsonb;
create or replace function public.print_agent_heartbeat(p_token text,p_machine text,p_device text,p_status text,p_error text,p_telemetry jsonb)
returns uuid language plpgsql security definer set search_path=pg_catalog,public as $$
declare result uuid;
begin
 if length(p_token)<32 then raise exception 'Invalid agent token' using errcode='42501'; end if;
 update public.print_printers set last_heartbeat=now(),machine_name=left(p_machine,200),device_name=left(p_device,200),status=left(p_status,80),last_error=left(p_error,500),telemetry=p_telemetry
 where token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex') returning id into result;
 if result is null then raise exception 'Invalid agent token' using errcode='42501'; end if;
 return result;
end $$;
create or replace function public.print_agent_products(p_token text,p_start integer default 0)
returns table(sku text,name text) language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if length(p_token)<32 or not exists(select 1 from public.print_printers where token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex')) then
 raise exception 'Invalid agent token' using errcode='42501'; end if;
 if p_start<0 then raise exception 'Invalid offset'; end if;
 return query select p.sku,p.name from public.print_products p where p.active order by p.sku offset p_start limit 1000;
end $$;
revoke all on function public.print_agent_heartbeat(text,text,text,text,text,jsonb) from public;
revoke all on function public.print_agent_products(text,integer) from public;
grant execute on function public.print_agent_heartbeat(text,text,text,text,text,jsonb) to anon;
grant execute on function public.print_agent_products(text,integer) to anon;
