import {query} from './db.js';
// Turns the Windows queue report into one message per finished or held order.
// "submitted" means the DYMO service accepted the label; physical output is not confirmed.
const ACTIVE=new Set(['pending','submitting']);
const OK=new Set(['submitted']);
const INVOICE_FINAL=new Set(['submitted','held','failed','uncertain']);
const INVOICE_STATUS={submitted:'נשלחה למדפסת Brother',held:'נעצרה (מייל לא תקין)',failed:'ההדפסה נכשלה',uncertain:'לא ברור אם הודפסה (נותק באמצע)'};
const WHATSAPP='https://n8n.tigermedia.co.il/webhook/tg-new-order';

export function finishedOrders(queue){
  const byEmail=new Map();
  for(const j of queue?.jobs||[]){
    if(!j.email_id||String(j.email_id).startsWith('manual:'))continue;
    if(!byEmail.has(j.email_id))byEmail.set(j.email_id,[]);
    byEmail.get(j.email_id).push(j);
  }
  const events=[];
  for(const [emailId,jobs] of byEmail){
    if(jobs.some(j=>ACTIVE.has(j.status)))continue;
    const problems=jobs.filter(j=>!OK.has(j.status));
    events.push({key:`${emailId}:done`,emailId,ok:problems.length===0,jobs,problems});
  }
  for(const b of queue?.blocked_emails||[])
    events.push({key:`${b.id}:blocked`,emailId:b.id,ok:false,jobs:[],problems:[],blocked:b.error||'Order held'});
  for(const i of queue?.invoices||[]){
    if(!i.email_id||!INVOICE_FINAL.has(i.status))continue;
    events.push({key:`${i.email_id}:invoice`,emailId:i.email_id,ok:i.status==='submitted',jobs:[],problems:[],invoice:i});
  }
  return events;
}

function summary(jobs){
  const lines=new Map();
  for(const j of jobs){const k=j.sku;const l=lines.get(k)||{sku:k,name:j.product_name||'',count:0};l.count++;lines.set(k,l)}
  return [...lines.values()];
}

export function message(event,subject){
  const title=subject||event.emailId;
  if(event.invoice){
    const i=event.invoice,t=(i.subject||title).replace(/^Invoice — /,'');
    const text=`${event.ok?'🧾':'⚠️'} טיוטת החשבונית ${i.document_id?`(מסמך ${i.document_id}) `:''}עבור "${t}": ${INVOICE_STATUS[i.status]||i.status}.`+
      (i.error?`\nסיבה: ${i.error}`:'')+(event.ok?'\n\n(המדפסת אישרה קבלה; יש לוודא שהדף יצא פיזית.)':'\nהחשבונית לא תודפס שוב אוטומטית — יש להדפיס ידנית מ-SUMIT.');
    return {subject:`${event.ok?'🧾 חשבונית הודפסה':'⚠️ בעיה בהדפסת חשבונית'} — ${t}`,text};
  }
  if(event.blocked)return {subject:`⚠️ ההזמנה לא הודפסה — ${title}`,
    text:`⚠️ ההזמנה "${title}" לא הודפסה. המדפסת עצרה את כל ההזמנה.\nסיבה: ${event.blocked}\nאף מדבקה לא הודפסה. יש לטפל ולשלוח מחדש.`};
  const ok=event.jobs.filter(j=>OK.has(j.status));
  const rows=summary(ok).map(l=>`${l.sku} × ${l.count} — ${l.name}`).join('\n');
  let text=`✅ ${ok.length} מדבקות נשלחו למדפסת עבור "${title}".\n\n${rows}`;
  if(!event.ok){
    const bad=event.problems.map(j=>`${j.sku} — ${j.status}${j.error?`: ${j.error}`:''}`).join('\n');
    text=`⚠️ ${event.problems.length} מדבקות לא הודפסו עבור "${title}".\n\n${bad}\n\n`+(ok.length?text:'');
  }
  text+='\n\n(המדפסת אישרה קבלה; יש לוודא שהמדבקות יצאו פיזית.)';
  return {subject:`${event.ok?'✅ מדבקות הודפסו':'⚠️ בעיה בהדפסת מדבקות'} — ${title}`,text};
}

async function resend(path,init={}){
  const r=await fetch('https://api.resend.com'+path,{...init,headers:{Authorization:'Bearer '+process.env.RESEND_API_KEY,'Content-Type':'application/json',...(init.headers||{})},signal:AbortSignal.timeout(6000)});
  if(!r.ok)throw Error('Resend '+r.status);
  return r.json();
}

async function send(event){
  let subject='';
  try{if(!event.emailId.startsWith('manual:'))subject=(await resend('/emails/receiving/'+encodeURIComponent(event.emailId))).subject||''}catch{}
  subject=subject.replace(/^Labels — /,'');
  const m=message(event,subject);
  await resend('/emails',{method:'POST',headers:{'Idempotency-Key':'print-notify-'+event.key},body:JSON.stringify({
    from:'Gorilla Label Printer <sku-bot@montigate.com>',to:(process.env.PRINT_NOTIFY_EMAIL||'gorillagrillshop@gmail.com').split(','),
    subject:m.subject,text:m.text})});
  await fetch(WHATSAPP,{method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(6000),body:JSON.stringify({
    first_name:event.ok?'PRINT OK':'PRINT PROBLEM',last_name:'Label printer',name:m.text.slice(0,900),
    phone:'+972515473526',email:'dima@holylabs.net',order_number:m.subject,order_id:event.emailId,store:'Gorilla'})}).catch(()=>{});
}

// Claims each event once in the database, so repeated heartbeats never resend it.
export async function notifyFinished(telemetry){
  if(!process.env.RESEND_API_KEY)return;
  for(const event of finishedOrders(telemetry?.queue)){
    const claimed=await query('insert into public.print_notifications(key) values($1) on conflict do nothing returning key',[event.key]);
    if(!claimed.length)continue;
    try{await send(event)}
    catch{await query('delete from public.print_notifications where key=$1',[event.key])}
  }
}
