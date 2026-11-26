import pytest
from eth_account import Account
from hexbytes import HexBytes

from eth_rescue.delegation import (
    SECP256K1_HALF_N,
    authorization_signer,
    replay_delegates,
)

COMPROMISED = Account.from_key(b"\x01" * 32)
OTHER = Account.from_key(b"\x02" * 32)
RESCUE = "0x" + "55" * 20
ATTACKER = "0x" + "99" * 20
SPONSOR = "0x" + "33" * 20
ZERO = "0x" + "00" * 20
CHAIN_ID = 1


def _auth(delegate, nonce, account=COMPROMISED, chain_id=CHAIN_ID):
    signed = Account.sign_authorization(
        {"chainId": chain_id, "address": delegate, "nonce": nonce}, account.key
    )
    return {
        "chainId": signed.chain_id,
        "address": delegate,
        "nonce": signed.nonce,
        "yParity": signed.y_parity,
        "r": HexBytes(signed.r.to_bytes(32)),
        "s": HexBytes(signed.s.to_bytes(32)),
    }


def _tx(sender=SPONSOR, *authorizations):
    return {"from": sender, "authorizationList": list(authorizations)}


def _replay(transactions, nonce=7, delegate=None):
    return replay_delegates(
        transactions, COMPROMISED.address, CHAIN_ID, nonce, delegate
    )


def test_authorization_signer_recovers_the_signer():
    assert authorization_signer(_auth(RESCUE, 7)) == COMPROMISED.address
    assert authorization_signer(_auth(RESCUE, 7, OTHER)) == OTHER.address


def test_authorization_signer_rejects_high_s_and_bad_parity():
    high_s = _auth(RESCUE, 7) | {"s": HexBytes((SECP256K1_HALF_N + 1).to_bytes(32))}
    bad_parity = _auth(RESCUE, 7) | {"yParity": 2}

    assert authorization_signer(high_s) is None
    assert authorization_signer(bad_parity) is None


def test_authorization_signer_of_a_tampered_tuple_is_someone_else():
    tampered = _auth(RESCUE, 7) | {"address": ATTACKER}

    assert authorization_signer(tampered) != COMPROMISED.address


def test_our_authorization_applies_at_the_current_nonce():
    assert _replay([_tx(SPONSOR, _auth(RESCUE, 7))]) == [RESCUE]


def test_a_compromised_transaction_ahead_spends_the_nonce():
    delegates = _replay(
        [_tx(COMPROMISED.address), _tx(SPONSOR, _auth(RESCUE, 7))], delegate=ATTACKER
    )

    assert delegates == [ATTACKER, ATTACKER]


def test_an_earlier_compromised_authorization_spends_the_nonce():
    delegates = _replay(
        [_tx(SPONSOR, _auth(ATTACKER, 7)), _tx(SPONSOR, _auth(RESCUE, 7))]
    )

    assert delegates == [ATTACKER, ATTACKER]


def test_sender_nonce_is_spent_before_its_own_authorizations():
    delegates = _replay(
        [
            _tx(COMPROMISED.address, _auth(ATTACKER, 7)),
            _tx(COMPROMISED.address, _auth(RESCUE, 9)),
        ]
    )

    assert delegates == [None, RESCUE]


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(_auth(ATTACKER, 7, OTHER), id="other-signer"),
        pytest.param(_auth(ATTACKER, 8), id="wrong-nonce"),
        pytest.param(_auth(ATTACKER, 7, chain_id=5), id="wrong-chain"),
    ],
)
def test_authorizations_that_do_not_apply_change_nothing(authorization):
    delegates = _replay([_tx(SPONSOR, authorization), _tx(SPONSOR, _auth(RESCUE, 7))])

    assert delegates == [None, RESCUE]


def test_chain_id_zero_authorizations_apply_on_any_chain():
    assert _replay([_tx(SPONSOR, _auth(ATTACKER, 7, chain_id=0))]) == [ATTACKER]


def test_redelegation_after_the_rescue_does_not_change_what_it_ran():
    delegates = _replay(
        [_tx(SPONSOR, _auth(RESCUE, 7)), _tx(SPONSOR, _auth(ATTACKER, 8))]
    )

    assert delegates == [RESCUE, ATTACKER]


def test_delegating_to_zero_clears_the_delegation():
    assert _replay([_tx(SPONSOR, _auth(ZERO, 7))], delegate=ATTACKER) == [None]


def test_later_authorizations_in_one_list_apply_in_order():
    delegates = _replay([_tx(SPONSOR, _auth(ATTACKER, 7), _auth(RESCUE, 8))])

    assert delegates == [RESCUE]
