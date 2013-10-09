gerrit-patch-uploader
=====================

Web-based patch uploader for WMF's (or any other, if you use a different .ssh/config) gerrit
system. Choose a project, enter committer name and email, commit message and unified diff, and
press 'submit'.

The uploader will apply the patch and upload it for you to Gerrit. Check the response for an 
remote: New Changes:
remote:   https://gerrit.wikimedia.org/r/...

line and follow that URL.


To upload an update to an existing patch, copy the Change-Id line from that changeset, and add it to the
bottom of your commit message.

Implementation notes
--------------------
We assume the gerrit instance is available using the SSH alias 'gerrit'. See sshconfig-example for an
example of what to put in ~/.ssh/config.

The app now runs over CGI, via index.py.

