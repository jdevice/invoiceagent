"""Tools the AP invoice agent can call.

Each public function is a tool: plain Python with type hints and a docstring.
Ollama turns the signature and docstring into the tool description the model
sees, so the docstrings are written as instructions to the model.

Data note: in BPI 2019, "Cumulative net worth (EUR)" is the line item's value
repeated on every event, not the amount of each event. So receipts and
invoices are COUNTED, not summed.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.cwd() / "data" / "erp.db"
con = sqlite3.connect(DB_PATH, check_same_thread=False)
con.row_factory = sqlite3.Row

PO_VALUE_ACTIVITIES = ("Create Purchase Order Item", "Change Price", "Change Quantity")
RECEIPT_ACTIVITIES = ("Record Goods Receipt", "Record Service Entry Sheet")
TOLERANCE_PCT = 0.02        # invoice may exceed the line value by 2 % ...
TOLERANCE_EUR = 10.0        # ... or EUR 10, whichever is larger
HIGH_VALUE_EUR = 50_000     # at or above this, a human must approve


# ---------------------------------------------------------------- helpers --

def _rows(sql, params=()):
    return [dict(r) for r in con.execute(sql, params)]


def _find_item(po_number, item_no):
    po = "".join(c for c in str(po_number) if c.isdigit())
    rows = _rows("""SELECT p.*, v.vendor_name FROM po_items p
                    JOIN vendors v USING (vendor_id)
                    WHERE p.po_number = ? AND p.item_no = ?""", (po, int(item_no)))
    return rows[0] if rows else None


def _financials(case_id):
    """Line value plus receipt/invoice counts for one line item."""
    line_value = 0.0
    receipt_times = set()
    cancelled_receipts = 0
    n_invoices = 0
    for e in _rows("SELECT activity, ts, net_worth FROM events WHERE case_id = ? ORDER BY event_id",
                   (case_id,)):
        a = e["activity"]
        if a in PO_VALUE_ACTIVITIES:
            line_value = e["net_worth"] or 0.0        # latest PO value wins
        elif a in RECEIPT_ACTIVITIES:
            receipt_times.add(e["ts"])                # GR + service entry at the same time = one receipt
        elif a == "Cancel Goods Receipt":
            cancelled_receipts += 1
        elif a == "Record Invoice Receipt":
            n_invoices += 1
        elif a == "Cancel Invoice Receipt":
            n_invoices -= 1
    n_receipts = len(receipt_times) - cancelled_receipts
    return {"line_value": round(line_value, 2), "n_receipts": n_receipts, "n_invoices": n_invoices,
            "uninvoiced_receipts": max(n_receipts - n_invoices, 0)}


# ------------------------------------------------------------------ tools --

def get_po_item(po_number: str, item_no: int) -> dict:
    """Look up one purchase-order line item: its vendor, match type, line value,
    receipt and invoice counts, and recent history.

    Args:
        po_number: Purchase order number, digits only, e.g. "4507000001"
        item_no: Line item number as an integer, e.g. 1 (not "00001")
    """
    it = _find_item(po_number, item_no)
    if not it:
        return {"error": "PO line item not found"}
    history = _rows("""SELECT ts, activity FROM events WHERE case_id = ?
                       ORDER BY event_id DESC LIMIT 15""", (it["case_id"],))[::-1]
    return {
        "po_number": it["po_number"],
        "item_no": it["item_no"],
        "vendor_id": it["vendor_id"],
        "vendor_name": it["vendor_name"],
        "item_category": it["item_category"],
        "goods_receipt_required": bool(it["goods_receipt"]),
        "description": it["sub_spend_area"],
        **_financials(it["case_id"]),
        "recent_history": history,
    }


def search_vendors(name: str) -> list:
    """Find vendors by (part of) their name, e.g. "vendor_0016". Use this to get the
    vendor_id of the vendor named on an invoice.

    Args:
        name: Full or partial vendor name
    """
    return _rows("SELECT vendor_id, vendor_name FROM vendors WHERE vendor_name LIKE ? LIMIT 10",
                 (f"%{name}%",))


def find_booked_invoices(vendor_id: str, amount: float, po_number: str = "", item_no: int = 0) -> list:
    """Find invoices already booked for this vendor with (almost) the same amount.
    same_po_line is true when a booked invoice is for the same PO and line item as the new invoice.
    Only a booked invoice with same_po_line true can make the new invoice a duplicate.

    Args:
        vendor_id: Vendor id, e.g. "vendorID_0016"
        amount: Net invoice amount in EUR
        po_number: PO number of the new invoice, digits only
        item_no: Line item number of the new invoice
    """
    tol = max(abs(amount) * 0.005, 0.01)
    po = "".join(c for c in str(po_number) if c.isdigit())
    rows = _rows("""SELECT p.po_number, p.item_no, e.ts, e.net_worth AS amount
                    FROM events e JOIN po_items p USING (case_id)
                    WHERE p.vendor_id = ? AND e.activity = 'Record Invoice Receipt'
                      AND e.net_worth BETWEEN ? AND ?
                    ORDER BY e.ts DESC LIMIT 10""", (vendor_id, amount - tol, amount + tol))
    for r in rows:
        r["same_po_line"] = (r["po_number"] == po and r["item_no"] == int(item_no))
    return rows


def check_three_way_match(po_number: str, item_no: int, amount: float, invoice_vendor_name: str = "") -> dict:
    """Check an invoice against the PO line item: does the invoicing vendor match the PO's vendor,
    is the amount within the line value (plus tolerance), is there a receipt that hasn't been
    invoiced yet, and is it above the approval threshold? Always use this instead of comparing
    or calculating yourself.

    Args:
        po_number: Purchase order number, digits only
        item_no: Line item number as an integer
        amount: Net invoice amount in EUR, as a plain number, e.g. 1234.56
        invoice_vendor_name: Vendor name exactly as written on the invoice
    """
    it = _find_item(po_number, item_no)
    if not it:
        return {"error": "PO line item not found"}
    f = _financials(it["case_id"])
    tolerance = round(max(f["line_value"] * TOLERANCE_PCT, TOLERANCE_EUR), 2)
    needs_receipt = bool(it["goods_receipt"])
    return {
        "invoice_amount": amount,
        "line_value": f["line_value"],
        "tolerance": tolerance,
        "amount_within_tolerance": amount <= f["line_value"] + tolerance,
        "goods_receipt_required": needs_receipt,
        "receipts": f["n_receipts"],
        "invoices_already_booked": f["n_invoices"],
        "receipt_ok": (not needs_receipt) or f["uninvoiced_receipts"] > 0,
        "above_high_value_threshold": amount >= HIGH_VALUE_EUR,
        "po_vendor_id": it["vendor_id"],
        "vendor_matches_po": (it["vendor_id"] in {v["vendor_id"] for v in search_vendors(invoice_vendor_name)
                                                  if v["vendor_name"] == invoice_vendor_name})
                             if invoice_vendor_name else None,       
    }


# the agent loop looks tools up by name
TOOLS = {f.__name__: f for f in (get_po_item, search_vendors, find_booked_invoices, check_three_way_match)}
