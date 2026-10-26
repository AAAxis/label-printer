import postgres from 'postgres';
import {neon} from '@neondatabase/serverless';
// Supabase (SUPABASE_DB_URL, transaction pooler on port 6543) when set; otherwise the old Neon DATABASE_URL.
// The pooler does not support prepared statements, hence prepare:false. One connection per serverless instance.
// Callers pass JSON already stringified (as the Neon driver expected), so strings go through unchanged.
const asJson=v=>typeof v==='string'?v:JSON.stringify(v);
const json=oid=>({to:oid,from:[oid],serialize:asJson,parse:JSON.parse});
let client;
export function query(text,params=[]){
  if(process.env.SUPABASE_DB_URL){
    client??=postgres(process.env.SUPABASE_DB_URL,{prepare:false,max:1,idle_timeout:20,connect_timeout:10,types:{json:json(114),jsonb:json(3802)}});
    return client.unsafe(text,params).then(rows=>[...rows]);
  }
  client??=neon(process.env.DATABASE_URL);
  return client.query(text,params);
}
