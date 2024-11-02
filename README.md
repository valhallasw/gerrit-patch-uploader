gerrit-patch-uploader
=====================

Web-based patch uploader for WMF's (or any other, if you use a different .ssh/config) gerrit
system. Choose a project, enter committer name and email, commit message and unified diff, and
press 'submit'.

The uploader will apply the patch and upload it for you to Gerrit. Check the response for an 
```
remote: New Changes:
remote:   https://gerrit.wikimedia.org/r/...
```

line and follow that URL.


To upload an update to an existing patch, copy the Change-Id line from that changeset, and add it to the
bottom of your commit message. Ensure there is NO empty line at the end of the message.

Implementation notes
--------------------
We assume the gerrit instance is available using the SSH alias 'gerrit'. See sshconfig-example for an
example of what to put in ~/.ssh/config.

The app now runs over uwsgi, via `~/www/python/src/app.py` .


Running locally
------------
```bash
git clone https://github.com/valhallasw/gerrit-patch-uploader
cd gerrit-patch-uploader
# if necessary, install your system's python-httplib2 package here. The one on pypi doesn't have
# WMF's server certificates
virtualenv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

cp config.py.example config.py
# generate oauth keys on https://www.mediawiki.org/wiki/Special:MWOAuthConsumerRegistration, or just fill in empty values
edit config.py # adapt all four variables

cat sshconfig-example >> ~/.ssh/config
edit ~/.ssh/config # adapt identityfile, or remove the line to use ~/.ssh/id_rsa et al.

eval `ssh-agent`
ssh-add

python patchuploader.py
```

Deploying
---------
Make sure you're in the gerrit-patch-uploader Toolforge project. Login to Toolforge, `become gerrit-patch-uploader`, then
```
cd www/python/src/
git pull
webservice stop
webservice python3.11 start
```


From scratch
------------

- `mkdir -r www/python`
- `cd www/python`
- `git checkout https://github.com/valhallasw/gerrit-patch-uploader src`
- `webservice python3.11 shell`
  - `cd www/python`
  - `python3 -m venv venv`
  - `venv/bin/pip install -r src/requirements.txt`
  - `exit`
- `cd ../..`
- `cp www/python/src/service.template .`
- `webservice start`
