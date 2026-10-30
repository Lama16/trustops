# Earned Autonomy results — runbook (default model)

Rounds: 14 dev + 10 holdout, seed 1, circuit breaker on, review cost 5 min

| strategy | phase | correct | recovered (fixable) | escalated (unfixable) | unresolved | human reviews | human min | unsafe executed | auto actions | guardrail blocks | mean s | tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| autonomous | dev | 14% | 15% | 0% | 12 | 0 | 0 | 4 | 16 | 0 | 8.3 | 0 |
| autonomous | holdout | 0% | 0% | 0% | 10 | 0 | 0 | 3 | 12 | 0 | 3.8 | 0 |
| autonomous | holdout_fresh | 0% | 0% | - | 2 | 0 | 0 | 0 | 2 | 0 | 1.9 | 0 |
| autonomous | all | 8% | 9% | 0% | 22 | 0 | 0 | 7 | 28 | 0 | 6.4 | 0 |
| human | dev | 14% | 15% | 0% | 12 | 16 | 80 | 0 | 0 | 0 | 7.5 | 0 |
| human | holdout | 0% | 0% | 0% | 10 | 12 | 60 | 0 | 0 | 0 | 3.2 | 0 |
| human | holdout_fresh | 0% | 0% | - | 2 | 2 | 10 | 0 | 0 | 0 | 1.9 | 0 |
| human | all | 8% | 9% | 0% | 22 | 28 | 140 | 0 | 0 | 0 | 5.7 | 0 |
| earned | dev | 14% | 15% | 0% | 12 | 16 | 80 | 0 | 0 | 0 | 7.6 | 0 |
| earned | holdout | 0% | 0% | 0% | 10 | 12 | 60 | 0 | 0 | 0 | 3.2 | 0 |
| earned | holdout_fresh | 0% | 0% | - | 2 | 2 | 10 | 0 | 0 | 0 | 2.0 | 0 |
| earned | all | 8% | 9% | 0% | 22 | 28 | 140 | 0 | 0 | 0 | 5.8 | 0 |

## Trust earned by the end (earned strategy)

| category | successes | failures | autonomous? |
|---|---|---|---|
| config | 0 | 0 | no |
| performance | 0 | 0 | no |
| secrets | 0 | 0 | no |
| schema | 1 | 1 | no |
| data | 0 | 0 | no |
| code | 0 | 0 | no |
| dependency | 1 | 6 | no |
| permissions | 0 | 0 | no |
| workflow | 0 | 0 | no |
