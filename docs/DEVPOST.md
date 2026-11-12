# Devpost text (edit to match what you actually verified)

## Tagline
An AI agent that turns a moving customer's first message into a PayPal-paid job.

## Inspiration
One of us works with moving companies every day as a client account manager. Small moving companies lose money in the gaps: quotes given by phone that miss the stairs, deposits that never arrive, and final invoices that sit unpaid for weeks. The work between "how much will it cost?" and "the money is in the account" is repetitive, and every step is a place where a mistake costs real money.

## What it does
Three AI roles work under one rule: AI understands and explains, models estimate, fixed rules price, a person approves, PayPal moves the money.

* A customer chats in English or Arabic. The agent extracts the job details, asks only for what is missing, and shows a price range.
* A gradient-boosting model estimates hours, with a P10 to P90 range and a list of what drives the estimate (stairs, heavy items, distance). The company's rate card turns hours into a price.
* The owner approves. PayPal Invoicing sends a deposit invoice with a QR code.
* When PayPal confirms the deposit, the job is booked. After the move the owner enters the actual hours; MoveDesk adds overtime, applies an early-payment discount and sends the balance invoice.
* A second model estimates the chance each customer pays on time. Risky invoices get earlier, firmer reminders. If the discount window passes, MoveDesk cancels and reissues the invoice at full price.
* The customer's chat follows the job: it shows the confirmed price, a Pay with PayPal button and QR code, the booking, the final invoice and the thank-you, without the customer calling anyone.
* The owner has a copilot. Ask "who owes me money?" or "who should I chase?" and it answers from live data using read-only tools, then proposes actions (approve, send a reminder, record cash) as buttons. It has no tool that changes anything; the owner's click does.

## How we built it
FastAPI and SQLite on the backend, Google Gemini (free tier) function calling for intake (with a rule-based fallback) and for the owner copilot (read tools plus a validated propose-only action tool, with an offline fallback), scikit-learn for the two models, PayPal Invoicing v2 for invoices, QR codes, cash recording, reminders and cancellation, and PayPal webhooks (signature-verified, de-duplicated) for payment events. The dashboard uses AG Grid.

## The design rule
AI extracts and explains. A model estimates hours. Fixed math sets the price. The owner approves. PayPal moves the money. The language model has no path to the price, a job is marked paid only after reading the invoice back from PayPal, and customer text is untrusted data. The copilot can only propose. These guarantees are covered by an automated test suite of 56 tests, including a scripted fake model that tries invalid and unsafe proposals.

## Challenges
Making an AI agent safe around money: separating what the model may propose from what only code and PayPal may decide, and handling races such as a customer paying an invoice at the moment we replace it.

## Accomplishments
A complete quote-to-cash loop with human approval, tested guardrails, and a bilingual customer experience.

## What we learned
Most of the value is in the boring guarantees: idempotency keys, webhook de-duplication and reading state back from the payment provider.

## What is next
Run the intake against real customer messages and measure it, add card-on-file deposits, calendar scheduling, and a per-company rate-card editor.

## Built with
Python, FastAPI, Google Gemini API, scikit-learn, PayPal Invoicing API and webhooks, SQLite, AG Grid.

## Honest notes for the "try it" section
Click "Play the story" for a ten-step tour of the full cycle. Runs with no keys using a built-in PayPal simulator; the copilot then answers in a clearly labelled offline mode. With sandbox credentials it uses the PayPal sandbox (only claim this once docs/SANDBOX_CHECKLIST.md is done). Model accuracy figures describe the dataset included in the repository and should be re-measured on a company's own job history.
