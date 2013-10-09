#!/usr/bin/env python
from wsgiref.handlers import CGIHandler
from app import app

CGIHandler().run(app)
