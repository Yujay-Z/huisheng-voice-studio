import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from voiceclone import separation

ROOT = Path(__file__).resolve().parent.parent


class SeparationLifecycleTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.cache/test-temp'
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.task = {'id':'test','state':'queued','message':'准备处理','created':0,
                     '_directory':self.directory,'_cancel':threading.Event(),'_settled':False}
        (self.directory/'status.json').write_text(json.dumps({'state':'ready','message':'完成','finished_at':1}),encoding='utf-8')

    def test_ready_file_does_not_unlock_ui_before_worker_cleanup(self):
        result = separation.status(self.task)
        self.assertEqual(result['state'],'processing')
        self.assertNotIn('finished_at',result)
        self.task['_settled']=True
        self.assertEqual(separation.status(self.task)['state'],'ready')

    def test_published_ready_means_the_next_job_can_acquire_the_slot(self):
        lock=threading.Lock()
        lock.acquire()
        process=Mock(returncode=0)
        process.poll.return_value=0
        with patch.object(separation,'ACTIVE',lock),patch.object(separation.subprocess,'Popen',return_value=process):
            separation.run(ROOT,self.task,self.directory/'request.json')
            self.assertEqual(separation.status(self.task)['state'],'ready')
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

    def test_cancelled_worker_releases_the_slot_and_keeps_cancelled_state(self):
        lock=threading.Lock()
        lock.acquire()
        self.task['_cancel'].set()
        process=Mock(returncode=0)
        process.poll.return_value=None
        with patch.object(separation,'ACTIVE',lock),patch.object(separation.subprocess,'Popen',return_value=process):
            separation.run(ROOT,self.task,self.directory/'request.json')
            self.assertFalse(lock.locked())
            self.assertEqual(separation.status(self.task)['state'],'cancelled')


if __name__=='__main__':
    unittest.main()
