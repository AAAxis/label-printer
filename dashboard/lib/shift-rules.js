// Shift report rules, kept free of I/O so they can be tested against real Shopify / Calendar data.
export const TZ='Asia/Jerusalem';
export const LEAD_MIN=Number(process.env.SHIFT_PRINT_LEAD_MIN||30);   // print this long before the shift starts
const MAX_AGE_DAYS=30;                                                // older open orders are left for the manager
const PAID=new Set(['PAID','PARTIALLY_REFUNDED']);
const SKIP_TAG='לא-למחסן';

const fmt=(d,opts)=>new Intl.DateTimeFormat('he-IL',{timeZone:TZ,...opts}).format(new Date(d));
export const dayKey=d=>new Intl.DateTimeFormat('en-CA',{timeZone:TZ}).format(new Date(d));   // 2026-10-26
export const hhmm=d=>fmt(d,{hour:'2-digit',minute:'2-digit',hour12:false});
export const ddmm=d=>fmt(d,{day:'2-digit',month:'2-digit'});

// Shifts are timed events titled with the worker's name. All-day events ("יום חובה", holidays) and the
// short weekly reminder "האם קבלתי משמרות?" are not shifts. Workers starting together form one shift.
export function shiftsFromEvents(events){
  const byStart=new Map();
  for(const e of events||[]){
    const s=e?.start?.dateTime,en=e?.end?.dateTime;
    if(!s||!en||e.status==='cancelled')continue;
    const mins=(new Date(en)-new Date(s))/60000,title=String(e.summary||'').trim();
    if(mins<120||!title||title.includes('?'))continue;
    const k=new Date(s).toISOString();
    const sh=byStart.get(k)||{start:k,end:new Date(en).toISOString(),workers:[],eventIds:[]};
    sh.workers.push(title);sh.eventIds.push(e.id);
    if(new Date(en)>new Date(sh.end))sh.end=new Date(en).toISOString();
    byStart.set(k,sh);
  }
  return [...byStart.values()].sort((a,b)=>a.start.localeCompare(b.start)).map(s=>({...s,key:`shift-${s.start}`}));
}

export function classifyDelivery(o){
  const title=String(o?.shippingLine?.title||'').trim(),tags=(o?.tags||[]).map(String);
  const methods=(o?.fulfillmentOrders?.nodes||[]).map(f=>f?.deliveryMethod?.methodType).filter(Boolean);
  if(methods.includes('PICK_UP')||tags.includes('איסוף עצמי')||/איסוף עצמי|מחסן/.test(title))return {kind:'pickup',label:'איסוף עצמי',detail:''};
  if(methods.includes('PICKUP_POINT')||title.startsWith('נקודות חלוקה')||/נקודת איסוף|לוקר|בוקס/.test(title)){
    const detail=title.includes(':')?title.slice(title.indexOf(':')+1).replace(/#\d+#/g,'').trim():'';
    return {kind:'point',label:'נקודת איסוף',detail};
  }
  const city=String(o?.shippingAddress?.city||'').trim();
  return {kind:'home',label:'משלוח עד הבית',detail:city};
}

// Which lines still need packing, and whether the order belongs in the warehouse report at all.
export function packableItems(o){
  return (o?.lineItems?.nodes||[]).filter(li=>li.requiresShipping!==false&&Number(li.unfulfilledQuantity)>0)
    .map(li=>({title:[li.title,li.variantTitle&&li.variantTitle!=='Default Title'?li.variantTitle:''].filter(Boolean).join(' – ').trim(),qty:Number(li.unfulfilledQuantity)}));
}
export function includeOrder(o,now=Date.now()){
  if(!o||o.cancelledAt||o.closed)return false;
  if(!PAID.has(o.displayFinancialStatus))return false;
  if(!['UNFULFILLED','PARTIALLY_FULFILLED','IN_PROGRESS','ON_HOLD','SCHEDULED'].includes(o.displayFulfillmentStatus))return false;
  if((o.tags||[]).includes(SKIP_TAG))return false;
  if(now-new Date(o.createdAt)>MAX_AGE_DAYS*864e5)return false;
  return packableItems(o).length>0;
}

// Fingerprint of what the warehouse acts on; a change after printing puts the order in "updated since printed".
export function fingerprint(o){
  const d=classifyDelivery(o);
  return JSON.stringify([packableItems(o).map(i=>[i.title,i.qty]),d.kind,d.detail,o?.shippingAddress?.name||'']);
}

// prior: Map order id -> {day, fingerprint} of the last time it was printed.
export function selectOrders(orders,prior,today,now=Date.now()){
  const out=[];
  for(const {store,order} of orders){
    if(!includeOrder(order,now))continue;
    const p=prior.get(order.id),fp=fingerprint(order);
    let section=null,flag='',carry=false;
    if(p&&p.day===today){if(p.fingerprint===fp)continue;section='עודכנו אחרי שהודפסו היום';flag='עודכנה אחרי ההדפסה'}
    else if(p){section='מימים קודמים – טרם נארזו';flag=`הודפסה ב-${ddmm(p.printedAt)}`;carry=true}
    out.push({id:order.id,store,name:order.name,createdAt:order.createdAt,date:`${ddmm(order.createdAt)} · ${hhmm(order.createdAt)}`,
      recipient:String(order?.shippingAddress?.name||'').replace(/[‎‏‪-‮]/g,'').trim()||'ללא שם נמען',
      delivery:classifyDelivery(order),items:packableItems(order),note:String(order.note||'').trim().slice(0,160),
      section,flag,carry,fingerprint:fp});
  }
  const rank=o=>o.carry?0:o.section?2:1;
  return out.sort((a,b)=>rank(a)-rank(b)||a.createdAt.localeCompare(b.createdAt));
}

export function shiftTitle(shift){
  const start=new Date(shift.start),h=Number(new Intl.DateTimeFormat('en-GB',{timeZone:TZ,hour:'numeric',hour12:false}).format(start));
  const part=h<12?'בוקר':h<17?'צהריים':'ערב';
  return {title:`הזמנות למשמרת ${part}`,
    subtitle:`${fmt(start,{weekday:'long',day:'2-digit',month:'2-digit',year:'numeric'})} · ${hhmm(shift.start)}–${hhmm(shift.end)} · ${shift.workers.join(', ')}`};
}
