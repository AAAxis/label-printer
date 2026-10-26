import {query} from './db.js';
// Printer health alerts. Each problem gets one row in print_alert_state while it lasts:
// alert once after it has lasted long enough, send one "back to normal" when it clears.
// Runs on every heartbeat and from /api/watchdog (which is what notices missing heartbeats).
// Email only: the TigerMedia WhatsApp template renders as a customer order receipt.
const MIN=60000;

export function conditions(printer,now=Date.now()){
  const t=printer.telemetry||{},q=t.queue||{},c=q.counts||{};
  const last=printer.last_heartbeat?new Date(printer.last_heartbeat).getTime():0;
  const name=printer.name||'Label printer';
  const offline=now-last>15*MIN;
  const list=[{kind:'offline',active:offline,after:0,since:new Date(last||now),
    detail:`לא התקבל דיווח מהמחשב של המדפסת מאז ${new Date(last).toLocaleString('he-IL',{timeZone:'Asia/Jerusalem'})} (המחשב כבוי, במצב שינה, או שתוכנת המדפסת נעצרה).`}];
  // Without fresh heartbeats the other readings are stale: keep their state as it is.
  if(offline)return list;
  // Only labels still waiting to print count; with nothing pending a DYMO restart is not worth an alert.
  const waiting=Number(c.pending)||0;
  list.push({kind:'stuck',active:printer.status!=='printing'&&waiting>0,after:15*MIN,
    detail:`${waiting} מדבקות ממתינות. מצב המדפסת: ${printer.status||'לא ידוע'}${printer.last_error||q.processing_error?` — ${printer.last_error||q.processing_error}`:''}`});
  const skus=[...new Set((q.jobs||[]).filter(j=>j.status==='needs_name').map(j=>j.sku))];
  list.push({kind:'needs_name',active:(c.needs_name||0)>0,after:30*MIN,
    detail:`${c.needs_name||0} מדבקות ממתינות כי המוצר חסר בקטלוג${skus.length?`: ${skus.join(', ')}`:''}. יש לבדוק את כרטיס המוצר ב-SUMIT (מק"ט ושם).`});
  return list.map(x=>({...x,key:`${printer.id}:${x.kind}`,printer:name}));
}

const TEXT={
  offline:['🔌 מחשב המדפסת לא מדווח','✅ מחשב המדפסת חזר לדווח'],
  stuck:['⚠️ מדפסת המדבקות תקועה','✅ מדפסת המדבקות חזרה להדפיס'],
  needs_name:['⚠️ מדבקות ממתינות — מוצר חסר בקטלוג','✅ המדבקות שחסר להן שם שוחררו']
};

async function send(subject,text,to=(process.env.PRINT_NOTIFY_EMAIL||'gorillagrillshop@gmail.com').split(',')){
  const r=await fetch('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(6000),
    headers:{Authorization:'Bearer '+process.env.RESEND_API_KEY,'Content-Type':'application/json'},
    body:JSON.stringify({from:'Gorilla Label Printer <sku-bot@montigate.com>',
      to,subject,text})});
  if(!r.ok)throw Error('Resend '+r.status);
}

export async function checkHealth(){
  if(!process.env.RESEND_API_KEY)return;
  const printers=await query('select id,name,status,last_error,last_heartbeat,telemetry from public.print_printers');
  for(const p of printers)await checkLabelRules(p);
  for(const p of printers)await checkProgress(p);
  for(const p of printers)for(const c of conditions(p)){
    const [title,back]=TEXT[c.kind];
    if(c.active){
      await query(`insert into public.print_alert_state(key,since,detail) values($1,$2,$3)
        on conflict(key) do update set detail=excluded.detail`,[c.key,c.since,c.detail]);
      // The conditional update lets only one concurrent check send the alert.
      const claimed=await query(`update public.print_alert_state set alerted_at=now()
        where key=$1 and alerted_at is null and since<=now()-make_interval(secs=>$2) returning since`,[c.key,c.after/1000]);
      if(!claimed.length)continue;
      try{await send(`${title} — ${c.printer}`,`${title}\n\n${c.detail}\n\nההתראה נשלחת פעם אחת; תישלח הודעה נוספת כשהבעיה תיפתר.`)}
      catch{await query('update public.print_alert_state set alerted_at=null where key=$1',[c.key])}
    }else{
      const cleared=await query('delete from public.print_alert_state where key=$1 returning alerted_at,since',[c.key]);
      if(!cleared[0]?.alerted_at)continue;
      const mins=Math.round((Date.now()-new Date(cleared[0].since).getTime())/MIN);
      await send(`${back} — ${c.printer}`,`${back}\n\nהבעיה נמשכה כ-${mins} דקות.`).catch(()=>{});
    }
  }
}

// Label rules agreed with the client: never print from a saved .dymo file (its importer text is not from SUMIT),
// and never print a product whose SUMIT card has no supplier. The PC enforces them; this catches any regression.
const RULES_FROM='2026-10-12T17:50:00Z';
export function ruleBreaks(jobs,suppliers){
  return (jobs||[]).filter(j=>j.status==='submitted'&&j.created_at>=RULES_FROM).flatMap(j=>{
    const why=[];
    if(j.label_type==='saved')why.push('הודפס מקובץ מדבקה שמור (טקסט יבואן שלא מ-SUMIT)');
    if(!suppliers.get(j.sku))why.push('הודפס למרות שאין ספק בכרטיס המוצר ב-SUMIT');
    return why.length?[{job:j,why}]:[];
  });
}

async function checkLabelRules(printer){
  const jobs=(printer.telemetry?.queue?.jobs||[]).filter(j=>j.status==='submitted'&&j.created_at>=RULES_FROM);
  if(!jobs.length)return;
  const rows=await query('select sku,supplier from public.print_products where sku=any($1::text[])',[[...new Set(jobs.map(j=>j.sku))]]);
  for(const {job,why} of ruleBreaks(jobs,new Map(rows.map(r=>[r.sku,r.supplier])))){
    const key=`rule-break:${printer.id}:${job.id}`;
    const claimed=await query('insert into public.print_notifications(key) values($1) on conflict do nothing returning key',[key]);
    if(!claimed.length)continue;
    try{await send(`🚫 מדבקה הודפסה בניגוד לכללים — ${job.sku}`,
      `מדבקה ${job.sku} (${job.product_name||''}) הודפסה ב-${job.created_at}:\n${why.join('\n')}\n\nיש לעדכן את המחשב של המדפסת. הכללים: רק תבנית אחת עם ספק מ-SUMIT; מוצר בלי ספק — לא מודפס.`,['dima@holylabs.net'])}
    catch{await query('delete from public.print_notifications where key=$1',[key])}
  }
}

// Labels pending but none printed for 15 minutes, whatever status the PC reports (a "printing" PC stalled for 18h on 25/10).
// The row's detail holds the submitted count; any change counts as progress and restarts the clock.
async function checkProgress(printer){
  const c=printer.telemetry?.queue?.counts||{},pending=Number(c.pending)||0,done=String(Number(c.submitted)||0);
  const key=`${printer.id}:noprogress`,name=printer.name||'Label printer';
  const fresh=printer.last_heartbeat&&Date.now()-new Date(printer.last_heartbeat).getTime()<=15*MIN;
  if(!fresh)return; // a silent PC is the 'offline' alert's job
  const [row]=await query('select since,alerted_at,detail from public.print_alert_state where key=$1',[key]);
  const recover=async()=>{if(row?.alerted_at)await send(`✅ המדבקות חזרו להדפיס — ${name}`,`✅ המדבקות חזרו להדפיס.\n\nהעיכוב נמשך כ-${Math.round((Date.now()-new Date(row.since).getTime())/MIN)} דקות.`).catch(()=>{})};
  if(!pending){if(row){await query('delete from public.print_alert_state where key=$1',[key]);await recover()}return}
  if(!row||row.detail!==done){
    await query(`insert into public.print_alert_state(key,since,detail) values($1,now(),$2)
      on conflict(key) do update set since=now(),detail=excluded.detail,alerted_at=null`,[key,done]);
    if(row)await recover();
    return;
  }
  const claimed=await query(`update public.print_alert_state set alerted_at=now()
    where key=$1 and alerted_at is null and since<=now()-interval '15 minutes' returning since`,[key]);
  if(!claimed.length)return;
  try{await send(`⚠️ מדבקות ממתינות ולא מודפסות — ${name}`,`⚠️ ${pending} מדבקות ממתינות להדפסה, ואף מדבקה לא הודפסה ב-15 הדקות האחרונות (מצב המחשב: ${printer.status||'לא ידוע'}).\n\nיש לבדוק במחשב המדפסת: שהמדפסת דלוקה, שיש גליל מדבקות, ושתוכנת ההדפסה רצה.\n\nההתראה נשלחת פעם אחת; תישלח הודעה כשההדפסה תחזור.`)}
  catch{await query('update public.print_alert_state set alerted_at=null where key=$1',[key])}
}
