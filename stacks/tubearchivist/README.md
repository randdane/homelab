# TubeArchivist

**What:** Subscribes to YouTube channels, downloads their videos, indexes
them with metadata and subtitles, and serves the result as a local library.
**Why I care:** It is the only stack here that preserves something which can
genuinely disappear. Everything else can be re-acquired; a deleted video
cannot.
**URL:** http://localhost:8001

## Notes

**Three services, and Elasticsearch is the expensive one.** The heap is capped
at 1GB (`ES_JAVA_OPTS`) because Elasticsearch will otherwise size itself
against the whole machine and starve the other stacks. Do not raise it without
checking what else is running.

**`archivist-es` is amd64-only.** `bbilly1/tubearchivist-es` is TubeArchivist's
own build; on an arm64 server, substitute the official `elasticsearch` image at
the same major version. Verified amd64 here.

**`TA_HOST` must include the scheme and port** and match how you actually
reach it. TubeArchivist builds absolute URLs from it, so a wrong value gives a
site whose links all point somewhere unreachable.

**The video library is excluded from the nightly tarball**, deliberately. It
will reach hundreds of GB, and `backup` would try to tar it every night. The
Elasticsearch index *is* backed up — that is the metadata, watch state, and
subtitles, which represent real curation work and are small.

**This creates a real gap you should decide about:** the videos themselves
have no backup. Options are a second copy on different storage, or accepting
that a disk failure means re-downloading whatever is still available — which
is precisely the risk this stack exists to hedge against. Duplicati pointed at
the media directory is the obvious answer if the library matters.

**Watch the disk.** There is no size cap. A handful of subscribed channels
becomes hundreds of gigabytes without any warning, and the failure mode is a
full filesystem that takes down every other stack on the host.

**It downloads from YouTube, which fights back.** Expect periodic breakage
after YouTube changes something; updates usually follow within days. Do not
put this in Watchtower's scope regardless — it migrates its Elasticsearch
index between versions.
