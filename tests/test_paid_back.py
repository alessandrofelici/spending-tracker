"""The Paid back page (/paid-back): confirming a Zelle payment, picking another
purchase for it, saying no and taking it back, payments still pending at the
bank, marking by hand from any month, and looking back over and undoing what
counts. Runs the real app offline (AppTest) on made-up rows."""

import re
from datetime import date

import pytest
from streamlit.testing.v1 import AppTest
from test_dashboard import button, metric
from test_reimbursements import checking
from test_storage import row

from spending import db, reimburse

PAGE = "paid_back_page.py"


@pytest.fixture
def page(app):
    """Open the dashboard, then the Paid back page."""

    def open_() -> AppTest:
        at = app()
        at.switch_page(PAGE).run()
        assert not at.exception, at.exception
        return at

    return open_


@pytest.fixture
def concert(conn):
    """Tickets and a dinner on the card, a Zelle payment that's a third of the
    tickets (and fits the dinner too), and a paycheck that's never proposed."""
    db.insert_transactions(
        conn,
        [
            row("concert", "TICKETS 1234 LANSING MI", 240.0, "2026-03-10"),
            row("dinner", "NOODLE HOUSE LANSING MI", 90.0, "2026-03-11"),
            row("feb", "PETSMART 1234 LANSING MI", 120.0, "2026-02-03"),
            checking("zelle", 80.0, "2026-03-12"),
            checking("paycheck", 1500.0, "2026-03-13"),
        ],
    )
    conn.commit()
    return conn


def keys(at: AppTest) -> list[str | None]:
    return [b.key for b in at.button]


def undos(at: AppTest) -> list[str]:
    """The Undo buttons, top to bottom."""
    return [k for b in at.button if (k := b.key) and k.startswith("undo-")]


def markdown(at: AppTest) -> list[str]:
    """What the page writes as markdown, before unescaping."""
    return [
        e.value
        for kind in ("markdown", "caption", "error", "warning", "subheader", "toast")
        for e in getattr(at, kind)
    ]


def texts(at: AppTest) -> str:
    """Everything the page says, as it reads (markdown escapes removed)."""
    return "\n".join(re.sub(r"\\(.)", r"\1", t) for t in markdown(at))


def no_bare_dollars(at: AppTest) -> bool:
    """Two bare $ in one markdown block would be shown as LaTeX math."""
    return not [t for t in markdown(at) if re.search(r"(?<!\\)\$", t)]


def links(at: AppTest) -> list[str]:
    return [e.proto.label for e in at.get("page_link")]


def pieces(conn) -> list[tuple]:
    return [
        (p["txn_id"], p["amount"], p["incoming_id"], p["note"])
        for p in db.get_reimbursements(conn)
    ]


def spending_total(at: AppTest) -> str:
    at.switch_page("dashboard.py").run()
    assert not at.exception, at.exception
    return metric(at, "Total spent")


def test_confirming_the_suggested_purchase(concert, page):
    at = page()
    page_text = texts(at)
    assert "To review (1)" in page_text
    assert "**$80.00** from **Friend** (Zelle)" in page_text
    # The best guess is an even share: a third of the tickets.
    picker = at.selectbox(key="for-zelle")
    assert picker.value == "concert"
    assert picker.options[0].endswith("$240.00 · 1/3")

    button(at, "ok-zelle").click().run()
    assert not at.exception, at.exception
    assert pieces(concert) == [("concert", 80.0, "zelle", None)]
    assert "ok-zelle" not in keys(at)  # used up
    assert "Counted $80.00 as paid back." in texts(at)  # a toast
    assert "paid back $80.00, $160.00 left" in texts(at)
    assert no_bare_dollars(at)
    assert spending_total(at) == "$250.00"  # March: 240 + 90 - 80


def test_picking_another_purchase_instead_of_cycling(concert, page):
    at = page()
    picker = at.selectbox(key="for-zelle")
    # Every purchase it fits, best first, then the others it could pay back.
    assert [o.split(" · ")[0] for o in picker.options] == [
        "TICKETS LANSING",
        "NOODLE HOUSE LANSING",
        "PETSMART LANSING",
    ]
    picker.select("feb").run()  # more than a week before the payment
    button(at, "ok-zelle").click().run()
    assert not at.exception, at.exception
    assert pieces(concert) == [("feb", 80.0, "zelle", None)]


def test_not_a_payback_can_be_taken_back(concert, page):
    at = page()
    button(at, "never-zelle").click().run()
    assert "ok-zelle" not in keys(at)
    assert "Hidden (1)" in [e.label for e in at.expander]
    assert "· not a payback" in texts(at)

    button(at, "unhide-zelle").click().run()
    assert not at.exception, at.exception
    assert "ok-zelle" in keys(at)
    assert at.expander == [] or "Hidden (1)" not in [e.label for e in at.expander]


def test_an_older_not_this_purchase_is_listed_and_can_be_taken_back(concert, page):
    db.dismiss_match(concert, "concert", "zelle")
    db.dismiss_match(concert, "dinner", "zelle")
    concert.commit()
    at = page()
    assert "ok-zelle" not in keys(at)  # nothing else fits within a week
    assert "2 purchases turned down" in texts(at)
    button(at, "unhide-zelle").click().run()
    assert at.selectbox(key="for-zelle").value == "concert"


def test_a_pending_payment_is_on_the_way(concert, page):
    today = date.today().isoformat()
    db.save_pending(
        concert,
        "item-chk",
        [
            {
                "id": "p1",
                "date": today,
                "description": "Zelle JORDAN EXAMPLE 1234567890 ZELLE CREDIT",
                "amount": -14.0,
            },
            {"id": "p2", "date": today, "description": "PAYROLL", "amount": -900.0},
        ],
    )
    concert.commit()
    at = page()
    assert "On the way (1)" in texts(at)
    assert "**$14.00** from **Jordan Example** (Zelle)" in texts(at)
    at.switch_page("dashboard.py").run()
    assert links(at) == ["1 Zelle payment to review · 1 on the way"]


def test_spending_links_here_only_when_there_is_something(conn, app):
    db.insert_transactions(
        conn, [row("concert", "TICKETS 1234 LANSING MI", 240.0, "2026-03-10")]
    )
    conn.commit()
    assert links(app()) == []
    db.insert_transactions(conn, [checking("zelle", 80.0, "2026-03-12")])
    conn.commit()
    assert links(app()) == ["1 Zelle payment to review"]


def test_marking_any_month_by_hand(concert, page):
    at = page()
    at.selectbox(key="mark-purchase-0").select("feb")
    at.number_input(key="mark-amount-0-None").set_value(60.0)
    at.text_input(key="mark-note-0").input("split in half")
    button(at, "mark-save").click().run()
    assert not at.exception, at.exception
    assert pieces(concert) == [("feb", 60.0, None, "split in half")]
    # The fields are cleared for the next one.
    assert at.selectbox(key="mark-purchase-1").value is None
    # The Zelle payment is still suggested: it wasn't used.
    assert "ok-zelle" in keys(at)


def test_marking_by_hand_can_use_up_a_zelle_payment(concert, page):
    at = page()
    at.selectbox(key="mark-purchase-0").select("feb")
    at.selectbox(key="mark-payment-0").select("zelle").run()
    # Picking the payment fills in what it brought in.
    assert at.number_input(key="mark-amount-0-zelle").value == 80.0
    button(at, "mark-save").click().run()
    assert pieces(concert) == [("feb", 80.0, "zelle", None)]
    assert "ok-zelle" not in keys(at)


@pytest.mark.parametrize(
    ("purchase", "amount", "error"),
    [("dinner", 100.0, "left to pay back"), (None, 10.0, "Pick the purchase")],
)
def test_marking_what_cant_be_paid_back_is_refused(
    concert, page, purchase, amount, error
):
    at = page()
    if purchase:
        at.selectbox(key="mark-purchase-0").select(purchase)
    at.number_input(key="mark-amount-0-None").set_value(amount)
    button(at, "mark-save").click().run()
    assert any(error in e.value for e in at.error)
    assert pieces(concert) == []


@pytest.fixture
def paid_back(concert):
    """The tickets paid back by Zelle and in cash, the dinner by hand."""
    db.add_reimbursement(concert, "concert", 80.0, incoming_id="zelle")
    db.add_reimbursement(concert, "concert", 80.0, note="cash")
    db.add_reimbursement(concert, "dinner", 45.0)
    concert.commit()
    return concert


def test_paid_back_so_far_is_one_row_per_purchase(paid_back, page):
    db.add_reimbursement(paid_back, "feb", 20.0)
    paid_back.commit()
    at = page()
    page_text = texts(at)
    assert "**TICKETS LANSING** · Mar 10" in page_text
    assert "paid back $160.00, $80.00 left" in page_text
    assert "$80.00 · Friend · Zelle · Mar 12" in page_text
    assert '$80.00 · by hand · "cash"' in page_text
    assert "paid back $45.00, $45.00 left" in page_text
    assert no_bare_dollars(at)

    at.selectbox(key="so-far-person").select("Friend").run()
    assert undos(at) == ["undo-1", "undo-all"]
    at.selectbox(key="so-far-person").select("By hand").run()
    # Newest purchase first: the dinner, the tickets, then February's.
    assert undos(at) == [
        "undo-3",
        "undo-2",
        "undo-4",
        "undo-all",
    ]
    at.selectbox(key="so-far-month").select("2026-02").run()
    assert undos(at) == ["undo-4", "undo-all"]
    at.selectbox(key="so-far-person").select("Friend").run()
    assert "Nothing for that month and person." in texts(at)


def test_undo_one_piece(paid_back, page):
    at = page()
    button(at, "undo-1").click().run()
    assert [p[2] for p in pieces(paid_back)] == [None, None]
    assert "ok-zelle" in keys(at)  # suggested again


def test_undo_all_shown_asks_first(paid_back, page):
    at = page()
    at.selectbox(key="so-far-person").select("By hand").run()
    button(at, "undo-all").click().run()
    assert any(
        "Undo 2 pieces paid back for 2 purchases?" in w.value
        and "2 pieces marked by hand can't be brought back" in w.value
        for w in at.warning
    )
    assert len(pieces(paid_back)) == 3  # nothing yet
    button(at, "undo-all-no").click().run()
    assert "undo-all-yes" not in keys(at)

    button(at, "undo-all").click().run()
    button(at, "undo-all-yes").click().run()
    assert not at.exception, at.exception
    assert pieces(paid_back) == [("concert", 80.0, "zelle", None)]


def test_sender_names_fall_back_to_the_banks_wording(conn, page):
    db.insert_transactions(
        conn,
        [
            row("gas", "SPEEDWAY 1234 LANSING MI", 42.0, "2026-03-10"),
            checking("z", 21.0, "2026-03-11") | {"description": "ONLINE ZELLE CR 1234"},
        ],
    )
    conn.commit()
    assert reimburse.sender("ONLINE ZELLE CR 1234") is None
    assert "**$21.00** · ONLINE ZELLE CR 1234" in texts(page())
