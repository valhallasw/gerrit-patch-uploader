#!/usr/bin/env python
import subprocess
import tempfile
import shutil
import os
import re

os.environ['PATH'] = os.environ['PATH'] + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/usr/games"
os.environ['LANG'] = 'en_US.UTF-8'
os.chdir(os.path.normpath(os.path.split(__file__)[0]))

from flask import Flask, render_template, request, Response
from werkzeug.contrib.cache import FileSystemCache
from flask_mwoauth import MWOAuth

import config

app = Flask(__name__)
app.secret_key = config.app_secret_key

mwoauth = MWOAuth(consumer_key=config.oauth_key, consumer_secret=config.oauth_secret)
app.register_blueprint(mwoauth.bp)

cache = FileSystemCache('cache')


def get_projects():
    projects = cache.get('projects')
    if projects is None:
        p = subprocess.Popen(['ssh', 'gerrit', 'gerrit ls-projects'], stdout=subprocess.PIPE)
        stdout, stderr = p.communicate()
        projects = stdout.split("\n")
        cache.set('projects', projects)
    return projects

@app.route("/")
def index():
   return render_template('index.html', projects=get_projects(), username=mwoauth.get_current_user(), committer_email=config.committer_email)

@app.route("/submit", methods=["POST"])
def submit():
    user = mwoauth.get_current_user(False)
    if not user:
        return "Must be logged in"

    if request.method != 'POST':
        return "can only POST"
    project = request.form['project']
    if project not in get_projects():
        return "project unknown"
    committer = request.form['committer']
    if not committer:
        return 'committer not set'
    message = request.form['message']
    if not message:
        return 'message not set'
    patch = request.form['patch']
    if not patch:
        return 'patch not set'

    return Response(apply_and_upload(user, project, committer, message, patch))

def run_command(cmd):
    yield " ".join(cmd) + "\n"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = p.communicate()[0].split("\n")
    lines = "\n".join([line for line in lines if "[K" not in line])
    yield lines

def apply_and_upload(user, project, committer, message, patch):
    yield "Result from uploading patch: <br><pre>"
    tempd = tempfile.mkdtemp()
    try:
        cmd = ['git', 'clone', '--depth=1', 'ssh://gerrit/' + project, tempd]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Clone failed")

        cmd = ['git', 'config', 'user.name', '[[mw:User:%s]]' % user.encode('utf-8')]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Git Config failed (should never happen)!")

        cmd = ['git', 'config', 'user.email', config.committer_email]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Git Config failed (should never happen)!")

        yield "\nscp -p gerrit:hooks/commit-msg .git/hooks/"
        p = subprocess.Popen(["scp", "-p", "gerrit:hooks/commit-msg", ".git/hooks"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Installing commit message hook failed")

        yield "\npatch -p0 < patch\n"
        p = subprocess.Popen(["patch", "-p0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(patch)[0]
        if p.returncode != 0:
            raise Exception("Patch failed (is your patch in unified diff format, and does it patch apply cleanly to master?)")

        yield "\ngit commit -a --committer=\"" + committer + "\" -F - < message\n"
        p = subprocess.Popen(["git", "commit", "-a", "--author=" + committer.encode('utf-8'), "-F", "-"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(message)[0]
        if p.returncode != 0:
            raise Exception("Commit failed (incorrect format used for author?)")

        yield "\ngit push origin HEAD:refs/for/master\n"
        p = subprocess.Popen(["git", "push", "origin", "HEAD:refs/for/master"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        pushresult = p.communicate(message)[0].replace("[K", "")
        yield pushresult
        if p.returncode != 0:
            raise Exception("Push failed")

        yield "</pre><br>"

        yield "Uploaded patches:"
        yield "<ul>"
        patches = re.findall('https://gerrit.wikimedia.org/.*', pushresult)
        for patch in patches:
            yield '<li><a href="' + patch + '">' + patch + "</a></li>"
        yield "</ul>"

        if len(patches) == 1:
            yield "Automatically redirecting in 5 seconds..."
            yield '<meta http-equiv="refresh" content="5; url=' + patch + '">'
    except Exception, e:
        yield "</pre>"
        yield "<b>Upload failed</b><br>"
        yield "Reason: <i>" + str(e) + "</i> (check log above for details)"
    finally:
        shutil.rmtree(tempd)


if __name__ == "__main__":
    app.run(debug=True)
