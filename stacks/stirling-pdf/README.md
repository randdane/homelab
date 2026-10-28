# Stirling PDF

**What:** PDF toolbox: merge, split, rotate, compress, OCR, redact, convert
to and from Office formats.
**Why I care:** Without it, the way to do these is to upload the document
to a PDF website. Pairs with Paperless and the `raven` scanner. Chosen over
BentoPDF for its API.
**URL:** `https://pdf.<PUBLIC_DOMAIN>` (gated vhost, Authentik
forward-auth). `127.0.0.1:8096` on `homelab` is the break-glass path over
ssh, and the way to reach the API from scripts on the host.

**Authentik:** forward-auth, group `admin-uis`. Stirling's own login is off
(`SECURITY_ENABLELOGIN=false`), because with it on, Stirling keeps a user
database. Its server runs LibreOffice, Tesseract and qpdf on whatever is
uploaded, so it gets a login in front even though it stores nothing.

## Notes

Stateless. It still writes a settings file and an empty database under
`/configs` in the container; nothing in them is used with login off, and a
recreate loses nothing.

`:latest` is deliberate, as for it-tools: no on-disk state to migrate.

OCR languages in the image: English, German, French, Simplified Chinese and
a couple more (`tesseract --list-langs`). Others need the `-fat` image or
mounted tessdata.

**What will bite you:** around 1 GB RAM while running, the largest of the
single-container tools. A big OCR job takes more.

**Unverified:** OCR quality on `raven` scans, and handing results to
Paperless.

**Stopped when idle** by `stacks/sablier` since 2026-10-28: 30 minutes
after the last request. The next visit shows a waiting page for about 75 s,
then the app. `127.0.0.1:8096` answers only while it is running; start it with
`docker start stirling-pdf` for API use from the host.
