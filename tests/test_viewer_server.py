from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from pipeline.serve_viewer import ViewerHandler


class VideoRangeTest(unittest.TestCase):
    def test_media_ranges_are_exact_and_unsatisfiable_ranges_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data=bytes(range(256))*20
            Path(directory,'video.mp4').write_bytes(data)
            class QuietHandler(ViewerHandler):
                def log_message(self,*args):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=directory))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}/video.mp4'
                for value,expected in [('bytes=100-299',data[100:300]),('bytes=-120',data[-120:]),('bytes=5000-',data[5000:])]:
                    with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':value})) as response:
                        self.assertEqual(response.status,206);self.assertEqual(response.read(),expected);self.assertEqual(response.headers['Accept-Ranges'],'bytes')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=99999-'}))
                self.assertEqual(error.exception.code,416)
            finally:
                server.shutdown();server.server_close();thread.join()
