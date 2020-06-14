#!/usr/bin/env python
import subprocess
import tempfile
import os
import re
import pipes
import binascii

import jinja2
from flask import Flask, render_template, request, Response, session
from cachelib import FileSystemCache
from flask_mwoauth import MWOAuth

FILE_DIR = os.path.abspath(os.path.split(__file__)[0])
os.chdir(FILE_DIR)

import config  # noqa, needs to be loaded from local path

GIT_PATH = 'git'
PATCH_PATH = 'patch'
RUN_ENV = {'PATH': FILE_DIR + "/bin", 'LANG': 'en_US.UTF-8', 'LD_LIBRARY_PATH': FILE_DIR + "/lib"}

app = Flask(__name__)
app.secret_key = config.app_secret_key

mwoauth = MWOAuth(consumer_key=config.oauth_key, consumer_secret=config.oauth_secret)
mwoauth.handshaker.user_agent = 'Gerrit-Patch-Uploader by valhallasw using MWOAuth - http://tools.wmflabs.org/gerrit-patch-uploader'
app.register_blueprint(mwoauth.bp)

cache = FileSystemCache('cache')


def get_projects():
    projects = cache.get('projects')
    if projects is None:
        p = subprocess.Popen(['ssh', 'gerrit', 'gerrit ls-projects'], stdout=subprocess.PIPE, env=RUN_ENV)
        stdout, stderr = p.communicate()
        projects = stdout.decode("utf-8", "replace").strip().split("\n")
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

    if 'fpatch' in request.files:
        patch = request.files['fpatch'].stream.read()
    if not patch:
        patch = request.form['patch'].replace("\r\n", "\n").encode('utf-8')
    if not patch:
        return 'patch not set'

    note = """This commit was uploaded using the Gerrit Patch Uploader [1].

Please contact the patch author, %s, for questions/improvements.

[1] https://tools.wmflabs.org/gerrit-patch-uploader/""" % committer

    return Response(jinja2.escape(e) for e in apply_and_upload(user, project, committer, message, patch, note))


def prepare_message(message):
    message = message.replace("\r\n", "\n")
    message = message.split("\n")

    if not message[-1].startswith('Change-Id: '):
        if not re.match(r"[a-zA-Z\-]+: ", message[-1]):
            message.append("")
        message.append('Change-Id: I%s' % binascii.b2a_hex(os.urandom(20)).decode('ascii'))

    return "\n".join(message) + "\n"


def apply_and_upload(user, project, committer, message, patch, note=None):
    yield jinja2.Markup("Result from uploading patch: <hr><div style='font-family: monospace;white-space: pre;'>")

    with tempfile.TemporaryDirectory() as tempd:
        def run_command(cmd, *, stdin=None, stdin_name=None):
            yield jinja2.Markup("<b>")
            yield " ".join(cmd)
            if stdin_name:
                yield " < " + stdin_name
            elif stdin:
                yield jinja2.Markup("\n<div style='margin-left:2em;border-left:1px solid black;padding-left:1em'>")
                yield stdin.decode('utf-8', 'replace')
                yield jinja2.Markup("</div>")

            yield jinja2.Markup("</b>\n<br><i>")
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=tempd,
                env=RUN_ENV)

            stdout = p.communicate(stdin)[0].replace(b"\x1b[K", b"").decode('utf-8', 'replace')
            yield stdout
            yield jinja2.Markup("</i><hr>")

            return p, stdout

        try:
            p, _ = yield from run_command(
                [GIT_PATH, 'clone', "-v", "-v", '--depth=1', 'ssh://gerrit/' + project, tempd])
            if p.returncode != 0:
                raise Exception("Clone failed")

            p, stdout = yield from run_command(
                [GIT_PATH, 'rev-parse', '--abbrev-ref', 'HEAD'])
            if p.returncode != 0:
                raise Exception("Could not determine branch")

            branch = stdout.strip()

            p, _ = yield from run_command(
                [GIT_PATH, 'config', 'user.name', '[[mw:User:%s]]' % user])
            if p.returncode != 0:
                raise Exception("Git Config failed (should never happen)!")

            p, _ = yield from run_command(
                [GIT_PATH, 'config', 'user.email', config.committer_email])
            if p.returncode != 0:
                raise Exception("Git Config failed (should never happen)!")

            patch_commands = [
                [GIT_PATH, "apply"],
                [PATCH_PATH, "--no-backup-if-mismatch", "-p0", "-u"],
                [PATCH_PATH, "--no-backup-if-mismatch", "-p1", "-u"],
            ]
            for pc in patch_commands:
                p, _ = yield from run_command(pc, stdin=patch, stdin_name="patch")
                if p.returncode == 0:
                    break

            if p.returncode != 0:
                raise Exception(
                    "Patch failed (is your patch in unified diff format, and does it patch apply cleanly to master?)"
                )

            p, _ = yield from run_command(
                [GIT_PATH, "add", "-A"])
            if p.returncode != 0:
                raise Exception("Git add failed (were no files changed?)")

            message = prepare_message(message)
            p, _ = yield from run_command(
                [GIT_PATH, "commit", "-a", "--author=" + committer, "-F", "-"], stdin=message.encode('utf-8'))
            if p.returncode != 0:
                raise Exception("Commit failed (incorrect format used for author?)")

            p, stdout = yield from run_command(
                [GIT_PATH, "rev-list", "-1", "HEAD"])
            if p.returncode != 0:
                raise Exception("Could not determine commit SHA1")
            sha1 = stdout.strip()

            p, pushresult = yield from run_command(
                [GIT_PATH, "push", "origin", "HEAD:refs/for/%s" % branch])
            if p.returncode != 0:
                raise Exception("Push failed")

            yield jinja2.Markup("</div><br>")

            yield "Uploaded patches:"
            yield jinja2.Markup("<ul>")
            patches = re.findall('https://gerrit.wikimedia.org/[^ ]*', pushresult)

            for patch in patches:
                yield jinja2.Markup('<li><a href="%s">%s</a>') % (patch, patch)
            yield jinja2.Markup("</ul>")

            if note:
                yield jinja2.Markup("<div>Submitting note: %s</div><br>") % note
                note = pipes.quote(note)
                sha1 = pipes.quote(sha1)
                p = subprocess.Popen(
                    ["ssh", "gerrit", "gerrit review %s -m %s" % (sha1, note)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=tempd,
                    env=RUN_ENV)
                p.communicate()
                if p.returncode != 0:
                    raise Exception("Note could not be submitted correctly")

            if len(patches) == 1:
                yield "Automatically redirecting in 5 seconds..."
                yield jinja2.Markup('<meta http-equiv="refresh" content="5; url=%s">') % (patch,)
        except Exception as e:
            yield jinja2.Markup("</div>")
            yield jinja2.Markup("<b>Upload failed</b><br>")
            yield jinja2.Markup("Reason: <i>%s</i> (check log above for details)") % e

            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    app.run(debug=True)
