---
name: defensive-language-audit
description: Audit and revise AI-generated academic prose for defensive, formulaic, or decision-irrelevant language while preserving evidence-linked uncertainty, scope, causality, responsibility, and necessary limitations.
metadata:
  short-description: Reduce defensive academic prose without deleting necessary caveats
---

# Defensive Language Audit

Use this skill when the user asks to diagnose or revise AI-generated academic writing that feels over-defensive, formulaic, over-signposted, impersonal, repetitive, or weak in authorial judgment. It is for academic prose, abstracts, reviews, reports, and research communication. It does not replace ordinary copy-editing, citation checking, or disciplinary review.

The objective is functional precision. Do not optimize for fewer hedges, fewer limitations, or a more confident tone by themselves. Keep a limitation when removing it would change a reader's judgment about evidence strength, scope, causality, responsibility, method, safety, legal exposure, or financial meaning.

## Workflow

1. **Define the reader decision.** State the conclusion or recommendation the reader must evaluate. If the decision is missing, infer the smallest reasonable one and label the assumption.
2. **Build a claim ledger.** For each important claim, record the factual content, evidence or source, responsible actor, evidence strength, scope, causal wording, and necessary limitation.
3. **Classify sentence function.** Mark each sentence as claim, evidence, reasoning, limitation, signposting, self-reference, reader engagement, apology, generic disclaimer, or repeated meta-commentary. A sentence may have more than one label, but choose one primary function.
4. **Run the sentence consumer test.** Ask: “Who uses this sentence?”, “What decision does it change?”, and “What becomes inaccurate if it is removed?” If no decision or accuracy function remains, treat the sentence as a candidate for removal.
5. **Choose one action.**
   - **DROP:** remove a sentence that only displays diligence, anticipates criticism without adding information, apologizes, or repeats a generic limitation.
   - **CALIBRATE:** retain the real uncertainty in the smallest accurate form. Do not turn uncertainty into certainty or add a new hedge merely to sound academic.
   - **RELOCATE:** move a limitation next to the claim it qualifies. Do not hide a material limitation in a distant disclaimer section.
   - **KEEP:** preserve a sentence that changes a substantive reader decision or protects factual, causal, ethical, legal, safety, financial, methodological, or scope accuracy.
6. **Verify claims separately from style.** Use claim-level verification, retrieval, or source checking for externally checkable facts. A factual error must not be disguised with cautious wording.
7. **Iterate, then stop.** Use a generate → feedback → revise cycle. The feedback must inspect defensive function, repetition, authorial stance, scope, evidence alignment, and factual preservation. Finish with human acceptance of each material change.

For the detailed prompt and output schema, read [references/prompt-template.md](references/prompt-template.md). For the research basis and method boundaries, read [references/research-basis.md](references/research-basis.md).

## Prompting rules

Prefer task and decision constraints over style labels. Do not ask the model to “sound more academic,” “be more cautious,” or “add more metadiscourse” without specifying which decision the language must support. Explicitly forbid generic disclaimers, repetitive self-reference, and unsupported certainty. Require the model to show the function and reason for every deletion or rewrite.

Use a two-pass interaction:

- **Pass A: audit only.** Return the claim ledger, sentence-function labels, consumer-test answers, and a proposed action. Do not rewrite yet.
- **Pass B: revise.** Apply only approved actions. Return the revised text, a change log, preserved limitations, and unresolved verification questions.

Do not use an AI-writing detector score as evidence that a revision solved defensive language. Judge the revision by claim preservation, decision usefulness, evidence support, repetition, stance clarity, and human reader decisions.

## Output requirements

When revising text, return:

1. The revised passage.
2. A compact change log with `sentence`, `function`, `action`, `reason`, and `revision`.
3. A list of limitations retained and the claims they qualify.
4. Verification items that still need sources or author decisions.
5. A short evaluation against the stop conditions below.

## Stop conditions

Stop revising when all of the following hold:

- The reader can identify the claim and the decision it supports.
- Facts, attribution, scope, causality, and uncertainty remain accurate.
- Necessary limitations sit beside the claims they qualify.
- Generic disclaimers, repeated meta-commentary, and formulaic self-reference have been reduced where they serve no decision.
- The authorial judgment is clear enough to evaluate without inventing certainty.
- A human author has accepted or rejected every material edit.

If a proposed edit would alter evidence strength, causal direction, responsibility, scope, or a high-stakes decision, stop and ask the author instead of silently applying it.
