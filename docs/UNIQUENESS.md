# MoveDesk / The Financial Detective: uniqueness memo

This page is the evidence that MoveDesk and The Financial Detective are two different
submissions, so the hackathon rule applies: an entrant may submit more than one submission,
however each submission must be unique and substantially different (SRC-02).

Written by Person 4 (Ahmed) for MD-4.4, from the comparison table in the execution plan
Section 3.1. Teams behind the two entries are the same five people; the projects are not.

## The two projects in one table

| Dimension | The Financial Detective | MoveDesk | Evidence |
|---|---|---|---|
| User and market | Agencies and freelance teams with milestone contracts | Small moving companies (residential moves) | Detective pitch line vs the MoveDesk story: a customer's chat message opens a job |
| Trigger | A client's PayPal payment (Orders v2 capture) audited against a contract | A customer's chat message asking for a quote | Orders v2 invoices vs intake chat; the chat creates the job, nothing was paid yet |
| PayPal APIs | Orders v2, Webhooks v1, Invoicing v2 (correction invoices), Payouts v1 | Invoicing v2 full lifecycle: deposit, QR, final, record cash, remind, cancel and reissue, plus Invoicing webhooks. No Orders, no Payouts | The two apps call disjoint endpoints; MoveDesk never creates an order or a payout |
| AI | Gemini clause extraction, rule compiler, timesheet mapper, explanations | Claude chat intake (bilingual), owner copilot with read tools and a propose-only action tool, two regression models (hours, on-time payment) | Different model providers for extraction (Gemini vs Claude), different tool sets |
| Money direction | Money in (corrections) and money out (payroll via Payouts) | Money in only: quote, deposit, final payment | MoveDesk has no payroll or payout path |
| Safety model | Leash: Allow / Ask / Deny on every money action | Owner approves every quote; the copilot only proposes; a job is paid only when PayPal says so | Approve button + verified webhook; no deny-list engine in MoveDesk |
| Frontend | React + Vite + ag-grid-react | FastAPI-served pages + AG Grid Community bundled locally | Different repos, build tools and page structures |
| Sponsor target | AG Grid | Render | A project can win at most one Grand Prize and one Sponsor Prize (SRC-02), so the two entries do not compete for the same one |

Prize targeting also differs on category: MoveDesk aims at Most Impactful with Best Use of
PayPal + AI as second choice (decision recorded on Nov 1); the Detective picks its own.

## Why the overlap that remains is shared plumbing, not the project

Both entries use Invoicing v2 and both have a safety theme. What the judges would compare is
the product, and the products answer different questions: one audits money that already moved,
the other moves the money for the first time in a quote-to-cash loop. Different domains,
different APIs, different AI roles, different prize targets, separate repositories, separate
videos.

## Wording cross-check (no shared paragraphs between the two Devpost texts)

Status: MoveDesk side done Oct 29; Detective side pending until its Devpost text is final.

- [x] MoveDesk text in `docs/DEVPOST.md` contains: no sentence copied from the Detective plan,
      no AG Grid sponsor section, and no paragraph shared verbatim with the Detective pitch.
- [ ] Read the Detective's Devpost draft next to `docs/DEVPOST.md` and confirm no paragraph
      appears in both. Owner: Ahmed with the Detective text owner, before Nov 9 (internal
      submit deadline).
- [ ] If any paragraph is shared or the wording repeats, reword on the MoveDesk side the same
      day and note it here.

## Open point

OQ-08 records the fallback: if there is any doubt that the two entries read as substantially
different, ask on the hackathon Discord before the Nov 9 internal submit, not after.
