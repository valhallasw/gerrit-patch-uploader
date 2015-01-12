#!/usr/bin/env python
import subprocess
import tempfile
import os
import re
import xmlrpclib
import pipes

os.environ['PATH'] += ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/usr/games"
os.environ['LANG'] = 'en_US.UTF-8'
os.chdir(os.path.normpath(os.path.split(__file__)[0]))

import jinja2
from flask import Flask, render_template, request, Response, session
from werkzeug.contrib.cache import FileSystemCache
from flask_mwoauth import MWOAuth

import config

GIT_PATH = os.path.expanduser('~/git/bin/git')

app = Flask(__name__)
app.secret_key = config.app_secret_key

mwoauth = MWOAuth(consumer_key=config.oauth_key, consumer_secret=config.oauth_secret)
app.register_blueprint(mwoauth.bp)

cache = FileSystemCache('cache')

bzsp = xmlrpclib.ServerProxy('https://bugzilla.wikimedia.org/xmlrpc.cgi')


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
    author = session.get('author', '')
    return render_template('index.html', projects=get_projects(), username=mwoauth.get_current_user(),
                           committer_email=config.committer_email, author=author)


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
    session['author'] = committer
    message = request.form['message']
    if not message:
        return 'message not set'
    fpatch = request.files['fpatch']
    if fpatch:
        patch = fpatch.stream.read()
    else:
        patch = request.form['patch'].encode('utf-8').replace("\r\n", "\n")
    if not patch:
        return 'patch not set'

    note = """This commit was uploaded using the Gerrit Patch Uploader [1].

Please contact the patch author, %s, for questions/improvements.

[1] https://tools.wmflabs.org/gerrit-patch-uploader/""" % committer

    return Response(jinja2.escape(e) for e in apply_and_upload(user, project, committer, message, patch, note))


def run_command(cmd):
    yield " ".join(cmd) + "\n"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = p.communicate()[0].split("\n")
    lines = "\n".join([line for line in lines if "[K" not in line])
    yield lines


def apply_and_upload(user, project, committer, message, patch, note=None):
    yield jinja2.Markup("Result from uploading patch: <br><div style='font-family: monospace;white-space: pre;'>")
    tempd = tempfile.mkdtemp()
    try:
        cmd = [GIT_PATH, 'clone', '--depth=1', 'ssh://gerrit/' + project, tempd]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Clone failed")

        cmd = [GIT_PATH, 'rev-parse', '--abbrev-ref', 'HEAD']
        yield "\n" + " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        branch = p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Could not determine branch")
        branch = branch.strip()
        yield jinja2.Markup("Will commit to branch: %s\n\n" % branch)

        cmd = [GIT_PATH, 'config', 'user.name', '[[mw:User:%s]]' % user.encode('utf-8')]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Git Config failed (should never happen)!")

        cmd = [GIT_PATH, 'config', 'user.email', config.committer_email]
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

        yield "\n"
        patch_commands = [
            [GIT_PATH, "apply"],
            ["patch", "--no-backup-if-mismatch", "-p0"],
            ["patch", "--no-backup-if-mismatch", "-p1"],
        ]
        for pc in patch_commands:
            yield "\n" + " ".join(pc) + " < patch\n"
            p = subprocess.Popen(pc, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
            yield p.communicate(patch)[0].decode('utf-8')  # patch is already bytes, so should not be .encode()d!
            if p.returncode == 0:
                break
        yield "\n"
        if p.returncode != 0:
            raise Exception(
                "Patch failed (is your patch in unified diff format, and does it patch apply cleanly to master?)"
            )

        yield "\n%s add -A\n" % GIT_PATH
        p = subprocess.Popen([GIT_PATH, "add", "-A"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0].decode('utf-8')
        if p.returncode != 0:
            raise Exception("Git add failed (were no files changed?)")

        yield "\n%s commit --author='%s' -F - < message\n" % (GIT_PATH, committer)
        p = subprocess.Popen([GIT_PATH, "commit", "-a", "--author=" + committer.encode('utf-8'), "-F", "-"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(message.replace('\r\n', '\n').encode('utf-8'))[0].decode('utf-8')
        if p.returncode != 0:
            raise Exception("Commit failed (incorrect format used for author?)")

        yield "\n%s rev-list -1 HEAD\n" % GIT_PATH
        p = subprocess.Popen([GIT_PATH, "rev-list", "-1", "HEAD"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        sha1 = p.communicate()[0].decode('utf-8').strip()
        if p.returncode != 0:
            raise Exception("Could not determine commit SHA1")

        yield sha1 + "\n\n"

        yield jinja2.Markup("\n%s push origin HEAD:refs/for/%s\n") % (GIT_PATH, branch)
        p = subprocess.Popen([GIT_PATH, "push", "origin", "HEAD:refs/for/%s" % branch],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        pushresult = p.communicate(message.encode('utf-8'))[0].replace("\x1b[K", "").decode('utf-8')
        yield pushresult
        if p.returncode != 0:
            raise Exception("Push failed")

        yield jinja2.Markup("</div><br>")

        yield "Uploaded patches:"
        yield jinja2.Markup("<ul>")
        patches = re.findall('https://gerrit.wikimedia.org/.*', pushresult)

        for patch in patches:
            yield jinja2.Markup('<li><a href="%s">%s</a>') % (patch, patch)
        yield jinja2.Markup("</ul>")

        if note:
            yield jinja2.Markup("<div>Submitting note: %s</div><br>") % note
            note = pipes.quote(note.encode('utf-8'))
            sha1 = pipes.quote(sha1.encode('utf-8'))
            p = subprocess.Popen(["ssh", "gerrit", "gerrit review %s -m %s" % (sha1, note)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
            p.communicate()
            if p.returncode != 0:
                raise Exception("Note could not be submitted correctly")

        if len(patches) == 1:
            yield "Automatically redirecting in 5 seconds..."
            yield jinja2.Markup('<meta http-equiv="refresh" content="5; url=%s">') % (patch,)
    except Exception, e:
        yield jinja2.Markup("</div>")
        yield jinja2.Markup("<b>Upload failed</b><br>")
        yield jinja2.Markup("Reason: <i>%s</i> (check log above for details)") % e
    finally:
        yield tempd


if __name__ == "__main__":
    app.run(debug=True)
