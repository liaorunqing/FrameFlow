# 30-Second Narrative Campaign Experiment

## Outcome

The `campaign-sequence-v1` pipeline produced one 30.016-second, five-shot
live-action narrative advertisement plus a five-second CTA section.

- Output: `backend/data/debug/campaign-30s/campaign-30s-final.mp4`
- Video: H.264, 1280 x 720
- Audio: AAC, 48 kHz, mono
- Narrative shots: 5
- Story sequences: 3
- Boundary keyframes: 6
- Final CTA: 5 seconds

## Continuity workflow

The story is organized as three sequences:

1. The protagonist has something difficult to say.
2. The product becomes a companion in action.
3. The emotional change becomes visible and leads into the CTA.

Each adjacent video shot shares a planned boundary keyframe. The generated
actual last frame is also stored for later continuity review.

## Keyframe QA result

- Six keyframes were approved.
- One boundary frame contained a duplicated protagonist.
- Only that frame was regenerated.
- Keyframe attempts: 7 total for 6 approved frames.
- Keyframe retry count: 1.
- The local `qwen3-vl:4b` composition gate correctly counted two visible people.
- One pairwise character check produced a false wardrobe mismatch and was
  explicitly approved by a recorded human override after visual inspection.

This demonstrates why automatic QA must support both local repair and auditable
human override.

## Video generation result

All five narrative clips succeeded on their first Seedance attempt.

- Per-shot tokens: 246,840
- Total video tokens: 1,234,200
- Video retries: 0
- Per-shot calculated cost: CNY 3.7026
- Total calculated video cost: CNY 18.5130

The cost is calculated from returned usage tokens at the configured
CNY 15 per million tokens. The provider billing console remains the source of
truth.

## Total calculated experiment cost

- Historical/reused keyframes including an earlier repair: CNY 1.00
- Three extension keyframes: CNY 0.75
- One local keyframe repair: CNY 0.25
- Five video clips: CNY 18.5130
- 76 TTS characters at the configured package-equivalent rate: CNY 0.0152
- Local Ollama QA and FFmpeg editing: CNY 0 external API cost
- Total: CNY 20.5282

See `backend/data/debug/campaign-30s/metrics.json` for the machine-readable
per-shot and per-keyframe ledger.

## Postproduction

The FFmpeg composer now supports:

- shared-boundary match cuts;
- continuous narration and room tone across picture cuts;
- per-shot narration slots;
- burned-in SRT subtitles;
- CTA card generation;
- optional logo overlay on the CTA card;
- H.264/AAC delivery master generation.

## Expansion gate for 45 and 60 seconds

The director now has exact duration profiles:

- 30 seconds: 5 narrative shots;
- 45 seconds: 8 narrative shots;
- 60 seconds: 10 narrative shots.

No paid 45- or 60-second run should start yet. One successful campaign proves
the pipeline works but is not enough to claim production stability. Expansion
requires at least three different 30-second campaigns meeting all conditions:

- 100% final video completion;
- no more than one video retry per campaign;
- at least 80% first-pass keyframe approval;
- no unresolved identity or product defect;
- final duration within 0.1 seconds;
- complete task ID, token, retry, and cost records.
