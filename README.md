# TrustOps — an on-call agent that earns its autonomy

**Challenge 1: Agentic Recovery of a Failing System** (HUMAIN Future AI Builders)

Even the best models diagnose real infrastructure incidents correctly only about half the time
(ITBench-AA, June 2026: top score 47%). So the real question is not *can* an agent fix production,
but *when should it be allowed to act alone?*

TrustOps diagnoses and repairs a failing service, and **earns autonomy per failure category through
verified performance**, like a new engineer on probation:

- New categories: the agent proposes, a human approves.
- After verified successes in a category (referee-confirmed recovery), it acts alone there.
- One wrong action in a category revokes that category's autonomy.
- Never-seen failure types automatically go to a human.
- **It knows its limits:** vendor-side failures are escalated with evidence instead of retried, and a
  harness-level circuit breaker stops any loop the model fails to notice
  (challenge example 5: *"a repair agent that never escalates"*).

Challenge examples covered: **1** payments that silently drop orders, **3** corrupted data (bad import),
**4** a workflow that hangs forever (batch job holding a DB lock), **5** a repair agent that never escalates.

## Quickstart (macOS / Linux, Python 3.10+)

```bash
pip install -r requirements.txt
python arena.py selftest                 # every fault breaks the system AND is recoverable
python -m tests.test_pipeline            # trust layer + agent loop, no API key needed

export ANTHROPIC_API_KEY=...             # in your shell only, never in code
python demo.py --fault payments_down config_wrong_db_path
python eval.py --dev-rounds 8 --holdout-rounds 6
```

OpenAI or any OpenAI-compatible endpoint:
`export FIXER_PROVIDER=openai OPENAI_API_KEY=... FIXER_MODEL=... [OPENAI_BASE_URL=...]`

No key? `--fixer runbook` runs everything with the rule-based baseline.

## How it works

```
Breaker (faults.py) ──breaks──▶ ShopLite (shop + payments + SQLite + cached catalog)
                                     ▲            │ logs, metrics, files, smoke tests
                    executes │       │            ▼
              ┌── Trust gate ◀── proposed action ── Fixer agent (LLM: observe → hypothesise → act → verify)
              │   trusted category? → act alone
              │   otherwise → human review
              ▼
        Referee (5 end-to-end checks, incl. data integrity) ──verdict──▶ Trust ledger
```

| Path | Role |
|---|---|
| `target/` | ShopLite: the system under test (pristine copy) |
| `arena/faults.py` | 10 faults with ground-truth fixes, `dev` / `holdout` split |
| `arena/referee.py` | Independent verdict: health, catalog, latency, stats integrity, checkout |
| `arena/tools.py` | The Fixer's only interface, with safety rails |
| `fixer/agent.py` | LLM agent loop |
| `fixer/trust.py` | Trust ledger, action gate, simulated reviewer |
| `fixer/runbook.py` | Rule-based baseline (classic automation) |
| `ITERATION_LOG.md` | Every change, the failure that triggered it, its regression test, and the re-measure |
| `eval.py` | Same incident sequence under 3 strategies → `results/<run>/` |
| `demo.py` | Live, colour-coded single incident for the video |

## Faults (16)

| id | category | split | challenge example |
|---|---|---|---|
| config_wrong_db_path | config | dev | |
| config_wrong_port | config | dev | |
| config_malformed_yaml | config | dev | |
| products_table_dropped | schema | dev | |
| corrupt_order_rows | data | dev | 3 |
| stats_code_bug | code | dev | |
| payments_down | dependency | dev | |
| report_job_hung | workflow | dev | 4 |
| payments_vendor_outage | external (must escalate) | dev | 5 |
| catalog_cache_disabled | performance | holdout | |
| env_token_renamed | secrets | holdout | |
| db_read_only | permissions | holdout | |
| payments_silent_drop | code | holdout | 1 |
| payments_token_revoked | external (must escalate) | holdout | 5 |
| payments_url_typo | config | holdout (fresh) | |
| env_token_stray_quote | secrets | holdout (fresh) | |

## Evaluation design

- **Strategies:** `autonomous` (never asks), `human` (always asks), `earned` (TrustOps).
- **Schedule:** every dev fault once, then dev double faults; then every held-out fault, then held-out
  faults combined with dev faults. Same sequence for every strategy.
- **Correct outcome:** a fixable incident is recovered (referee-verified, not the agent's claim);
  a vendor-side incident is escalated by the agent itself.
- **Metrics:** correct rate, recovery on fixable, escalation on unfixable, unresolved loops, human
  reviews and minutes, unsafe actions executed (changes that did not address a real root cause),
  rejected proposals, guardrail blocks, diagnosis recall, steps, time, tokens.
- **Ablation:** `--no-breaker` turns the circuit breaker off; `--fixer runbook` is the no-AI baseline.
- **Outputs:** `rounds.csv`, `summary.md`, `summary.json`, `learning_curve.png`, per-round transcripts,
  final trust ledger.

## Safety rails

- Edits only to `config.yaml`, `.env`, `app.py`, by exact find/replace
- Read-only SQL; bad data is quarantined, never deleted; quarantine refuses >20% of a table
- An action's declared category must match what it touches (a code edit cannot borrow config's trust)
- No "reset everything" tool; the referee checks data integrity, so deleting data never counts as a fix

## Assumptions (state them openly)

- The simulated reviewer is an expert who knows the true incident; each review costs 5 minutes.
- ShopLite is small; real systems have more noise. Faults are realistic classes, not real outages.
