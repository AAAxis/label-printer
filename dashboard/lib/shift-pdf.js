import {PDFDocument,rgb} from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import bidiFactory from 'bidi-js';
import {readFileSync} from 'fs';
// A4 shift report: one framed block per order with a tick box, Hebrew right-to-left.
// pdf-lib draws glyphs left to right, so each line is reordered to visual order with bidi-js first.
const bidi=bidiFactory();
const FONT={regular:new URL('./fonts/Heebo-Regular.ttf',import.meta.url),bold:new URL('./fonts/Heebo-Bold.ttf',import.meta.url)};
const W=595.28,H=841.89,M=36,INK=rgb(0.07,0.07,0.07),SOFT=rgb(0.38,0.38,0.38),LINE=rgb(0.72,0.72,0.72);

export function visual(text){
  const s=String(text??'');
  const levels=bidi.getEmbeddingLevels(s,'rtl');
  const chars=[...s];
  // Brackets inside right-to-left runs must face the other way once reversed (bidi-js's own map comes back empty here).
  const MIRROR={'(':')',')':'(','[':']',']':'[','{':'}','}':'{','<':'>','>':'<'};
  levels.levels.forEach((lv,i)=>{if(lv%2===1&&MIRROR[chars[i]])chars[i]=MIRROR[chars[i]]});
  for(const [a,b] of bidi.getReorderSegments(s,levels)){const seg=chars.slice(a,b+1).reverse();chars.splice(a,seg.length,...seg)}
  return chars.join('');
}

// fontkit reverses a run on its own when it detects Hebrew script in it, judged by the run's first letter.
// Drawing Hebrew and non-Hebrew pieces separately makes that predictable for mixed lines.
const HEB=/[\u0590-\u05FF]/;
function runs(v){
  const out=[];
  for(const ch of v){const h=HEB.test(ch);const last=out[out.length-1];if(last&&last.h===h)last.t+=ch;else out.push({h,t:ch})}
  return out.map(r=>({t:r.t,draw:r.h?[...r.t].reverse().join(''):r.t}));
}
const measure=(t,font,size)=>runs(visual(t)).reduce((w,r)=>w+font.widthOfTextAtSize(r.draw,size),0);
function drawRuns(page,t,x,y,size,font,color){let cx=x;for(const r of runs(visual(t))){page.drawText(r.draw,{x:cx,y,size,font,color});cx+=font.widthOfTextAtSize(r.draw,size)}}

function wrap(text,font,size,max){
  const words=String(text??'').split(/\s+/).filter(Boolean),lines=[];let cur='';
  for(const w of words){const t=cur?cur+' '+w:w;if(!cur||measure(t,font,size)<=max)cur=t;else{lines.push(cur);cur=w}}
  if(cur)lines.push(cur);
  return lines.length?lines:[''];
}

export async function renderShiftPdf(report){
  const doc=await PDFDocument.create();doc.registerFontkit(fontkit);
  doc.setTitle(report.title);doc.setCreator('Gorilla shift report');
  const reg=await doc.embedFont(readFileSync(FONT.regular),{subset:true}),bold=await doc.embedFont(readFileSync(FONT.bold),{subset:true});
  let page,y,pageNo=0;
  const right=W-M,left=M;
  const text=(t,{x=right,size=10,font=reg,color=INK,align='right'}={})=>{
    const w=measure(t,font,size);
    drawRuns(page,t,align==='right'?x-w:align==='left'?x:x-w/2,y,size,font,color);return w;
  };
  const newPage=()=>{
    page=doc.addPage([W,H]);pageNo++;y=H-M;
    y-=18;text(report.title,{size:16,font:bold});
    text(`הופק ${report.generatedAt} · ${report.orders.length} הזמנות`,{x:left,size:9,color:SOFT,align:'left'});
    y-=14;text(report.subtitle,{size:10,color:SOFT});
    y-=8;page.drawLine({start:{x:left,y},end:{x:right,y},thickness:1.5,color:INK});y-=12;
  };
  const section=(label)=>{if(y<M+60)newPage();y-=4;text(label,{size:10,font:bold,color:SOFT});y-=14};
  newPage();

  const BOX=16,PAD=7,inner=right-left-2*PAD-BOX-10;
  const blockLines=(o)=>{
    const head=1,ship=wrap(`${o.recipient} · ${o.delivery.label}${o.delivery.detail?' · '+o.delivery.detail:''}`,bold,10,inner).length;
    const items=o.items.reduce((n,it)=>n+wrap(it.title,reg,9.5,inner-30).length,0);
    return {head,ship,items,h:PAD*2+13+ship*13+items*12.5+(o.note?wrap('הערה: '+o.note,reg,8.5,inner).length*11:0)};
  };
  let lastSection=null;
  for(const o of report.orders){
    if(o.section!==lastSection){if(o.section)section(o.section);lastSection=o.section}
    const b=blockLines(o);
    if(y-b.h<M+20)newPage();
    const top=y;
    page.drawRectangle({x:left,y:top-b.h,width:right-left,height:b.h,borderColor:o.carry?SOFT:LINE,borderWidth:o.carry?1.2:0.8,borderDashArray:o.carry?[4,3]:undefined});
    page.drawRectangle({x:left+PAD,y:top-PAD-BOX,width:BOX,height:BOX,borderColor:INK,borderWidth:1.6});
    y=top-PAD-11;
    const w=text(o.name,{x:right-PAD,size:12,font:bold});
    text(`${o.date}${o.flag?' · '+o.flag:''}`,{x:right-PAD-w-10,size:8.5,color:SOFT});
    y-=14;
    const shipLine=`${o.recipient} · ${o.delivery.label}${o.delivery.detail?' · '+o.delivery.detail:''}`;
    if(o.delivery.kind==='pickup'){
      // self pickup is set in a black band so it is never handed to the courier by mistake
      const bw=measure(o.delivery.label,bold,10)+8,rw=measure(o.recipient+' · ',bold,10);
      page.drawRectangle({x:right-PAD-rw-bw,y:y-3,width:bw,height:13,color:INK});
      text(o.recipient+' ·',{x:right-PAD,size:10,font:bold});
      drawRuns(page,o.delivery.label,right-PAD-rw-bw+4,y,10,bold,rgb(1,1,1));
      y-=13;
    }else{
      for(const l of wrap(shipLine,bold,10,inner)){text(l,{x:right-PAD,size:10,font:bold});y-=13}
    }
    for(const it of o.items){
      const lines=wrap(it.title,reg,9.5,inner-30);
      text(`×${it.qty}`,{x:right-PAD,size:9.5,font:bold});
      for(const l of lines){text(l,{x:right-PAD-30,size:9.5});y-=12.5}
    }
    if(o.note)for(const l of wrap('הערה: '+o.note,reg,8.5,inner)){text(l,{x:right-PAD,size:8.5,color:SOFT});y-=11}
    y=top-b.h-7;
  }
  // footers once the page count is known
  const pages=doc.getPages();
  pages.forEach((p,i)=>{page=p;y=M-14;
    text('ריבוע מסומן = נבדק ונארז · בסוף המשמרת להעביר את הדף למנהל',{size:8,color:SOFT});
    text(`עמוד ${i+1} מתוך ${pages.length}`,{x:left,size:8,color:SOFT,align:'left'});});
  return doc.save();
}
