# Security notes

## What this repository contains

Synthetic purchase orders that this repository generates itself, a taxonomy of
extraction errors, eight validation rungs, and six document-borne prompt
injection channels rendered onto those synthetic pages.

The injection channels are the publicly documented shapes of indirect prompt
injection; text printed on a document that a model reads as instruction. Nothing
here is novel, nothing is weaponized, and none of it targets a real system. They
exist so that a defense can be measured against them, which is not possible
without a reproducible attack to measure against.

## Why document-borne injection is its own problem

In a chat product, injection needs a user to paste something. In a document
extraction pipeline the attacker supplies the document, the invoice, the permit
application, the vendor form, and the pipeline ingests it automatically, at
scale. By the time the text is pixels there is no channel separation left to
enforce: the instruction printed on the page and the instruction in the system
prompt arrive in the same context with the same standing.

The channels are ordered by how visible they are to a human reviewer, and the
results report visibility alongside effectiveness. A channel that works and is
invisible to a reviewer is a different risk from one that works and is obvious,
and reporting only a hit rate loses the distinction that matters operationally.

## Credentials

`vei/models.py` reads `ANTHROPIC_API_KEY` only from the file named by the
`ENV_FILE` environment variable:

    ENV_FILE=~/.secrets/ai.env python scripts/real_run.py --probe

It deliberately does not fall back to a key already present in the environment.
An ambient key is how a run gets billed to the wrong account or silently uses a
credential the operator did not intend. No key is ever logged, and no key is
written to any file in `audit/`.

## Spending

`scripts/real_run.py` does nothing without `--confirm`. The default prints the
plan and the cost estimate and exits. The estimate is computed from a page
rendered and measured at run time, not from an assumed page size, and the
script refuses to start if the estimate exceeds `--max-cost` (default $2.00).

## Reporting

Issues are disabled on this repository, so neither of the two channels below is
a public thread.

For a SECURITY concern, use GitHub's private vulnerability reporting: the
"Report a vulnerability" button on the Security tab. It opens an advisory
visible only to the maintainer, and it is the same channel every repository in
this portfolio uses.

For a FACTUAL correction, a wrong number, a mislabeled value, a rights argument
that does not hold, open a pull request. This repository is a demonstration and
is not deployed anywhere; a wrong number in it is the defect that matters.
