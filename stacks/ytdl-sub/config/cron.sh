# Sourced by the image's cron wrapper (not executed), from /config.
# If the virtiofs share is not mounted, /mnt/youtube is an empty directory on
# the VM's own disk, and a night of downloads would fill it.
if [ "$(stat -f -c %T /tv_shows)" != "virtiofs" ]; then
  echo "ERROR: /tv_shows is $(stat -f -c %T /tv_shows), not virtiofs; is /mnt/youtube mounted on the host? Skipping."
else
  ytdl-sub --config /ytdl/config.yaml sub /ytdl-site/subscriptions.yaml
fi
