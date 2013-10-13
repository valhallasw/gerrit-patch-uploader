#!/data/project/gerrit-patch-uploader/bin/python
from flup.server.fcgi import WSGIServer
from patchuploader import app

from flask import request

import time
import logging
from logging import FileHandler
logger = FileHandler('error.log')
app.logger.setLevel(logging.DEBUG)
app.logger.addHandler(logger)
app.logger.debug(u"Flask server started " + time.asctime())

@app.after_request
def write_access_log(response):
    app.logger.debug(u"%s %s -> %s" % (time.asctime(), request.path, response.status_code))
    return response

if __name__ == '__main__':
    WSGIServer(app).run()
