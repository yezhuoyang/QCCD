# The one process behind qccd.academy

The site is static files under `/var/www/qccd.academy` (see `docs/WEBSITE_PLAN.md` §10).
Accounts and comments are the one thing that cannot be static: `../comments_api.py`, a
standard-library Python server on `127.0.0.1:8200`, proxied by nginx at `/api/`, storing
users, sessions and threads in SQLite.  This directory holds what the server needs.

| file | goes to | does |
|---|---|---|
| `../comments_api.py` | `/home/qccd/comments_api.py` | the API; `python3 comments_api.py --help` |
| `qccd-comments.service` | `/etc/systemd/system/` | runs it as user `qccd`, database `/var/lib/qccd/comments.db` |
| `nginx-api.conf` | `/etc/nginx/snippets/qccd-api.conf` | `include snippets/qccd-api.conf;` inside the HTTPS server block of `sites-available/qccd.academy` |
| `mail.env.example` | `/etc/qccd/mail.env` | the SMTP relay the invitation mail goes out through (optional) |

First install, as root on the droplet:

```sh
install -d -o qccd -g qccd -m 750 /var/lib/qccd
install -o qccd -g qccd -m 644 comments_api.py /home/qccd/comments_api.py
cp qccd-comments.service /etc/systemd/system/ && systemctl daemon-reload
cp nginx-api.conf /etc/nginx/snippets/qccd-api.conf   # then add the include line and: nginx -t && systemctl reload nginx
install -d -m 755 /etc/qccd && install -o root -g qccd -m 640 mail.env.example /etc/qccd/mail.env   # optional; edit it, see below
systemctl enable --now qccd-comments
echo 'qccd ALL=(root) NOPASSWD: /bin/systemctl restart qccd-comments' > /etc/sudoers.d/qccd-comments && chmod 440 /etc/sudoers.d/qccd-comments
curl -s https://qccd.academy/api/health        # {"ok":true}
```

The admin is whoever `--admins` in the unit names; the account itself is made (and its
password set or reset) from the command line, never by registering, so nobody else can
claim the address:

```sh
sudo -u qccd python3 /home/qccd/comments_api.py passwd --db /var/lib/qccd/comments.db yezhuoyang@cs.ucla.edu --name "John Zhuoyang Ye"
sudo -u qccd python3 /home/qccd/comments_api.py users   --db /var/lib/qccd/comments.db
sudo -u qccd python3 /home/qccd/comments_api.py threads --db /var/lib/qccd/comments.db
```

`passwd` also resets any reader's forgotten password.

## Who gets in

The site is by invitation: an account exists only because an admin named its address and
somebody followed the link that was mailed to it.  Everything below is on the *Readers and
invitations* screen in the admin's own menu on any page -- type an address, press
**Invite**, and the message goes out -- and the same things are on the command line when
the panel is not to hand:

```sh
sudo -u qccd python3 /home/qccd/comments_api.py invite  --db /var/lib/qccd/comments.db someone@university.edu --name "Their Name" --send
sudo -u qccd python3 /home/qccd/comments_api.py invites --db /var/lib/qccd/comments.db
sudo -u qccd python3 /home/qccd/comments_api.py access  --db /var/lib/qccd/comments.db someone@university.edu --revoke   # and --allow
```

An invitation is one link, good for 14 days and for one account, at the address it names;
inviting the same address again replaces it, and an unused one can be withdrawn.
Revoking an account signs it out on the spot.  The readers who registered before any of
this existed keep their accounts: `users` lists them, `access --revoke` closes any that
should not be there.

`invite --send` and the panel both need `/etc/qccd/mail.env`; see `mail.env.example` for
Gmail (an app password) or a transactional relay.  Without it the invitation is still
made and the panel shows the link to send by hand -- it is the mail that is optional, not
the invitation.  Check the relay end to end by inviting yourself at another address.

Every later deploy is `.github/workflows/site.yml`: it rsyncs the site and
`comments_api.py` as `qccd` and restarts the unit through that one sudo rule.  The
database is never touched by a deploy; back it up with
`sqlite3 /var/lib/qccd/comments.db ".backup /root/comments-$(date +%F).db"`.
