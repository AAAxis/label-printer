import {randomBytes} from 'crypto';
import {query} from '../lib/db.js';
import {equal,authorized} from './session.js';
import {LEAD_MIN,dayKey,hhmm,ddmm,shiftsFromEvents,selectOrders,shiftTitle} from '../lib/shift-rules.js';
import {renderShiftPdf} from '../lib/shift-pdf.js';
// Warehouse shift report: Make calls this every 15 minutes. Shifts come from Google Calendar and orders from
// both Shopify stores, both through the read-only Make feed. The PDF goes to the Brother through the same
// "Invoice — " email path as SUMIT drafts; the PC's own print report confirms it.
const MIN=60000,CAL_TTL=3*60*MIN,LATE_MIN=60,CONFIRM_MIN=20,MAX_ATTEMPTS=3;
const STORES=['gorilla','raklizrok'];
const MANAGER=()=>(process.env.PRINT_NOTIFY_EMAIL||'gorillagrillshop@gmail.com').split(',');

async function feed(body){
  const r=await fetch(process.env.SHIFT_FEED_URL,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({token:process.env.SHIFT_FEED_TOKEN,...body}),signal:AbortSignal.timeout(25000)});
  const text=await r.text();
  try{return JSON.parse(text)}catch{throw Error(`Make feed (${body.mode}${body.store?' '+body.store:''}) did not answer: ${text.slice(0,80)}`)}
}
async function email(payload,key){
  const r=await fetch('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(15000),
    headers:{Authorization:'Bearer '+process.env.RESEND_API_KEY,'Content-Type':'application/json',...(key?{'Idempotency-Key':key}:{})},
    body:JSON.stringify({from:'Gorilla Label Printer <sku-bot@montigate.com>',...payload})});
  if(!r.ok)throw Error('Resend '+r.status+' '+(await r.text()).slice(0,120));
  return r.json();
}
const notifyManager=(subject,text,key)=>email({to:MANAGER(),bcc:['dima@holylabs.net'],subject,text},key).catch(()=>{});

async function loadShifts(force=false){
  const [c]=await query('select fetched_at,shifts from public.shift_calendar where id=1');
  if(!force&&c&&Date.now()-new Date(c.fetched_at)<CAL_TTL)return c.shifts;
  const from=new Date(Date.now()-12*60*MIN).toISOString(),to=new Date(Date.now()+48*60*MIN).toISOString();
  const r=await feed({mode:'events',timeMin:from,timeMax:to});
  if(!Array.isArray(r?.items))throw Error('Calendar answer has no events list');
  const shifts=shiftsFromEvents(r.items);
  await query(`insert into public.shift_calendar(id,fetched_at,shifts) values(1,now(),$1::jsonb)
    on conflict(id) do update set fetched_at=now(),shifts=excluded.shifts`,[JSON.stringify(shifts)]);
  return shifts;
}

async function loadOrders(){
  const all=[];
  for(const store of STORES){
    const r=await feed({mode:'orders',store});
    const nodes=r?.data?.orders?.nodes;
    if(!Array.isArray(nodes))throw Error(`Shopify ${store}: ${JSON.stringify(r?.errors||r).slice(0,160)}`);
    for(const order of nodes)all.push({store,order});
  }
  return all;
}

async function priorPrints(){
  const rows=await query(`select distinct on (order_id) order_id,day::text as day,fingerprint,printed_at
    from public.shift_report_orders where day>=current_date-7 order by order_id,printed_at desc`);
  return new Map(rows.map(r=>[r.order_id,{day:r.day,fingerprint:r.fingerprint,printedAt:r.printed_at}]));
}

function reportDoc(shift,orders,generatedAt,reprint=false){
  const t=shiftTitle(shift);
  return {title:t.title+(reprint?' (הדפסה חוזרת)':''),subtitle:t.subtitle,generatedAt,orders};
}

async function sendToPrinter(report,shift,orders,{reprint=false}={}){
  const pdf=await renderShiftPdf(reportDoc(shift,orders,`${ddmm(Date.now())} ${hhmm(Date.now())}`,reprint));
  const subject=`Invoice — דוח משמרת ${ddmm(shift.start)} ${hhmm(shift.start)} [${report.token}${reprint?'-R'+Date.now().toString(36):''}]`;
  await email({to:['printer@montigate.com'],subject,text:`DocumentID=${Math.floor(Date.now()/1000)}\nShift report ${report.token}`,
    attachments:[{filename:`shift-${dayKey(shift.start)}-${hhmm(shift.start).replace(':','')}.pdf`,content:Buffer.from(pdf).toString('base64')}]},
    reprint?null:`shift-report-${report.token}-${report.attempts}`);
  return subject;
}

async function printShift(shift,now){
  // Confirm the shift still exists right before printing (cancelled or moved shifts must not print).
  const fresh=await loadShifts(true);
  if(!fresh.some(s=>s.key===shift.key))return {shift:shift.key,skipped:'shift no longer in calendar'};
  const today=dayKey(now);
  const orders=selectOrders(await loadOrders(),await priorPrints(),today,now);
  const [existing]=await query('select id,token,attempts from public.shift_reports where shift_key=$1',[shift.key]);
  const token=existing?.token||randomBytes(4).toString('hex');
  if(!orders.length){
    await query(`insert into public.shift_reports(shift_key,shift_start,shift_end,workers,token,status,attempts)
      values($1,$2,$3,$4,$5,'empty',1) on conflict(shift_key) do update set status='empty',attempts=shift_reports.attempts+1`,
      [shift.key,shift.start,shift.end,shift.workers,token]);
    await notifyManager(`אין הזמנות למשמרת ${ddmm(shift.start)} ${hhmm(shift.start)}`,
      `לא נמצאו הזמנות חדשות לטיפול במשמרת ${hhmm(shift.start)} (${shift.workers.join(', ')}), ולכן לא הודפס דוח.`,`shift-empty-${shift.key}`);
    return {shift:shift.key,orders:0};
  }
  const [rep]=await query(`insert into public.shift_reports(shift_key,shift_start,shift_end,workers,token,status,attempts,orders)
    values($1,$2,$3,$4,$5,'sent',1,$6::jsonb)
    on conflict(shift_key) do update set status='sent',attempts=shift_reports.attempts+1,orders=excluded.orders,created_at=now()
    returning id,token,attempts`,[shift.key,shift.start,shift.end,shift.workers,token,JSON.stringify(orders)]);
  // Orders count as printed from the moment the report is sent; a failed print releases them again.
  // The printer PC only accepts "Invoice —" emails with a DocumentID= line (see sendToPrinter).
  for(const o of orders)await query(`insert into public.shift_report_orders(order_id,day,store,order_name,report_id,fingerprint)
    values($1,$2,$3,$4,$5,$6) on conflict(order_id,day) do update set report_id=excluded.report_id,fingerprint=excluded.fingerprint,printed_at=now()`,
    [o.id,today,o.store,o.name,rep.id,o.fingerprint]);
  try{await sendToPrinter(rep,shift,orders)}
  catch(e){
    await query('delete from public.shift_report_orders where report_id=$1',[rep.id]);
    await query(`update public.shift_reports set status='failed' where id=$1`,[rep.id]);
    throw e;
  }
  return {shift:shift.key,orders:orders.length};
}

// Match sent reports with the PC's print status ("Invoice — … [token]").
async function confirmPrints(){
  const [p]=await query('select telemetry from public.print_printers limit 1');
  const invoices=p?.telemetry?.queue?.invoices||[];
  const open=await query(`select id,token,shift_key,shift_start,created_at,attempts from public.shift_reports where status in ('sent','unconfirmed')`);
  for(const r of open){
    const hit=invoices.find(i=>String(i.subject||'').includes(`[${r.token}]`));
    if(hit?.status==='submitted'){await query(`update public.shift_reports set status='printed',printed_at=now() where id=$1`,[r.id]);continue}
    if(hit&&['held','failed','uncertain'].includes(hit.status)){
      await query('delete from public.shift_report_orders where report_id=$1',[r.id]);
      await query(`update public.shift_reports set status='failed' where id=$1`,[r.id]);
      if(r.attempts>=MAX_ATTEMPTS)await notifyManager(`⚠️ דוח המשמרת לא הודפס — ${ddmm(r.shift_start)} ${hhmm(r.shift_start)}`,
        `המחשב במחסן דיווח שהדפסת דוח המשמרת נכשלה (${hit.status}${hit.error?': '+hit.error:''}).\n${r.attempts<MAX_ATTEMPTS?'ננסה להדפיס שוב בבדיקה הבאה.':'לא ננסה שוב אוטומטית — אפשר להדפיס שוב מהדשבורד.'}`,
        `shift-failed-${r.token}-${r.attempts}`);
      continue;
    }
    if(!hit&&Date.now()-new Date(r.created_at)>CONFIRM_MIN*MIN){
      const done=await query(`update public.shift_reports set status='unconfirmed' where id=$1 and status='sent' returning id`,[r.id]);
      if(done.length)await notifyManager(`⚠️ אין אישור הדפסה לדוח המשמרת — ${ddmm(r.shift_start)} ${hhmm(r.shift_start)}`,
        `הדוח נשלח למחשב המחסן לפני ${CONFIRM_MIN} דקות, אבל המחשב לא דיווח שהדפיס אותו. יש לבדוק שהמדפסת דלוקה ושיש נייר. אפשר להדפיס שוב מהדשבורד.`,
        `shift-unconfirmed-${r.token}`);
    }
  }
}

export async function tick(now=Date.now()){
  await confirmPrints();
  const shifts=await loadShifts();
  const due=shifts.filter(s=>{const st=new Date(s.start).getTime();return now>=st-LEAD_MIN*MIN&&now<=st+LATE_MIN*MIN});
  const out=[];
  for(const s of due){
    const [r]=await query('select status,attempts from public.shift_reports where shift_key=$1',[s.key]);
    if(r&&(r.status!=='failed'||r.attempts>=MAX_ATTEMPTS))continue;
    out.push(await printShift(s,now));
  }
  return {due:due.length,printed:out};
}

async function reprint(id){
  const [r]=await query('select * from public.shift_reports where id=$1',[id]);
  if(!r||!r.orders?.length)throw Error('Report not found or empty');
  await sendToPrinter(r,{start:r.shift_start,end:r.shift_end,workers:r.workers},r.orders,{reprint:true});
}

export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(req.method==='GET'){
    if(!authorized(req))return res.status(401).json({error:'Sign in required'});
    const rows=await query(`select id,shift_start,workers,status,attempts,jsonb_array_length(orders) orders,created_at,printed_at
      from public.shift_reports order by shift_start desc limit 20`);
    return res.json({reports:rows});
  }
  if(req.method!=='POST')return res.status(405).end();
  let b=req.body;if(typeof b==='string'){try{b=JSON.parse(b)}catch{b={}}}
  if(b?.action==='reprint'){
    if(!authorized(req))return res.status(401).json({error:'Sign in required'});
    try{await reprint(b.id);return res.json({ok:true})}catch(e){return res.status(502).json({error:String(e.message).slice(0,200)})}
  }
  if(!process.env.WATCHDOG_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.WATCHDOG_SECRET))return res.status(401).end();
  if(b?.action==='preview'){
    // The PDF the next shift would get right now; nothing is printed or recorded.
    const now=Date.now(),next=(await loadShifts(true)).find(s=>new Date(s.end).getTime()>now);
    if(!next)return res.status(404).json({error:'No upcoming shift in the calendar'});
    const orders=selectOrders(await loadOrders(),await priorPrints(),dayKey(now),now);
    const pdf=await renderShiftPdf(reportDoc(next,orders,`${ddmm(now)} ${hhmm(now)} (תצוגה מקדימה)`));
    res.setHeader('Content-Type','application/pdf');return res.send(Buffer.from(pdf));
  }
  try{return res.json({ok:true,...await tick()})}
  catch(e){
    await notifyManager('⚠️ דוח המשמרת: תקלה בהפקה',`לא הצלחתי להפיק את דוח המשמרת: ${String(e.message).slice(0,300)}\nננסה שוב בבדיקה הבאה (כל 15 דקות).`,
      `shift-error-${dayKey(Date.now())}-${new Date().getUTCHours()}`);
    return res.json({ok:false,error:String(e.message).slice(0,300)});
  }
}
