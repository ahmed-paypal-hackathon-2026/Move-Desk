# PayPal sandbox checklist

The live client (`app/paypal.py`, class `LivePayPal`) follows PayPal's Invoicing v2 documentation but has not been run against the real sandbox. Do this once before recording the demo. Budget two hours. Fix anything that differs in `build_invoice_payload` or `LivePayPal`, then run `pytest` again.

Reference: <https://developer.paypal.com/docs/api/invoicing/v2/>

## Setup

1. Create a sandbox app at <https://developer.paypal.com/dashboard/applications/sandbox>. Copy the client id and secret into `.env`.
2. In the dashboard, open Sandbox accounts. You need a business account (the merchant, owns the app) and a personal account (the customer). Use the personal account's email as the customer email in chat.
3. Add a webhook: URL `https://<public-host>/api/paypal/webhook` (use a tunnel such as ngrok while testing locally), event `INVOICING.INVOICE.PAID`. Put its id in `PAYPAL_WEBHOOK_ID`.
4. Start the app with the credentials loaded. The dashboard tag should read "PayPal sandbox".

## Five things to confirm, in order

| # | Assumption in the code | How to check | If it fails |
|---|---|---|---|
| 1 | Invoice creation accepts our payload and returns an `href` whose last segment is the invoice id | Approve a quote. The job should move to Deposit sent with an invoice URL | Read the error in the job timeline (it includes PayPal's debug id). Adjust `build_invoice_payload`. The `partial_payment` shape is the likeliest culprit |
| 2 | `POST .../send` emails the customer, and `GET` returns `detail.metadata.recipient_view_url` | Check the sandbox personal account's email (<https://www.sandbox.paypal.com>) | If the URL is empty, build it from the invoice id as PayPal documents |
| 3 | Item-level `discount.percent` gives the 5 percent early-pay discount | Complete a job. The balance invoice total should be 95 percent of the balance | If rejected or ignored, issue the discounted amount as a negative line item instead |
| 4 | The paid webhook has `event_type` `INVOICING.INVOICE.PAID` and the invoice id at `resource.id` (PayPal's documented shape; the handler also accepts `resource.invoice.id`) | Pay the deposit as the sandbox customer. The job should become Booked. If not, open the webhook delivery log in the dashboard | Adjust the id lookup in `handle_paypal_event` |
| 5 | `generate-qr-code` returns the QR (docs describe both Base64 PNG text and a JSON body; `qr_data_uri` accepts raw PNG, Base64 or JSON) | The deposit card should show a scannable QR | Save the real response as a test fixture; adjust `qr_data_uri` only if it is a fourth format |

## Also try

* Record a cash payment from the dashboard. The invoice should show as paid in PayPal.
* Send a reminder (advance the clock is simulator-only; in sandbox, call `POST /api/admin/run-reminders` after lowering `p_on_time` in the database, or wait for the schedule).
* Cancel and reissue: the discount expiry path calls cancel then create. Confirm the old invoice shows as cancelled and the customer receives the new one.
* Replay a webhook from the dashboard delivery log. The app should answer `duplicate` the second time.

## Record the result

Write what you changed here, so the README can say "verified on <date>". Until then, the README states that the live path is unverified, and it should keep saying so.
