import {queueData} from './queue-data.js';
import {authorized} from './session.js';
import {query} from './db.js';
export default async function handler(req,res){res.setHeader('Cache-Control','no-store');if(req.method!=='GET')return res.status(405).end();if(!authorized(req))return res.status(401).json({error:'Sign in required'});try{
const [printers,jobs,products]=await Promise.all([query('select id,name,machine_name,device_name,last_heartbeat,status,last_error,telemetry from public.print_printers order by created_at'),query('select id,sku,product_name,printer_id,status,created_at,error from public.print_jobs order by created_at desc limit 200'),query('select sku,name,synced_at from public.print_products where active order by sku')]);
res.json({printers,products,now:new Date().toISOString(),...queueData(printers,jobs)});
}catch{return res.status(503).json({error:'Could not load database. Try refreshing.'})}}
