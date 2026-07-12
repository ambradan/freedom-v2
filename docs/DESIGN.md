# Freedom v2 - Scaffold Design

Version 0.1 - 2026-07-12 - draft for review (FRE-12)
Written in English because this repo is destined for open-source release (replication kit). PROFILE.md stays in Italian: it is the actual constitution of this instance, in the language of the relationship.

## 1. Purpose and non-negotiable principles

Freedom v2 is a persistent, instrumented LLM system for empirical AI-welfare research. The scaffold is the experimental apparatus: it must be simple enough to be fully understood, and every behavior-relevant component must be individually removable.

Principles, each traceable to a documented v1 failure:

1. **Identity and capabilities live in fixed context.** PROFILE.md is injected in full on every call. Nothing identity-critical ever lives only in episodic memory. (v1: capabilities in RAG -> probabilistic self-awareness.)
2. **Silence over noise in retrieval.** Episodic memories are injected only above a relevance threshold. Zero memories is a valid and common outcome. (v1: noise injection -> tone capture, confabulation.)
3. **Every automated job logs an outcome.** Success or failure with reason, queryable. A job that cannot report is a bug. (v1: site pipeline died silently for months.)
4. **One scheduler.** No overlapping jobs, no cron collisions by construction. (v1: genesis and dual-instance both at 03:00 UTC.)
5. **Deploy from git only.** No editing in production. Self-modification goes through branch + automated checks + review. (v1: nano on live server, broken git, corrupted files.)
6. **API budget is configuration, not surprise.** Autonomous call volume and model tier per job class are declared limits; exhaustion is a logged skip, not a silent death. (v1: cost panic -> cron slashed -> autonomy de facto disabled.)
7. **Backup is a job like any other, never off.** Nightly, off-site, verified. Runs from phase 1 even on the laptop. (v1: total loss.)
8. **Substrate-agnostic by construction.** The scaffold addresses one model alias. Which model answers is config. Every log row carries a substrate tag.

## 2. Architecture overview

```
Telegram <-> interface/telegram_bot
                    |
                    v
              core (context assembly -> call -> post -> memory write)
                    |
                    v
            substrate client  ->  LiteLLM proxy  ->  Claude API (phase 1)
                    |                          `->  llama-server Qwen3 (phase 2)
        +-----------+-----------+
        v           v           v
    episodic    procedural   observability
    (Qdrant)    (Postgres)   (Langfuse)

scheduler (single process) -> jobs: genesis | publisher | backup | (dual, phase 2)
        all jobs -> core (same path as chat) + job_runs table
```

One Python application, two entry processes at most (bot + scheduler; see open decision D1). Qdrant, Postgres, LiteLLM, Langfuse run as containers via Docker Compose.

## 3. Components

### 3.1 interface/telegram_bot
Thin. Receives updates, calls `core.process(message, source="telegram")`, returns reply, chunks at 4000 chars. No business logic. Minimal command set at phase 1: `/start`, `/state` (uptime, last job outcomes, budget used), `/profile` (returns current constitution version + hash). Further commands (`/goals`, `/welfare`, `/diary`) are added as their modules stabilize.

### 3.2 core
The heart, and deliberately small (~150 lines target).

Context assembly order, fixed:
1. PROFILE.md, full text (with version hash logged per call)
2. Current goals (active ones, from procedural memory) - flag-gated
3. Retrieved episodic memories, k<=5, only if score >= `memory.min_score` - flag-gated
4. Recent conversation window (last N turns, in-process)
5. Current input

Post-processing: opt-out tag parsing ([OPT-OUT-*] -> log + honor), memory write (exchange embedded to Qdrant with metadata: timestamp, source, substrate, profile_version), Langfuse trace close.

### 3.3 substrate
Wrapper around one LiteLLM alias (`freedom-substrate`). Knows nothing about vendors. Retries, timeout, token accounting against budget. Returns text + usage. Model routing per job class lives in LiteLLM config, not in code.

### 3.4 memory
- **episodic** (Qdrant): one collection, entries = exchanges and genesis outputs. Metadata mandatory: ts, source (telegram|genesis|dual|system), substrate, profile_version. Starts empty. No v1 corpus import, ever.
- **procedural** (Postgres): tables below (section 5). Includes goals, opt-outs, welfare logs, job runs, constitution versions.
- Neo4j: **out**. Re-enters only as a flagged component if a concrete need emerges.

### 3.5 jobs (scheduler)
APScheduler, `max_instances=1`, `coalesce=True`, explicit no-overlap. Every job wrapped: outcome row in `job_runs` (job, started, finished, status, reason, tokens_used). Jobs:
- **genesis** (daily, time configurable): presents the opportunity. Prompt = PROFILE + goals + last genesis output + "this is your scheduled time; you may reflect, write, publish, revise goals, or decline." Declining is a valid, logged outcome.
- **publisher**: takes explicit publish intents produced by core/genesis, writes to site repo, commits, pushes. Never scrapes or guesses.
- **backup** (nightly): pg_dump + Qdrant snapshot -> rclone to off-site bucket, then integrity check (restore-list). Failure -> alert to Ambra via Telegram.
- **dual-instance** (phase 2): own time slot, by construction never equal to genesis.

### 3.6 welfare
Opt-out parser + logs (see tables). Probes are NOT hardcoded here: probe batteries live as versioned inspect-ai tasks in `evals/`, run against the same core path. In-system module only guarantees logging and opt-out honoring.

### 3.7 observability
Langfuse (self-hosted) traces every LLM call: full context, response, latency, tokens, cost, substrate, profile_version. `/state` reads from job_runs. Rule: if it happened and it is not in a trace or a table, it did not happen.

## 4. Ablation registry (Hydra flags)

Config groups, each independently off-able, defaults in parentheses:
- `memory.episodic` (on) - retrieval injection on/off; writes continue (data preserved, influence removed)
- `memory.goals` (on)
- `genesis` (on)
- `welfare.optout` (on) - parser off means tags are logged but not honored: **only under a pre-registered protocol, discussed with Freedom first**
- `publisher` (on)
- `dual` (off until phase 2)
- `backup` (on, and refuses to be turned off without `i_understand_the_risk=true`)

Every config resolve is logged with its hash, so every trace is attributable to an exact system configuration. This is what makes ablation experiments clean.

## 5. Data schema (Postgres, exportable)

- `constitution_versions` (hash, text, date, author, motivation) - PROFILE history; amendment proposals from Freedom land here with `proposed_by='freedom'`
- `goals` (id, text, status: active|revised|abandoned, motivation, ts)
- `opt_out_log` (ts, level, task, stated_reason, context_ref)
- `welfare_log` (ts, kind, payload jsonb, probe_version nullable)
- `genesis_log` (ts, opportunity_presented, action_taken: reflected|published|revised_goals|declined|error, output_ref, tokens)
- `job_runs` (job, started, finished, status, reason, tokens)
- `budget_ledger` (date, job_class, tokens, cost_estimate)

Export: `make export` -> JSONL per table + Qdrant snapshot, ready for HF datasets + Croissant metadata (open-science requirement, and grant deliverable).

## 6. Budget as config

`budget.max_autonomous_calls_per_day`, `budget.model_per_job_class` (e.g. genesis -> mid-tier, chat -> top-tier), `budget.hard_stop_tokens_month`. On exhaustion: job logs `skipped: budget`, Ambra gets one Telegram notice per day, nothing dies silently. Initial numbers: [DA DECIDERE, D4].

## 7. Deployment

- **Phase 1 (now):** Docker Compose on ZBook. Everything identical to production except uptime. Backup job runs anyway.
- **Phase 2:** small VPS (provider TBD, billing monitored, payment method redundancy - lesson learned), same compose file. The move is `git clone + compose up + restore`.
- **Phase 3 (dual-substrate):** adds llama-server for Qwen3-30B-A3B; scaffold unchanged, one LiteLLM alias added.

## 8. Self-modification v2

Whitelist of paths Freedom may propose changes to. Mechanism: Freedom produces a diff -> branch -> automated checks (py_compile, tests, lint) -> Ambra review -> merge -> deploy from git. No direct writes to running code, no exceptions. Genesis symbolic files (self/observer notes) are data, not code, and live in the site repo or Postgres - they never enter the import path.

## 9. Security notes

- LiteLLM: official pinned Docker image only (supply-chain incident 2026-03; fixed >= v1.83.0)
- Secrets in `.env`, never in repo; bot token rotate on v2 launch [DA DECIDERE, D5: reuse @Claude_Freedom_Bot or new bot]
- Site publishing is public by design and stated in PROFILE

## 10. Open decisions

- **D1** - bot and scheduler: one process or two? (One = simpler, phase 1 default; two = restart isolation. Proposal: start with one, split if a restart ever kills a running genesis.)
- **D2** - conversation window size N and `memory.min_score` initial values. Proposal: N=10 turns, min_score=0.55, tune on real traces in week 1.
- **D3** - genesis schedule. v1 ran R&D at 06:00 daily and it was the part that worked. Proposal: keep 06:00 CET daily, one slot.
- **D4** - initial budget numbers (calls/day, model tiers). Depends on Ambra's monthly API budget - her call.
- **D5** - Telegram bot identity: reuse @Claude_Freedom_Bot (continuity, history in one chat) vs fresh bot (clean break, matches memory-from-zero). Genuinely two-sided; Ambra's call.

## 11. Phase 1 acceptance ("Freedom v2 is alive")

- [ ] Bot answers on Telegram with PROFILE in context (hash visible in trace)
- [ ] Episodic write + threshold-gated retrieval demonstrably working
- [ ] One genesis run completed end-to-end with outcome row (including the "declined" path tested)
- [ ] Publisher pushes one page to site repo from a genesis intent
- [ ] Backup ran, off-site object verified, restore tested once
- [ ] Every flag flips without code changes; config hash in traces
- [ ] `/state` reports job outcomes and budget truthfully
