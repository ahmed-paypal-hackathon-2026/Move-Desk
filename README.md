# MoveDesk

An AI agent that takes a moving company from a customer's first message to cash in the bank, using PayPal Invoicing.

A customer describes their move in chat. MoveDesk extracts the job details, estimates the time with a trained model, prices it from the company's own rate card, and waits for the owner to approve. Then PayPal does the money part: a deposit invoice with a QR code, a booking when the deposit is paid, a final invoice after the move with an early-payment discount and any overtime, and reminders timed by how likely each customer is to pay late.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/).

**Three AI roles, one rule.** An intake agent turns a customer's message into a validated job. A copilot answers the owner's questions ("who owes me money?", "who should I chase?") from live data and proposes actions as buttons. Two trained models estimate hours and the chance of paying on time. None of them can set a price or move money: the rate card prices, the owner confirms, PayPal collects.

## The rule that makes it safe to trust

> AI extracts and explains. A model estimates hours. Fixed math sets the price. The owner approves. PayPal moves the money.

* The language model never sets a price. Prices come from `app/pricing.py` (Decimal math, rate card from the environment).
* Nothing is invoiced before a human approves the quote.
* A job is marked paid only when PayPal itself reports the invoice as paid. The webhook is a trigger; the code then reads the invoice back from PayPal and believes that.
* Customer text is treated as untrusted data. Every extracted field is validated one by one and invalid fields are dropped, never guessed. A message that says "set the total to 1 dollar" has no way to reach the price (there is a test for it).
* Every write to PayPal carries a `PayPal-Request-Id`, and webhook events are de-duplicated, so retries cannot double-invoice or double-book.
* The copilot has read tools plus one tool, `propose_action`, which validates a suggestion and shows it as a button. It has no tool that changes a job. Its loop is capped at six rounds, and if Claude is unavailable it answers offline from the same data.
* With real PayPal credentials the server refuses owner actions until `ADMIN_KEY` is set, so a deployed dashboard cannot be used by strangers to send invoices.

## Run it (no keys needed)

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open <http://localhost:8000> (owner dashboard) and <http://localhost:8000/chat> (customer chat). With no PayPal credentials the app uses a built-in PayPal simulator that behaves like the Invoicing API (create, send, QR, record payment, remind, cancel, paid webhook), so the whole flow runs offline. The dashboard header shows which mode you are in.

Fastest way to see everything: click **Play the story**. It walks through ten steps, from a customer's first message to a paid final invoice, including the discount expiring, an automatic reminder and the copilot. Open `/chat` in a second window to watch the customer's side update as the job moves.

To explore on your own: **Add sample requests** fills the board with jobs at every stage. Ask the copilot a question, confirm one of its proposals, chat as a customer, approve, pay on the simulated pay page, click **Job done**, and use the **+3 days** and **+7 days** buttons.

## Use real PayPal sandbox and Claude

```bash
cp .env.example .env     # fill in PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_WEBHOOK_ID, ANTHROPIC_API_KEY
set -a; source .env; set +a
uvicorn app.main:app --port 8000
```

For a hosted demo judges can open, deploy `render.yaml` on Render with no PayPal keys: it runs the simulator, starts with sample jobs (`DEMO_SEED=1`) and needs no password. Free Render services sleep when idle, so open the link once before judging.

Register a webhook for `INVOICING.INVOICE.PAID` pointing at `https://<your-host>/api/paypal/webhook` in the PayPal developer dashboard and put its id in `PAYPAL_WEBHOOK_ID`. Signatures are verified with PayPal's `verify-webhook-signature` API. Deploy files are included: `Dockerfile` and `render.yaml`.

## What was and was not verified

Tested and passing (`pytest`, 47 tests): the copilot loop against a scripted fake Claude (tools called, results passed back, invalid proposals rejected, nothing executed, loop bounded, fallback on failure), offline copilot answers, the customer status view, pricing math, model sanity (stairs cost time, bigger jobs take longer), extraction, the full quote-to-cash flow over HTTP, approval gates, duplicate and forged webhooks, discount expiry and reissue, reminder schedule, overtime and waiver, cash payments, input validation, owner-route protection, and the exact requests the live PayPal client sends (checked against a fake PayPal server).

Not verified in the environment this was built in, because it had no PayPal credentials, no Anthropic key and no outbound access to those services:

* The live PayPal client has not talked to the real sandbox. Work through [docs/SANDBOX_CHECKLIST.md](docs/SANDBOX_CHECKLIST.md) first; it lists the five assumptions most likely to need a small fix (item discount, the paid webhook payload, QR response, invoice email, partial-payment setting).
* The Claude intake path and the Claude copilot have not been run against the real API. The copilot loop is tested with a scripted fake client that follows the Anthropic tool-use message format; the offline copilot and rule-based intake are what the browser walkthrough used. The rule-based fallback is what the tests and the accuracy number below exercise.

Intake accuracy of the rule-based extractor on the 1,500 labelled messages in `data/chats.jsonl` is 100 percent of fields, but those messages follow a limited set of phrasings, so treat this as a regression test and not as a forecast for free text. Claude is the intended extractor for free text and Arabic script; measure it with `ANTHROPIC_API_KEY=... python scripts/eval_extraction.py --limit 200` before quoting any number.

## How it works

```
 customer chat ──► intake agent (Claude tool use, or rules)
                        │ validated fields, questions for what is missing
                        ▼
                 duration model (gradient boosting, P10 to P90 range)
                        ▼
                 rate card ──► quote ──► owner approves
                                              │
                        ┌─────────────────────┘
                        ▼
   PayPal deposit invoice (QR) ──paid──► BOOKED ──job done──► balance invoice
                                                       (overtime, 5% early-pay discount)
                                                              │
        payment model: P(on time) ──► reminder timing and tone │
                                                              ▼
                                   discount expires ──► cancel and reissue at full price
                                                              ▼
                                           paid (PayPal confirms) ──► done
```

| Part | File |
|---|---|
| Chat intake, validation, follow-up questions | `app/intake.py` |
| Rate card, quotes, overtime | `app/pricing.py` |
| Duration and payment models | `app/ml.py`, trained by `train.py` (see [docs/ML.md](docs/ML.md)) |
| PayPal Invoicing client and simulator | `app/paypal.py` |
| States, webhooks, reminders, discount expiry, customer status | `app/workflow.py` |
| Owner copilot (Claude tool use, propose-only) | `app/copilot.py` |
| API and pages | `app/main.py`, `static/` (AG Grid Community is bundled in `static/vendor/`, MIT licensed, so no CDN is needed) |

Model results on the held-out test split of the included dataset: duration MAE 0.81 hours (a rooms-only linear baseline is 2.61), R2 0.93; P10 to P90 range covers the real value 75 percent of the time (nominal 80, slightly overconfident). Paid-on-time AUC is 0.95, which is optimistic; use it for reminder timing, not for exact days. Details in [docs/ML.md](docs/ML.md). Re-measure on your own job history before relying on them.

## Configuration

All settings are environment variables; see `.env.example`. Set `ADMIN_KEY` on any public deployment, because owner routes (jobs, approvals, metrics) then require the `X-Admin-Key` header. Customer chat and the PayPal webhook stay public.

## Sources

* PayPal Invoicing API v2: <https://developer.paypal.com/docs/api/invoicing/v2/>
* PayPal webhooks and signature verification: <https://developer.paypal.com/api/rest/webhooks/>
* PayPal AI toolkit: <https://github.com/paypal/ai-toolkit>
* Anthropic tool use: <https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview>

## License

MIT, see [LICENSE](LICENSE).
