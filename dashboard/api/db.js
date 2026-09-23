import {neon} from '@neondatabase/serverless';
let client;
export function query(text,params=[]){client??=neon(process.env.DATABASE_URL);return client.query(text,params)}
