#!/usr/bin/env python
import subprocess
import tempfile
import shutil
import os
import re
import xmlrpclib

os.environ['PATH'] = os.environ['PATH'] + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/usr/games"
os.environ['LANG'] = 'en_US.UTF-8'
os.chdir(os.path.normpath(os.path.split(__file__)[0]))

import jinja2
from flask import Flask, render_template, request, Response, redirect, url_for, flash
from werkzeug.contrib.cache import FileSystemCache
from flask_mwoauth import MWOAuth

import config

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
   return render_template('index.html', projects=get_projects(), username=mwoauth.get_current_user(), committer_email=config.committer_email)

@app.route("/bugzilla/fromurl", methods=["GET"])
def bz_fromurl(oldurl=''):
    return render_template('bzfromurl_welcome.html', oldurl=oldurl)

patch_content_types = ['text/plain']

@app.route("/bugzilla/fromurl", methods=["POST"])
def bz_fromurl_post():
    url = request.form.get('url')
    if not isinstance(url, basestring):
        return bz_fromurl()

    if not u'attachment.cgi' in url and not u'bug.cgi' in url:
        flash('Error: URL not recognised. Does it contain attachment.cgi/bug.cgi?')
        return bz_fromurl(url)

    try:
        id = int(re.search(r'id=([0-9]+)', url).groups()[0])
    except AttributeError:
        flash('Error: could not parse ID in string. Does it contain an id=... parameter?')
        return bz_fromurl(url)

    if u'attachment.cgi' in url:
        return redirect(url_for('upload_bugzilla_patch', patchid=id))

    satts = bzsp.Bug.attachments({'ids': id})['bugs'].values()[0]
    atts = [att for att in satts if att['content_type'] in patch_content_types]
    if len(atts) == 0:
        flash('Error: no viable attachments for bug %i' % id)
        for att in satts:
            flash(jinja2.Markup('Found: %s, but with content-type %s, which is not allowed') % (att['file_name'], att['content_type']))
        return bz_fromurl(url)

    if len(atts) == 1:
        att = atts[0]
        return upload_bugzilla_patch(att['id'], att)

    return render_template('bzfromurl_chooseatt.html', attachments=atts)

@app.route("/bugzilla/<int:patchid>")
def upload_bugzilla_patch(patchid, att=None):
    patchid = int(patchid)

    if not att:
        att = bzsp.Bug.attachments({'attachment_ids': patchid})['attachments'].values()[0]
    if att['content_type'] not in patch_content_types:
        return jinja2.Markup("Content-type not in %r; got %s instead") % (patch_content_types, att['content_type'])
    user = bzsp.User.get({'names': att['creator']})['users'][0]

    author = user['real_name'] + " <" + user['name'] + "> "
    commitmessage = att['summary'] + "\n\nBug: " + str(att['bug_id'])

    patch = att['data'].data
    try:
        patch = patch.decode('utf-8')
    except UnicodeDecodeException:
        patch = patch.decode('latin-1')

    return render_template('index.html', projects=get_projects(), username=mwoauth.get_current_user(), committer_email=config.committer_email,
                           author=author, commitmessage=commitmessage, patch=patch)

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
    fpatch = request.files['fpatch']
    if fpatch:
        patch = fpatch.stream.read()
    else:
        patch = request.form['patch']
    if not patch:
        return 'patch not set'

    return Response(jinja2.escape(e) for e in apply_and_upload(user, project, committer, message, patch))

def run_command(cmd):
    yield " ".join(cmd) + "\n"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = p.communicate()[0].split("\n")
    lines = "\n".join([line for line in lines if "[K" not in line])
    yield lines

def apply_and_upload(user, project, committer, message, patch):
    yield jinja2.Markup("Result from uploading patch: <br><div style='font-family: monospace;white-space: pre;'>")
    tempd = tempfile.mkdtemp()
    try:
        cmd = ['git', 'clone', '--depth=1', 'ssh://gerrit/' + project, tempd]
        yield " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Clone failed")

        cmd = ['git', 'rev-parse', '--abbrev-ref', 'HEAD']
        yield "\n" + " ".join(cmd) + "\n"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        branch = p.communicate()[0]
        if p.returncode != 0:
            raise Exception("Could not determine branch")
        branch = branch.strip()
        yield jinja2.Markup("Will commit to branch: %s\n\n" % branch)

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

        yield "\n"
        patch_commands = [["git", "apply"], ["patch", "-p0"], ["patch", "-p1"]]
        for pc in patch_commands:
            yield "\n" + " ".join(pc) + " < patch\n"
            p = subprocess.Popen(pc, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
            yield p.communicate(patch.replace('\r\n', '\n').encode('utf-8'))[0]
            if p.returncode == 0:
                break
        yield "\n"
        if p.returncode != 0:
            raise Exception("Patch failed (is your patch in unified diff format, and does it patch apply cleanly to master?)")

        yield "\ngit commit -a --committer=\"" + committer + "\" -F - < message\n"
        p = subprocess.Popen(["git", "commit", "-a", "--author=" + committer.encode('utf-8'), "-F", "-"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        yield p.communicate(message.replace('\r\n', '\n').encode('utf-8'))[0]
        if p.returncode != 0:
            raise Exception("Commit failed (incorrect format used for author?)")

        yield jinja2.Markup("\ngit push origin HEAD:refs/for/%s\n") % branch
        p = subprocess.Popen(["git", "push", "origin", "HEAD:refs/for/%s" % branch],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tempd)
        pushresult = p.communicate(message)[0].replace("\x1b[K", "")
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

        if len(patches) == 1:
            yield "Automatically redirecting in 5 seconds..."
            yield jinja2.Markup('<meta http-equiv="refresh" content="5; url=%s">') % (patch,)
    except Exception, e:
        yield jinja2.Markup("</div>")
        yield jinja2.Markup("<b>Upload failed</b><br>")
        yield jinja2.Markup("Reason: <i>%s</i> (check log above for details)") % e
    finally:
        shutil.rmtree(tempd)


if __name__ == "__main__":
    app.run(debug=True)
