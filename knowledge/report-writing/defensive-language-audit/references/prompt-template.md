# Reusable prompt and output schema

Use the following prompt as a starting point. Replace the bracketed fields with the actual task.

```text
You are auditing an AI-generated academic passage.

Reader decision: [what the reader must decide]
Discipline and genre: [discipline, article section or abstract]
Evidence available: [sources, data, methods, or “not provided”]

Rules:
- Do not add generic disclaimers, apologies, or repeated self-reference.
- Do not remove a limitation when its removal changes evidence strength, scope, causality, responsibility, safety, legal meaning, financial meaning, or method interpretation.
- Use a hedge or booster only when the evidence requires that degree of uncertainty or emphasis.
- Treat “this paper will…”, “it is important to note…”, “it should be acknowledged…”, and similar phrases as candidates for function testing, not automatic errors.
- Preserve facts, numbers, source attribution, causal direction, and the author's actual position.

Pass A — audit only:
For each sentence, return:
1. primary function;
2. sentence consumer: who uses it;
3. decision changed;
4. what becomes inaccurate if removed;
5. action: DROP, CALIBRATE, RELOCATE, or KEEP;
6. evidence or author decision needed.

Pass B — revise only the approved actions:
Return the revised passage and a change log with:
sentence | function | action | reason | revision.
Then list retained limitations and unresolved verification questions.
```

## Worked decision examples

- “These findings may not apply to every context.”
  - Keep or relocate when the study sampled one population and the claim is being generalized.
  - Calibrate to a specific scope when the limitation is real but the universal wording is broader than the evidence.
  - Drop only when the surrounding sentence already states the same scope restriction and the duplicate changes no decision.

- “It is important to note that this study has several limitations.”
  - Test the following limitation sentences individually. Keep the concrete limitations; usually drop the announcement sentence if it changes no decision.

- “This paper will first discuss…”
  - Keep only when the roadmap helps the reader navigate a long or complex argument. Drop repeated stage labels that merely announce obvious paragraph order.

- “The method suggests an association, rather than proving causation.”
  - Keep. The causal boundary changes how the reader may interpret the result.

- “The results are robust and undeniably important.”
  - Require evidence for both judgments. Calibrate or drop unsupported boosters instead of replacing them with a hedge.

## Suggested evaluation record

For a study or repeated workflow, record:

- unsupported-claim rate;
- percentage of sentences with an identifiable decision or accuracy function;
- repeated metadiscourse or repeated n-gram rate;
- distribution and variety of hedges, boosters, self-mentions, and reader-address forms;
- claim-preservation errors after revision;
- reader decision agreement before and after revision;
- human author acceptance rate and unresolved edits.

These measures evaluate different properties. Do not collapse them into one “AI style” score.
