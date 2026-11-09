# Sourced by the image's cron wrapper (not executed), from /config, so no
# `exit`: it would end the wrapper too. The outcome goes to healthchecks.io
# instead: a success ping, a /fail ping, or (a run that never happened)
# silence past the check's period. The wrapper's own exit status reaches
# nobody, so it is not relied on.
ytdl_ping() {  # $1: "" for success, "/fail" for failure
  if [ -z "${HC_URL:-}" ]; then
    echo "WARNING: HC_URL is unset; this run's result reaches nobody."
    return 0
  fi
  curl -fsS -m 10 --retry 3 -o /dev/null "${HC_URL}$1" \
    || echo "WARNING: healthchecks ping failed"
}

# If the virtiofs share is not mounted, /mnt/youtube is an empty directory on
# the VM's own disk, and a night of downloads would fill it.
if [ "$(stat -f -c %T /tv_shows)" != "virtiofs" ]; then
  echo "ERROR: /tv_shows is $(stat -f -c %T /tv_shows), not virtiofs; is /mnt/youtube mounted on the host? Skipping."
  ytdl_ping /fail
elif ytdl-sub --config /ytdl/config.yaml sub /ytdl-site/subscriptions.yaml; then
  ytdl_ping ""
else
  echo "ERROR: ytdl-sub exited $?"
  ytdl_ping /fail
fi
