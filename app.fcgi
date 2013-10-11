#!/data/project/gerrit-patch-uploader/bin/python
from flup.server.fcgi import WSGIServer
from patchuploader import app

if __name__ == '__main__':
    WSGIServer(app).run()
