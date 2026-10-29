# Demo video script (target 2:45, hard limit 3:00)

Upload to YouTube as public. Record at 1080p with two windows side by side: the dashboard (left, wide) and the customer chat at /chat (right, narrow). Before recording: Clear all, then Add sample requests, so the board has jobs at every stage. Then click Play the story and click through it while you talk. The story drives everything; you only narrate.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Dashboard with sample jobs | "I work with moving companies. They quote by phone, chase deposits by text, and wait weeks to get paid. MoveDesk is an AI agent that runs the whole cycle, from the first message to PayPal cash." |
| 0:15 | Story step 1: customer message | "A customer describes the move in plain words, at any hour, in English or Arabic." |
| 0:30 | Step 2: agent's reply | "The agent pulls out the details, and gives a price range. The hours come from a model trained on past jobs. The price comes from the company's own rate card, never from the AI." |
| 0:50 | Step 3: drawer, Why this price | "The owner sees exactly why: stairs add time, packing adds time, and every guess the agent made is listed. Nothing is sent until the owner approves." |
| 1:05 | Step 4: deposit invoice, chat shows Pay deposit | "Approve, and PayPal sends the deposit invoice with a QR code. The customer's chat updates on its own." |
| 1:20 | Step 5: Booked | "The customer pays. PayPal notifies us, and we read the invoice back from PayPal before trusting it. Booked." |
| 1:35 | Step 6: final invoice | "After the move, it ran an hour and a half long. Overtime is added at the rate card price, and a 5 percent discount rewards paying within three days. A second model predicts how likely this customer is to pay on time." |
| 1:55 | Steps 7 and 8: fast-forward | "If they wait, the discount ends: MoveDesk cancels that invoice in PayPal and issues one at full price. Reminders go out on a schedule set by the risk model." |
| 2:15 | Step 9: copilot answer | "The owner can just ask: who owes me money? The copilot answers from live data and proposes actions as buttons. It can never act on its own." |
| 2:30 | Step 10: Paid | "Paid. AI understands and explains. A model estimates. Fixed rules price. A person approves. PayPal moves the money." |
| 2:40 | Terminal: pytest, 47 passed | "Every one of those guardrails is covered by automated tests." |

Honesty rules for the recording:
* If the dashboard tag says "PayPal simulator", say "shown here with the built-in PayPal simulator" once, early. Only say "PayPal sandbox" if docs/SANDBOX_CHECKLIST.md is done and the tag reads "PayPal sandbox".
* If no ANTHROPIC_API_KEY is set, the copilot answers in offline mode and says so on screen. Do not describe that answer as Claude. With a key set, ask it a free-form question instead, such as "Which job made the most money and why did it take so long?"
