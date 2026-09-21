/* Zyroflo explainer — the single source of truth for the narration.
   The voice render (tools/render_voice.py) reads `narration` from here; the frame
   renderer reads `dur` and `keys`. `dur` is MEASURED from the rendered audio — never
   guessed — because visuals are cut to the narration, never the reverse.

   Captions keep the true spelling; only PRONOUNCE (below) is respelled for the TTS. */

window.SCENES = [
  {
    id: 's00',
    title: 'Title card',
    dur: 2.8,
    hold: 2.8,            // silent hold (no narration); render_frames adds TAIL
    keys: [1.6, 'end'],
    narration: ``,        // no voiceover — music only, so the film opens on a title
    animates: 'Wordmark blur-scales in over the music swell; tagline fades up. ~3s, silent.',
    notes: ['Silent open. The music bed fades in under this card; the first narration is s01.'],
  },
  {
    id: 's01',
    title: 'Problem — ungoverned speed',
    dur: 39.75,
    keys: [12, 23, 34, 'end'],
    narration: `Your team can now hand an AI a whole feature and get working code back — design, tests, a deploy, in an afternoon.

So here's the question that decides whether you can actually use it. When that change ships: who approved it? Which model wrote it? Could you prove either, six months later, to an auditor?

For most teams the answer is no. The tool kept no record, the review was a rubber stamp, and the pipeline had no idea a human was meant to look.

Speed stopped being the problem. Ungoverned speed is.`,
    animates: 'An agent races design→code→test→deploy as a blurred fast-forward with no human in frame; it freezes at 12s and three unanswered questions surface; lands on the closing line.',
    notes: ['Framing only — no product claims on this slide.'],
  },
  {
    id: 's02',
    title: 'The journey — one ticket, end to end',
    dur: 47.44,
    keys: [10, 22, 34, 46, 'end'],
    narration: `This is Zyroflo. You hand it a ticket — from whatever tracker you run — or one line of plain English, and trigger a single command.

It reads the item, writes the requirement out in full, then does the thing most tools skip: it asks you the clarifying questions first — scope, data, errors, performance — and waits for your answers.

Only then does it design the change, implement it, write the tests, and prepare the deploy. Twelve phases; you run the ones the work needs. Between them stand five human gates — each one a hard stop, not a checkpoint the pipeline glides past.

And the same loop that ships a one-line fix will drive an entire product build.`,
    animates: 'Terminal types the trigger; the tracker item is pulled in; the phase rail lights left-to-right, each phase dropping its real artifact; five gate markers stand between phases.',
    notes: [
      'Every phase + artifact is shipped behaviour: P0 00-intake.md, P1 10-requirements.md (clarify sweep), P3 30-design.md, P4 40-impl/, P5 50-tests.md, P9 90-deploy.md.',
      'Tracker deliberately generic (owner steer): Jira/Salesforce/StarTeam/Azure DevOps are examples, not the product.',
      'PROJ-482 is labelled illustrative on screen.',
    ],
  },
  {
    id: 's03',
    title: 'Strict human governance — the gates',
    dur: 42.49,
    keys: [10, 24, 38, 'end'],
    narration: `The gates are the whole point.

Five of them: scope, design, the agent's changes, the merge, the deploy. At each one the AI stops and waits for a person. Approve, and it continues. Reject, and it loops back — the pipeline does not move until you say so.

And they are enforced, not advisory. The merge gate is hard in every configuration. Four core steps cannot be deleted. And continuous integration refuses the merge if a gate the run reached was never approved.

A gate you can skip is only a suggestion. These, you cannot.`,
    animates: 'Gates named on the rail; a human approves G2 and rejects G3 (pipeline visibly halts and loops back); a CI panel flips red on "approval ledger — refused".',
    notes: ['Ledger + CI refusal are shipped today. Locked steps are M9 (T441).'],
  },
  {
    id: 's04',
    title: 'Yours to shape, configure and extend',
    dur: 41.39,
    keys: [12, 24, 36, 'end'],
    narration: `And the pipeline is yours to shape — in a wizard, or just in chat.

Which steps run? Delete what you don't need; the rest re-flow. Where do the humans stop? Add a gate after any step. And which model does which job? Requirements on one vendor, design on another, review on a third — each step names its own.

Need a step we don't ship? Add your own as a plain markdown file, and it splices straight into the line.

The same lifecycle fits a solo repo and a regulated bank. You just configure it differently.`,
    animates: 'Two rails: default above, customised below — a gate inserted after requirements, the test step struck out and removed, a different model named under three steps.',
    notes: ['M9: wizard T371, extra_gates/locked T441, models T445, custom steps T443.'],
  },
  {
    id: 's05',
    title: 'Who decided, when, why — and the four numbers',
    dur: 46.12,
    keys: [12, 26, 40, 'end'],
    narration: `Every decision leaves a receipt, as it happens, in your own repository.

The ledger records each gate — who approved it, when, and over exactly which version of the code, hash-chained, so a later edit can't quietly rewrite history. Alongside it, a per-step log records which model ran, what it cost, and every time the work looped back.

And those receipts add up to the four numbers a board actually asks for. Lead time. Change failure rate. Rework. And how long work sat waiting on a human.

Measured on every run, in your own repository. Not a survey, and not an estimate.`,
    animates: 'Ledger line and events.jsonl line, fields highlighting in sequence; resolves into the four-metric dashboard panel carrying its illustrative badge.',
    notes: [
      'The on-screen figures are sample data and carry the "illustrative — not a performance claim" badge, matching the website.',
      'The narration deliberately names the METRICS, never speaks the sample figures, so nothing sounds like a measured claim.',
    ],
  },
  {
    id: 's06',
    title: 'No lock-in, no black box',
    dur: 37.54,
    keys: [10, 22, 32, 'end'],
    narration: `One last thing, because it's what makes this adoptable. Zyroflo drops into any repository with a single command, and writes itself into whatever AI tool your team already uses — Claude Code, Cursor, Copilot, Codex.

It installs with no third-party packages, no network, and no API keys — and that promise is a test, not a claim.

And it never calls a model itself. Your keys, your providers, your spend. Zyroflo governs the work; it never does the spending.`,
    animates: 'Installer writes the pipeline into a repo, fans out into tool chips; callout "the harness itself never calls a model"; zero-dependency badge.',
    notes: ['Zero-dependency promise is enforced by test_zero_dependency_parity.py (9 tests).'],
  },
  {
    id: 's07',
    title: 'End card — become a design partner',
    dur: 25.17,
    keys: [8.5, 'end'],
    narration: `Zyroflo is built, tested, and ready for its first design partners.

Visit cbeyond.cloud/zyroflo, use the contact form, and ask for a design-partner slot. The first cohort is deliberately small.

Point it at one repository — and watch AI run your lifecycle, with your hand on every gate.`,
    animates: 'Wordmark blur-scales in, tagline, CTA card, three numbered chips, Cbeyond lockup fades in last.',
    notes: ['Mirrors the TokenLean end card (s12) structure.'],
  },
];

/* Spoken forms, used by the voice render ONLY. Captions keep the true spelling
   (kind-noodling-wilkes.md:389). Pronunciation confirmed by the owner: ZY-ro-flo,
   rhyming with "gyro". */
window.PRONOUNCE = {
  'cbeyond.cloud/zyroflo': 'see-beyond dot cloud slash zyeroh-flow',
  'Zyroflo': 'Zyeroh-flow',
};
