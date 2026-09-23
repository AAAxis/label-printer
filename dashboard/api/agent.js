import {query} from './db.js';
// The Windows agent authenticates with its own token; the database functions check its hash.
const calls={
 print_agent_products:b=>['select sku,name from public.print_agent_products($1,$2)',[Number.isInteger(b.p_start)?b.p_start:0]],
 print_agent_heartbeat:b=>['select public.print_agent_heartbeat($1,$2,$3,$4,$5,$6) as id',[b.p_machine??null,b.p_device??null,b.p_status??null,b.p_error??null,JSON.stringify(b.p_telemetry??{})]]
};
export default async function handler(req,res){res.setHeader('Cache-Control','no-store');if(req.method!=='POST')return res.status(405).end();
let body=req.body;if(typeof body==='string'){try{body=JSON.parse(body)}catch{return res.status(400).end()}}
const token=(req.headers.authorization||'').replace(/^Bearer /,'');const call=calls[body?.rpc];
if(!call)return res.status(400).json({error:'Unknown call'});if(token.length<32)return res.status(401).json({error:'Invalid agent token'});
const [text,params]=call(body);
try{const rows=await query(text,[token,...params]);return res.json(body.rpc==='print_agent_heartbeat'?rows[0].id:rows)}
catch(e){if(e?.code==='42501')return res.status(401).json({error:'Invalid agent token'});return res.status(503).json({error:'Database unavailable'})}}
