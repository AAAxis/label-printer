import {query} from './db.js';
import {equal} from './session.js';
// Daily Vercel Cron: copies the SUMIT catalog (via the Make webhook) into Neon, same rules as cloud/sync_catalog.py.
// A failure keeps the existing catalog and alerts by WhatsApp.
const WHATSAPP='https://n8n.tigermedia.co.il/webhook/tg-new-order';

async function page(startIndex){
  const r=await fetch(process.env.SUMIT_WEBHOOK_URL,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:process.env.SUMIT_WEBHOOK_TOKEN,startIndex}),signal:AbortSignal.timeout(25000)});
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

export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(!process.env.CRON_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.CRON_SECRET))return res.status(401).end();
  try{
    const {products,skipped,ambiguous}=await collect();
    const skus=[...products.keys()],names=[...products.values()];
    await query(`insert into public.print_products(sku,name,active,synced_at)
      select s,n,true,now() from unnest($1::text[],$2::text[]) as t(s,n)
      on conflict(sku) do update set name=excluded.name,active=true,synced_at=now()`,[skus,names]);
    await query('update public.print_products set active=false where active and not (sku=any($1::text[]))',[skus]);
    return res.json({synced:skus.length,skipped,ambiguous});
  }catch(e){
    await fetch(WHATSAPP,{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(6000),body:JSON.stringify({
      first_name:'Automation ERROR',last_name:'Catalog sync',name:`סנכרון קטלוג SUMIT היומי נכשל — הקטלוג הקודם נשמר. ${String(e.message).slice(0,200)}`,
      phone:'+972515473526',email:'dima@holylabs.net',order_number:'ERROR — catalog sync',order_id:'catalog-sync',store:'Gorilla'})}).catch(()=>{});
    return res.status(502).json({error:'Catalog sync failed; existing catalog preserved'});
  }
}
