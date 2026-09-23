// Local reports are visibility only. They never enter the cloud job claim queue.
export function queueData(printers, cloudJobs, now = Date.now()) {
  const reports = printers.filter(p => p.telemetry?.queue?.reported_at);
  const local = reports.flatMap(p => {
    const q = p.telemetry.queue;
    return (q.jobs || []).map(j => ({...j,
      id: `local:${p.id}:${q.id || 'unknown'}:${j.id}`,
      printer_id: p.id, source: 'Windows', reported_at: q.reported_at,
      report_stale: now - Date.parse(q.reported_at) > 90000
    }));
  });
  const jobs = [...local, ...cloudJobs.map(j => ({...j, source:'Cloud'}))];
  jobs.sort((a,b) => {
    const active = s => ['pending','queued','claimed','submitting','uncertain','failed'].includes(s) ? 0 : 1;
    return active(a.status)-active(b.status) || Date.parse(b.created_at)-Date.parse(a.created_at);
  });
  const counts = {};
  for (const p of reports) for (const [state,n] of Object.entries(p.telemetry.queue.counts || {})) counts[state]=(counts[state]||0)+n;
  for (const j of cloudJobs) counts[j.status]=(counts[j.status]||0)+1;
  return {jobs, queueCounts:counts, windowsQueueConnected:reports.length > 0,
    windowsQueueFresh:reports.some(p => now-Date.parse(p.telemetry.queue.reported_at)<90000),
    blockedEmails:reports.flatMap(p => (p.telemetry.queue.blocked_emails || []).map(e=>({...e,printer_id:p.id}))),
    blockedEmailCount:reports.reduce((n,p)=>n+(p.telemetry.queue.blocked_email_count||0),0)};
}
