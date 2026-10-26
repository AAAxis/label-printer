import {queueData} from '../lib/queue-data.js';
import {authorized} from './session.js';
import {query} from '../lib/db.js';
export default async function handler(req,res){res.setHeader('Cache-Control','no-store');if(req.method!=='GET')return res.status(405).end();if(!authorized(req))return res.status(401).json({error:'Sign in required'});try{
const [printers,jobs,products,orders,shiftReports]=await Promise.all([query('select id,name,machine_name,device_name,last_heartbeat,status,last_error,telemetry from public.print_printers order by created_at'),query('select id,sku,product_name,printer_id,status,created_at,error from public.print_jobs order by created_at desc limit 200'),query('select sku,name,synced_at from public.print_products where active order by sku'),query('select gmail_id,subject,sender,customer,document_id,total_lines,matched_lines,unmatched,created_at from public.print_orders order by created_at desc limit 50').catch(()=>[]),query('select id,shift_start,workers,status,attempts,jsonb_array_length(orders) as orders,created_at,printed_at from public.shift_reports order by shift_start desc limit 10').catch(()=>[])]);
res.json({printers,products,orders,shiftReports,now:new Date().toISOString(),...queueData(printers,jobs)});
}catch{return res.status(503).json({error:'Could not load database. Try refreshing.'})}}
