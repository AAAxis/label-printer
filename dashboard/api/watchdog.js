import {equal} from './session.js';
import {checkHealth} from './health.js';
// Called every few minutes by an external scheduler (Make), so a PC that stops sending heartbeats is still noticed.
export default async function handler(req,res){
  res.setHeader('Cache-Control','no-store');
  if(!process.env.WATCHDOG_SECRET||!equal(req.headers.authorization,'Bearer '+process.env.WATCHDOG_SECRET))return res.status(401).end();
  try{await checkHealth();return res.json({ok:true})}
  catch{return res.status(503).json({error:'Health check failed'})}
}
