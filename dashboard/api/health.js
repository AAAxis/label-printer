import {query} from './db.js';
// Printer health alerts. Each problem gets one row in print_alert_state while it lasts:
// alert once after it has lasted long enough, send one "back to normal" when it clears.
// Runs on every heartbeat and from /api/watchdog (which is what notices missing heartbeats).
const WHATSAPP='https://n8n.tigermedia.co.il/webhook/tg-new-order';
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

async function send(subject,text,ok){
  const r=await fetch('https://api.resend.com/emails',{method:'POST',signal:AbortSignal.timeout(6000),
    headers:{Authorization:'Bearer '+process.env.RESEND_API_KEY,'Content-Type':'application/json'},
    body:JSON.stringify({from:'Gorilla Label Printer <sku-bot@montigate.com>',
      to:(process.env.PRINT_NOTIFY_EMAIL||'gorillagrillshop@gmail.com').split(','),subject,text})});
  if(!r.ok)throw Error('Resend '+r.status);
  await fetch(WHATSAPP,{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(6000),body:JSON.stringify({
    first_name:ok?'PRINT OK':'PRINT PROBLEM',last_name:'Label printer',name:text.slice(0,900),
    phone:'+972515473526',email:'dima@holylabs.net',order_number:subject,order_id:'printer-health',store:'Gorilla'})}).catch(()=>{});
}

export async function checkHealth(){
  if(!process.env.RESEND_API_KEY)return;
  const printers=await query('select id,name,status,last_error,last_heartbeat,telemetry from public.print_printers');
  for(const p of printers)for(const c of conditions(p)){
    const [title,back]=TEXT[c.kind];
    if(c.active){
      await query(`insert into public.print_alert_state(key,since,detail) values($1,$2,$3)
        on conflict(key) do update set detail=excluded.detail`,[c.key,c.since,c.detail]);
      // The conditional update lets only one concurrent check send the alert.
      const claimed=await query(`update public.print_alert_state set alerted_at=now()
        where key=$1 and alerted_at is null and since<=now()-make_interval(secs=>$2) returning since`,[c.key,c.after/1000]);
      if(!claimed.length)continue;
      try{await send(`${title} — ${c.printer}`,`${title}\n\n${c.detail}\n\nההתראה נשלחת פעם אחת; תישלח הודעה נוספת כשהבעיה תיפתר.`,false)}
      catch{await query('update public.print_alert_state set alerted_at=null where key=$1',[c.key])}
    }else{
      const cleared=await query('delete from public.print_alert_state where key=$1 returning alerted_at,since',[c.key]);
      if(!cleared[0]?.alerted_at)continue;
      const mins=Math.round((Date.now()-new Date(cleared[0].since).getTime())/MIN);
      await send(`${back} — ${c.printer}`,`${back}\n\nהבעיה נמשכה כ-${mins} דקות.`,true).catch(()=>{});
    }
  }
}
