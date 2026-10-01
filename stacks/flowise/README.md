# Flowise

**What:** Drag-and-drop builder for LLM chains, agents, and retrieval flows.
**Why I care:** Prototyping a flow visually is faster than wiring one up in
code, and the result can be called over HTTP.
**URL:** http://localhost:3000

## Notes

**`lifecycle: developing`, deliberately.** Of everything migrated, this is the
most speculative — the classic "stood it up once" candidate. Leaving it
`developing` means the tracker shows it as an experiment rather than something
the house depends on. If it is still `developing` and untouched in six months,
that is the tracker doing its job: drop it.

**The legacy definition had no authentication whatsoever.** Flowise stores
model-provider API keys and will happily spend them, so an open instance is a
billing incident waiting to happen. `FLOWISE_USERNAME`/`FLOWISE_PASSWORD` are
required here.

**`data` holds the encrypted secret store** alongside the flows themselves —
hence `data: precious`, even for an experiment. Losing it means re-entering
provider keys.

**All four path variables point at the same volume** (`DATABASE_PATH`,
`APIKEY_PATH`, `SECRETKEY_PATH`, `LOG_PATH`). Flowise defaults them to
separate locations, some outside any volume, so a restart quietly loses part
of its state.

**Never expose this publicly.** An LLM endpoint reachable from the internet
with your API keys behind it is somebody else's free inference.
