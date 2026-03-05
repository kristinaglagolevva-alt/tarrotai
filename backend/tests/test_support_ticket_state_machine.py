from features.support_tickets.state_machine import transition_ticket_status


def test_user_message_moves_to_pending_support():
    assert transition_ticket_status("open", "user_message") == "pending_support"
    assert transition_ticket_status("pending_user", "user_message") == "pending_support"


def test_support_reply_moves_to_pending_user():
    assert transition_ticket_status("open", "support_reply") == "pending_user"
    assert transition_ticket_status("pending_support", "support_reply") == "pending_user"


def test_close_and_reopen():
    assert transition_ticket_status("open", "close") == "closed"
    assert transition_ticket_status("closed", "reopen") == "open"


def test_pending_markers():
    assert transition_ticket_status("open", "mark_pending_support") == "pending_support"
    assert transition_ticket_status("open", "mark_pending_user") == "pending_user"

