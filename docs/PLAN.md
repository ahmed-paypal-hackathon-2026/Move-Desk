# MoveDesk: plan to submission

Today: Sat Oct 24, 2026. Devpost deadline: **Thu Nov 12, 12:00 pm PST** (10:00 pm in Cairo, check the
exact time on the page). Our internal submit date: **Tue Nov 10**, leaving a two-day buffer.

## What we are building

An AI agent for moving companies that takes a customer from first chat message to money collected:
chat, structured job, ML hour estimate, fixed-rate quote, owner approval, PayPal deposit invoice,
booking on payment, balance invoice with early-pay discount, smart reminders, dashboard.

Rule that keeps trust high: **AI extracts and explains, ML estimates hours, fixed math sets the price,
the owner approves, PayPal moves the money.**

## What judges score (from the Devpost page)

1. Technological implementation: deep PayPal use plus real AI/ML, non-trivial.
2. Design: a complete product, not a proof of concept.
3. Potential impact: a specific problem for a specific audience.
4. Innovation: how different it is from existing ideas.
5. Presentation: video shows it working end to end.

Hard requirements: working demo (hosted URL or clear run instructions), public repo with a license
visible in the About section, YouTube video under 3 minutes that is public, text description, list of
tools used. Mockups do not count.

## Done today

- Dataset: 60,000 jobs, 60,000 invoices, 40 companies, 1,500 labeled chats.
- Duration model: MAE 0.81 hours vs 2.61 for a rule of thumb. Quote range model.
- Days-to-pay and paid-on-time models.
- FastAPI endpoints for both, tested.

## Schedule

| Dates | Goal | Done when |
|---|---|---|
| Oct 24 to 26 | Foundations | PayPal sandbox app created. Repo public with MIT license. Deposit invoice created and paid in sandbox from code. FastAPI skeleton running with the ML router. |
| Oct 27 to 30 | AI intake and pricing | Claude turns chat text into validated JSON. Extraction accuracy measured on `chats.jsonl` and written in README. Rate card and price calculator done. Quote screen with owner Approve button. |
| Oct 31 to Nov 3 | Money flow | Approve sends deposit invoice with QR code. Webhook marks it paid and books the job. Balance invoice with early-pay discount. Cash payment recorded. Reminder scheduler uses the on-time probability. |
| Nov 4 to 7 | Product and polish | AG Grid dashboard: jobs by stage, cash collected, overdue. Customer chat page. Error states. Tests for the pricing math and webhook handler. |
| Nov 8 to 9 | Ship | Deploy (Render is a sponsor). README with run steps. Record and edit the video. |
| Nov 10 | Submit | Devpost form complete. Test the repo from a clean clone. |
| Nov 11 to 12 | Buffer | Fix anything judges might trip on. No new features. |

## First two days: verify these in the PayPal sandbox

I could not confirm these from docs alone, so test them before building on them.

1. How to take a deposit. Safest plan: two separate invoices (deposit, then balance).
2. That the early-payment discount rule on invoices behaves as expected.
3. Which webhook event fires when an invoice is paid, and that signature verification works.
4. That the Invoicing API is enabled on your sandbox business account and the QR code endpoint works.
5. How sandbox invoice emails reach a buyer account, so the demo can show a customer paying.

If any of these fail, switch to Orders plus payment links for the deposit and keep invoices for the balance.

## Suggested team split (adjust to your size)

- Mo: PayPal integration, webhooks, FastAPI backend, deployment.
- Teammate 2: Claude intake agent, extraction accuracy test, reminder tone (English and Arabic).
- Teammate 3: AG Grid dashboard and customer chat UI.
- Teammate 4: README, license, video script, recording, Devpost text.

## 3-minute demo script

1. (0:00) Problem: movers lose money on underestimated jobs and late payments. One sentence.
2. (0:15) Customer chats in plain language, including a heavy item and a walk-up.
3. (0:35) Agent shows the structured job. Estimate: hours with a range, then the fixed-rate quote and why.
4. (1:00) Owner taps Approve. Deposit invoice with QR code goes out.
5. (1:20) Customer pays in the PayPal sandbox. Dashboard flips to Booked via webhook.
6. (1:45) Move complete. Balance invoice with early-pay discount. One customer pays, one gets a reminder.
7. (2:15) Dashboard: cash collected, overdue, model accuracy panel.
8. (2:40) Close: who it is for, what is next.

## How we stand out

- A real vertical (movers) with real workflow detail, not a generic invoicing bot.
- ML where it matters: estimated hours and payment timing, with explainable coefficients on screen.
- Visible safety design: approval gates, validated AI output, no AI-made prices.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Invoicing API surprises in sandbox | Test in the first two days, fallback to Orders and payment links |
| Claude extraction errors | Pydantic validation, ask a follow-up question when a field is missing, accuracy reported |
| Scope creep | Features freeze on Nov 7 |
| Video runs long | Script to 2:45, rehearse twice, judges need not watch past 3:00 |
| Public repo leaks secrets | `.env.example` only, check git history before making the repo public |

## Prize targets

Best Use of PayPal + AI, Technological Implementation, Design, Best Demo Delivery, AG Grid prizes
(dashboard), Render prizes (hosting).
