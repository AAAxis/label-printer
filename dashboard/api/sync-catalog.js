import {query} from './db.js';
import {equal} from './session.js';
// Daily Vercel Cron: copies the SUMIT catalog (via the Make webhook) into Neon, same rules as cloud/sync_catalog.py.
// A failure keeps the existing catalog and alerts Dima by email.

async function page(startIndex,mode){
  const r=await fetch(process.env.SUMIT_WEBHOOK_URL,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:process.env.SUMIT_WEBHOOK_TOKEN,startIndex,...(mode?{mode}:{})}),signal:AbortSignal.timeout(25000)});
  if(!r.ok)throw Error('SUMIT webhook HTTP '+r.status);
  return r.json();
}

export async function collect(fetchPage=page){
  const products=new Map(),ambiguous=new Set();let skipped=0,start=0;
  for(let i=0;i<1000;i++){
    const res=await fetchPage(start);
    if(![0,'Success','Success (0)'].includes(res?.Status))throw Error('SUMIT rejected the product request');
    const items=res?.Data?.IncomeItems;
    if(!Array.isArray(items))throw Error('Invalid SUMIT product response');
    for(const it of items){
      if(!it.SKU){skipped++;continue}
      if(typeof it.SKU!=='string'||typeof it.Name!=='string'||!it.SKU.trim()||!it.Name.trim())throw Error('Invalid SKU or name in SUMIT');
      const sku=it.SKU.trim();
      if(ambiguous.has(sku))continue;
      if(products.has(sku)){products.delete(sku);ambiguous.add(sku);continue}
      products.set(sku,it.Name.trim());
    }
    if(!res.Data.HasNextPage){
      if(!products.size)throw Error('SUMIT returned no products with SKUs');
      return {products,skipped,ambiguous:[...ambiguous]};
    }
    if(!items.length)throw Error('SUMIT pagination did not advance');
    start+=items.length;
  }
  throw Error('SUMIT pagination limit reached');
}

const cardValue=v=>{if(Array.isArray(v))v=v.find(x=>typeof x==='string'&&x.trim())||'';return typeof v==='string'?v.split(/\s+/).filter(Boolean).join(' '):''};

// SKU -> supplier from the SUMIT CRM product cards (property "ספק"). Conflicting cards for one SKU give no supplier.
export async function collectSuppliers(fetchPage=start=>page(start,'cards')){
  const suppliers=new Map(),conflicting=new Set();let start=0;
  for(let i=0;i<1000;i++){
    const res=await fetchPage(start),cards=res?.Data?.Entities;
    if(![0,'Success','Success (0)'].includes(res?.Status)||!Array.isArray(cards))throw Error('Invalid SUMIT product-card response');
    for(const c of cards){
      const sku=cardValue(c.Accounting_SKU),supplier=cardValue(c['ספק']);
      if(!sku||!supplier||conflicting.has(sku))continue;
      if(suppliers.has(sku)&&suppliers.get(sku)!==supplier){suppliers.delete(sku);conflicting.add(sku);continue}
      suppliers.set(sku,supplier.slice(0,120));
    }
    if(!res.Data.HasNextPage)return suppliers;
    if(!cards.length)throw Error('SUMIT product-card pagination did not advance');
    start+=cards.length;
  }
  throw Error('SUMIT product-card pagination limit reached');
}

export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(!process.env.CRON_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.CRON_SECRET))return res.status(401).end();
  try{
    // Suppliers are optional: if the product cards fail, names still refresh and stored suppliers are kept.
    const [{products,skipped,ambiguous},suppliers]=await Promise.all([collect(),collectSuppliers().catch(()=>null)]);
    const skus=[...products.keys()],names=[...products.values()],sups=skus.map(s=>suppliers?.get(s)??null);
    await query(`insert into public.print_products(sku,name,supplier,active,synced_at)
      select s,n,p,true,now() from unnest($1::text[],$2::text[],$3::text[]) as t(s,n,p)
      on conflict(sku) do update set name=excluded.name,active=true,synced_at=now(),
      supplier=case when $4 then excluded.supplier else print_products.supplier end`,[skus,names,sups,suppliers!==null]);
    await query('update public.print_products set active=false where active and not (sku=any($1::text[]))',[skus]);
    return res.json({synced:skus.length,withSupplier:sups.filter(Boolean).length,suppliersLoaded:suppliers!==null,skipped,ambiguous});
  }catch(e){
    await fetch('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(6000),
      headers:{Authorization:'Bearer '+process.env.RESEND_API_KEY,'Content-Type':'application/json','Idempotency-Key':'catalog-sync-failed-'+new Date().toISOString().slice(0,10)},
      body:JSON.stringify({from:'Gorilla Automation <sku-bot@montigate.com>',to:['dima@holylabs.net'],subject:'ERROR — catalog sync',
        text:`סנכרון קטלוג SUMIT היומי נכשל — הקטלוג הקודם נשמר. ${String(e.message).slice(0,200)}`})}).catch(()=>{});
    return res.status(502).json({error:'Catalog sync failed; existing catalog preserved'});
  }
}
