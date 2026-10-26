import {equal} from './session.js';
import {checkHealth} from '../lib/health.js';
// Called every few minutes by an external scheduler (Make), so a PC that stops sending heartbeats is still noticed.
export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(!process.env.WATCHDOG_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.WATCHDOG_SECRET))return res.status(401).end();
  try{await checkHealth();return res.json({ok:true})}
  catch(e){return res.status(503).json({error:'Health check failed',detail:String(e?.message||e).slice(0,300)})}
}
