import unittest
from unittest.mock import Mock
from supabase_agent import CloudAgent, Heartbeat

class AgentTests(unittest.TestCase):
    def test_catalog_pagination_preserves_sku(self):
        a=CloudAgent('https://example.com/api/agent','x'*40)
        a.rpc=Mock(side_effect=[[{'sku':str(i).zfill(5),'name':'Product'} for i in range(1000)],[{'sku':'ABC','name':'Hebrew product'}]])
        self.assertEqual(len(a.products()),1001)
        self.assertEqual(a.rpc.call_args.args[1],{'p_start':1000})

    def test_empty_catalog_preserves_cache(self):
        a=CloudAgent('https://example.com/api/agent','x'*40);a.rpc=Mock(return_value=[])
        with self.assertRaises(ValueError):a.products()

    def test_unreachable_printer_still_reports_agent(self):
        a=Mock();h=Heartbeat(a,'DYMO','printing',Mock(side_effect=OSError))
        h.tick()
        self.assertEqual(a.heartbeat.call_args.args[1],'printer_unreachable')
        self.assertEqual(a.heartbeat.call_args.args[3]['mode'],'printing')

    def test_preview_does_not_claim_to_print(self):
        a=Mock();h=Heartbeat(a,'DYMO','preview',lambda:[{'name':'DYMO','connected':True}]);h.tick()
        self.assertEqual(a.heartbeat.call_args.args[1],'preview')

if __name__=='__main__':unittest.main()

class QueueReportTests(unittest.TestCase):
    def test_snapshot_is_readonly_and_includes_errors(self):
        import tempfile
        from pathlib import Path
        from printer import Queue
        from supabase_agent import queue_snapshot
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.db';q=Queue(path)
            with q.db:
                q.db.execute("INSERT INTO emails VALUES ('e','2026-09-15','ready','')")
                q.db.execute("INSERT INTO labels(email_id,position,sku,name,error) VALUES ('e',0,'001','Test','Missing label')")
            q.set_meta('processing_error','DYMO GetPrinters HTTP 500')
            report=queue_snapshot(path)
            self.assertEqual(report['id'],q.meta('queue_id'))
            self.assertEqual(report['counts'],{'pending':1})
            self.assertEqual(report['jobs'][0]['error'],'Missing label')
            self.assertEqual(report['processing_error'],'DYMO GetPrinters HTTP 500')
            self.assertEqual(q.pending()[0]['status'],'pending')
            q.db.close()
            q=Queue(path);self.assertEqual(q.meta('queue_id'),report['id']);q.db.close()

    def test_paused_queue_is_reported_without_claiming_physical_print(self):
        agent=Mock()
        report={'processing_error':'Missing file','counts':{'submitted':2},'jobs':[]}
        h=Heartbeat(agent,'DYMO','printing',lambda:[{'name':'DYMO','connected':True}],snapshot=lambda:report)
        h.tick()
        self.assertEqual(agent.heartbeat.call_args.args[1],'queue_paused')
        self.assertEqual(agent.heartbeat.call_args.args[3]['queue'],report)

    def test_snapshot_failure_does_not_prevent_heartbeat(self):
        agent=Mock()
        h=Heartbeat(agent,'DYMO','printing',lambda:[{'name':'DYMO','connected':True}],snapshot=Mock(side_effect=OSError('busy')))
        h.tick()
        self.assertIn('queue_error',agent.heartbeat.call_args.args[3])
