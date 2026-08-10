# Contributing

See the README's [Contributing](README.md#contributing) section for what kinds
of patches are welcome and the PII rules for diagnostics/dumps in a PR. This
file covers how changes get committed.

## Commits

- **Author and committer must be the human accountable for the change** —
  never a tool, bot, or AI agent identity — including when the change was
  drafted or applied by an AI coding agent. Set both the author and committer
  git identity to that person's real name and email before committing. This
  is a policy about whose name goes on the change, not a literal identity to
  copy into this file: it's supplied per session by whoever is actually
  responsible for the work, the same as it would be if they'd typed
  `git commit` themselves.
- **No co-author trailers for AI tools or assistants.** Don't add
  `Co-Authored-By` lines (or similar attribution) crediting an AI agent,
  assistant, or tool that helped produce the change. The commit is
  attributed entirely to the accountable human.

## Code comments

This codebase reverse-engineers undocumented device APIs, so comments
recording *why* a decision was made (a calibration, a rejected write, an
issue number a quirk was confirmed against) are genuinely valuable — more
valuable than in most codebases. That's exactly why comments here need
discipline: it's easy for "explain the reasoning" to slide into "narrate
the whole investigation," and a file where every line has a paragraph
under it is as hard to read as one with no comments at all. Keep the
conclusion; cut the journey.

- **Comment the "why," never the "what."** If a comment just restates what
  the next line already says, delete it. Code should read clearly enough
  on its own that comments are only needed for the non-obvious.
- **One or two sentences, not an essay.** State the conclusion and the one
  piece of evidence that makes it credible (an issue number, a model name,
  a single confirming observation). Don't reproduce the full
  investigation — every dump checked, every attempt that failed, every
  hypothesis considered and discarded. A future reader needs to trust the
  conclusion and know where to look if they need to redo the work, not
  relive it.
- **A pointer, not a re-derivation.** Cite the issue/model once; don't
  re-explain a sibling function's already-documented reasoning. Reference
  it (`same reasoning as X above`) instead of restating it.
- **Module/class docstrings are a short orientation, not a design doc.**
  A few lines on purpose and any cross-cutting invariant is enough.
- **Failed-attempt logs don't belong inline.** If an investigation into an
  unsolved problem produced real negative results worth preserving (e.g. a
  reset mechanism nobody could find), put them in an issue or docs, not a
  block comment several times longer than the code it sits above.
- **When in doubt, cut.** If deleting a comment wouldn't lose real
  understanding, it's noise. Prefer trimming an existing comment over
  adding a new one.

## For AI coding agents

See `AGENTS.md`.
