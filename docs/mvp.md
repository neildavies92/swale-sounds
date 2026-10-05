# Swale Sounds MVP

## Objective

Prove that Swale Sounds can repeatedly turn structured content hypotheses into
published YouTube videos, measure audience response, and determine which content
clusters deserve further investment, while keeping music-generation cost close
to zero.

The MVP is a repeatable series of controlled publish/measure experiments, not
full automation. Phase 1 local production is complete. Phase 2 now includes local
publication planning; uploading, publication tracking, and analytics remain
future work.

## Production loop and sourcing

Content hypothesis → Session specification → YouTube Audio Library music →
optional ambience → artwork → existing audio renderer → existing video renderer
→ publication package → YouTube → analytics → next experiment.

YouTube Audio Library is the default MVP music source. The operator manually
acquires files, checks their licence and attribution requirements, and imports
them through the existing Asset workflow. Prefer tracks that do not require
attribution for the initial experiment set to reduce licence-description
complexity during demand validation. Record the checked evidence using the
[existing provenance options](../README.md#source-asset-import-and-provenance).

OpenAI artwork generation remains optional. Original/AI music generation is
deferred until audience demand is demonstrated: early investment should establish
which content people respond to before funding differentiated music.

Retain the session-scoped Asset model intentionally, accepting duplication when
the same source music is used across Sessions. A global reusable Track/catalogue
domain is deferred until real usage provides enough evidence to justify it.

## MVP completion criteria

- Produce, publish, and measure at least **20 controlled content experiments**.
- Keep each published video traceable to its Session specification, source
  Assets, audio RenderRun, video RenderRun, and a future Publication record.
  Publication records are planned, not implemented today.
- Capture sufficient performance data to compare genre, mood, purpose,
  environment, location, weather, time, and duration. Record each hypothesis and
  the dimensions being varied so the results can inform subsequent experiments.
- Use those results to identify content clusters that justify investment in
  differentiated/original music.

There are no fixed CTR or watch-time thresholds at this stage. Completion means
collecting comparable evidence and using it to decide where to invest next.

## Roadmap

### Phase 1 — Local production engine

**Status: complete.** Session specification, Asset provenance/import, audio
rendering, video rendering, and artwork generation.

### Phase 2 — Publishable MVP

**In progress.** YouTube Audio Library sourcing convention, PublicationPlan,
title/description/tags metadata, thumbnail packaging, manual YouTube upload, and
Publication ↔ YouTube video ID tracking. Sourcing conventions and deterministic
local PublicationPlan are available now. Run `swale-sounds publish plan` after
rendering, review the package, then manually upload the referenced MP4 through
YouTube Studio. See the [publication workflow](../README.md#publication-planning).
The command does not upload anything or record a publication. Thumbnail
transformation and Publication ↔ YouTube video ID tracking remain future work.

### Phase 3 — Measurement MVP

YouTube Analytics ingestion, analytics snapshots, video performance reporting,
metadata/performance correlation, and experiment evaluation.

### Phase 4 — Data-driven planning

Identify successful content clusters, design structured experiments, develop
playlist strategy, and recommend the next Session.

### Phase 5 — Automation

YouTube API publishing, scheduling, background processing, cloud deployment,
and monitoring.

### Phase 6 — Content differentiation

Original/AI music, a reusable music catalogue, improved mastering/mixing, and
motion/animation.
