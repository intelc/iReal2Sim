"""Serve local reconstruction artifacts with byte ranges for reliable video seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


class ViewerHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Accept-Ranges','bytes')
        super().end_headers()

    def send_head(self):
        self.request_range=None
        path=self.translate_path(self.path)
        header=self.headers.get('Range')
        if not header or os.path.isdir(path):
            return super().send_head()
        try:
            file=open(path,'rb')
        except OSError:
            self.send_error(404,'File not found');return None
        stat=os.fstat(file.fileno());size=stat.st_size
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
        try:
            if match is None or not any(match.groups()):raise ValueError()
            a,b=match.groups()
            if a:
                start=int(a);end=min(int(b),size-1) if b else size-1
            else:
                length=int(b)
                if length<=0:raise ValueError()
                start=max(0,size-length);end=size-1
            if start<0 or start>=size or end<start:raise ValueError()
        except ValueError:
            file.close();self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return None
        self.send_response(206);self.send_header('Content-type',self.guess_type(path));self.send_header('Content-Length',str(end-start+1));self.send_header('Content-Range',f'bytes {start}-{end}/{size}');self.send_header('Last-Modified',self.date_time_string(stat.st_mtime));self.end_headers()
        self.request_range=(start,end);file.seek(start);return file

    def copyfile(self,source,outputfile):
        if self.request_range is None:return super().copyfile(source,outputfile)
        remaining=self.request_range[1]-self.request_range[0]+1
        try:
            while remaining:
                chunk=source.read(min(65536,remaining))
                if not chunk:break
                outputfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError):
            pass  # The media client can abandon a range when the user seeks again.


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,default=Path('build/reconstruction'));p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    handler=partial(ViewerHandler,directory=str(a.directory.resolve()))
    with ThreadingHTTPServer(('127.0.0.1',a.port),handler) as server:
        print(f'Viewer: http://127.0.0.1:{a.port}',flush=True);server.serve_forever()
