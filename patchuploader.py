#!/usr/bin/env python
import subprocess
import tempfile
import shutil
import time
import os

os.environ['PATH'] = os.environ['PATH'] + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/usr/games"

from flask import Flask, render_template, request, Response
app = Flask(__name__)

os.chdir(os.path.normpath(os.path.split(__file__)[0]))

from werkzeug.contrib.cache import FileSystemCache

cache = FileSystemCache('cache')

def get_projects():
    projects = cache.get('projects')
    if projects is None:
        p = subprocess.Popen(['ssh', 'gerrit', 'gerrit ls-projects'], stdout=subprocess.PIPE)
        stdout, stderr = p.communicate()
        projects = stdout.split("\n")
        cache.set('projects', projects)
    return projects

@app.route("/bla")
def test():
	return "\n".join(k + ": " + v for (k,v) in os.environ.items())

@app.route("/")
def hello():
   return render_template('index.html', projects=get_projects())

@app.route("/submit", methods=["POST"])
def submit():
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

    return Response(apply_and_upload(project, committer, message, patch), mimetype='text/plain')

def apply_and_upload(project, committer, message, patch):
    tempd = tempfile.mkdtemp()
    try:
        cmd = ['git', 'clone', '--depth=1', 'ssh://gerrit/' + project, tempd]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            return

        cmd = ['git', 'config', 'user.name', 'Gerrit Patch Uploader']
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            return

        cmd = ['git', 'config', 'user.email', 'gerritreviewerbot@gmail.com']
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            return

        yield "\nscp -p gerrit:hooks/commit-msg .git/hooks/"
	p = subprocess.Popen(["scp", "-p", "gerrit:hooks/commit-msg", ".git/hooks"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            return

        yield "\npatch -p0 < patch\n"
        p = subprocess.Popen(["patch", "-p0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(patch)[0]
        if p.returncode != 0:
            return

        yield "\ngit commit -a --committer=\"" + committer + "\" -F - < message\n"
        p = subprocess.Popen(["git", "commit", "-a", "--author=" + committer, "-F", "-"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(message)[0]
        if p.returncode != 0:
            return

        yield "\ngit push origin HEAD:refs/for/master\n"
	p = subprocess.Popen(["git", "push", "origin", "HEAD:refs/for/master"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(message)[0].replace("[K", "")
        if p.returncode != 0:
            return

    finally:
        shutil.rmtree(tempd)

if __name__ == "__main__":
    app.run(debug=True)
