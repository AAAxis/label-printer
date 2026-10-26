import {query} from '../lib/db.js';
import {equal} from './session.js';
// Make reports each processed order here: which lines were NOT entered into the SUMIT draft.
// notify.js adds them to the ✅/⚠️ email; the dashboard lists them per order.
function lines(raw){
  let list=raw;
  if(typeof list==='string'){try{list=JSON.parse(list)}catch{list=[]}}
  if(!Array.isArray(list))return [];
  return list.slice(0,200).map(l=>({ean:String(l?.ean??'').slice(0,64),name:String(l?.name??'').slice(0,300),qty:Number(l?.qty)||0}));
}
export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(req.method!=='POST')return res.status(405).end();
  if(!process.env.ORDER_REPORT_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.ORDER_REPORT_SECRET))return res.status(401).end();
  let b=req.body;if(typeof b==='string'){try{b=JSON.parse(b)}catch{return res.status(400).end()}}
  if(!b?.gmail_id||!b?.subject)return res.status(400).json({error:'gmail_id and subject required'});
  await query(`insert into public.print_orders(gmail_id,subject,sender,customer,document_id,total_lines,matched_lines,unmatched)
    values($1,$2,$3,$4,$5,$6,$7,$8::jsonb) on conflict(gmail_id) do update set subject=excluded.subject,sender=excluded.sender,
    customer=excluded.customer,document_id=excluded.document_id,total_lines=excluded.total_lines,matched_lines=excluded.matched_lines,
    unmatched=excluded.unmatched,created_at=now()`,
    [String(b.gmail_id),String(b.subject).slice(0,500),b.sender??null,b.customer??null,b.document_id?String(b.document_id):null,
     Number(b.total_lines)||0,Number(b.matched_lines)||0,JSON.stringify(lines(b.unmatched))]);
  return res.json({ok:true});
}
