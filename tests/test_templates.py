from rescue_scripts import templates


AUCTION_HOUSE = "0x1111111111111111111111111111111111111111"
CONTRACT = "0x2222222222222222222222222222222222222222"
VICTIM = "0x3333333333333333333333333333333333333333"
SAFE = "0x4444444444444444444444444444444444444444"


def test_transient_auction_house_erc721_rescue_delists_then_transfers():
    actions = templates.transient_auction_house_erc721_rescue(
        AUCTION_HOUSE, CONTRACT, VICTIM, SAFE, 123
    )

    assert actions == [
        {
            "address": AUCTION_HOUSE,
            "function_signature": "delist(address,uint256)",
            "args": [CONTRACT, 123],
            "gas_estimate": templates.GAS_TRANSIENT_DELIST,
            "description": "Delist ERC721 token #123 from Transient Auction House",
        },
        {
            "address": CONTRACT,
            "function_signature": "transferFrom(address,address,uint256)",
            "args": [VICTIM, SAFE, 123],
            "gas_estimate": templates.GAS_ERC721,
            "description": f"ERC721 token #123 -> {SAFE}",
        },
    ]
