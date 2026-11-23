# AP Invoice Agent — a local LLM agent for invoice matching, with code guardrails

This is an AI agent built to process vendor invoices the way that an accounts-payable clerk would: it reads a free-text invoice, looks up the purchase order, vendor and booking history in an ERP database, and decides to **approve**, **reject** or **escalate to a human**, and provides a reason and explanation.

I made this to run fully locally (Qwen3 8B via Ollama, SQLite) on real data sourced from the [BPI Challenge 2019](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1).

> **Why this project:** I previously built RPA bots (UiPath, Blue Prism) when I was working as a consultant at Capgemini. Rules-based bots can break when the invoice layouts vary or an exception merits a judgment call. This project tests what an LLM agent can do instead, and where it still needs deterministic code around it.

## Results

40 test invoices built from real history (14 clean, 26 with problems), adding one change for each row.

| Run | Change | Decision accuracy | Model wrongly approved a problem invoice | Wrongly approved after guardrail | Clean invoices flagged |
|---|---|---|---|---|---|
| 1 | Baseline agent + approval guardrail | 62% | 8 / 26 | 1 / 26 | 1 / 14 |
| 2 | Reason code decides the action; guardrail checks against the invoice text | 88% | 8 / 26 | **0** / 26 | **0** / 14 |
| 3 | Tool returns `vendor_matches_po` instead of the model comparing IDs | 90% | 2 / 26 | **0** / 26 | **0** / 14 |
| 4 | Tool returns `same_po_line` for duplicate checks | **98%** | 3 / 26 | **0** / 26 | **0** / 14 |

~7 seconds per invoice on a 16 GB laptop (CPU). Runs 1–2 used a first test set. Run 2 was repeated on a second, locked test set, which runs 3-4 were also done on.

### Key findings

1. **The model can make judgment calls, but code guardrails keep it safe.** In every run the 8B model approved some invoices that should have been stopped. Adding guardrails meant that after my first iteration, no problem invoices made it through.
2. **Put value/text comparisons in the tools, don't rely on prompting or the model.** The model looked up both vendor IDs and still declared `vendorID_0001` and `vendorID_0595` a match. Having the tool return a plain `vendor_matches_po: true/false` sharply reduced approvals for incorrect vendors. The same change, implemented for duplicates, fixed missing-receipt invoices being misread as duplicates.
3. **Let the model diagnose what the problem is, and let policy choose the action.** The model often identified the right problem (e.g. `PRICE_VARIANCE`) but picked the wrong action (reject instead of escalate). Mapping reason to action in code fixed that.

## What does the code do, and what does the LLM do?
- **The LLM does:** reading invoices with varied layouts and number formats (`13.115,18 EUR`, `EUR 13,115.18`, `4507-001752`, `Ref: 4507001752_00010`), deciding which lookups to make, and explaining the decision.
- **Code does:** all arithmetic and tolerance checks, the reason to action policy, and an independent re-check of every approval. If an approval doesn't hold up, it is overridden to *escalate* and logged.

Policy: an invoice may exceed the PO line value by 2 % or €10 (whichever is larger); a goods/service receipt that hasn't been invoiced yet is required for 3-way-match items; invoices of €50,000 or more always go to a human.

## The data

- **BPI Challenge 2019** event log from a multinational company: 251,734 PO line items, 1,595,923 events, 1,975 vendors, loaded into SQLite (`vendors`, `po_items`, `events`).
- **Rewinding history:** for each test case, one real invoice event and everything after it are hidden, so the agent sees the ERP exactly as it was when that invoice arrived.
- **Test invoices** are rendered as free text with varied formats. Scenarios and expected outcomes:

| Scenario | How it's made | Expected |
|---|---|---|
| clean (14) | real invoice, all checks pass | approve |
| high_value (4) | real invoice ≥ €50k | escalate |
| price_inflated (6) | amount raised 10–40 % | escalate |
| missing_receipt (6) | real invoice that arrived before any goods receipt | escalate |
| vendor_mismatch (5) | billed by a different real vendor | reject |
| unknown_po (5) | PO number that doesn't exist | reject |

## Limitations

- **Iterated on the same test set.** Runs 2–4 were tuned and evaluated on the same 40 invoices, so 98 % is likely optimistic. A fresh held-out set is the next step to better test the performance of this agent.
- **Small test set** (40 invoices) and a single local model.
- **Data semantics:** in BPI 2019, `Cumulative net worth (EUR)` is the line item's value repeated on every event, not a per-event amount, so receipts and invoices are counted instead of summed. This means what we are doing is a simplification of real three-way matching.
- **No duplicate-invoice scenario:** BPI has no vendor invoice numbers, so a re-sent invoice can't be distinguished from a recurring one.
- Vendors are anonymised (`vendor_0123`), and invoices are generated text rather than scanned PDFs.

## Next steps

- Evaluate on a fresh held-out test set
- Compare against a stronger local model - or better yet, a larger hosted model
- Add a vendor invoice-number register to support real duplicate detection
- PDF/OCR input and a simple review screen for escalated invoices

## Try it yourself!

1. Download `BPI_Challenge_2019.xes` from the [4TU page](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1) into `data/`
2. `pip install -r requirements.txt`
3. Install [Ollama](https://ollama.com) and run `ollama pull qwen3:8b`
4. Open `invoice_agent.ipynb` and run it top to bottom (building the database takes a few minutes)

```
invoice-agent/
├── invoice_agent.ipynb   # data loading, database, agent, test set, evaluation
├── src/tools.py          # the tools the agent can call
├── results_qwen3_8b.csv  # per-invoice results for the final run (v4)
├── requirements.txt
└── data/                 # not committed — download BPI 2019 here
```
