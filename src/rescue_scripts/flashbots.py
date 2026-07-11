from typing import Any, cast

from eth_account.signers.local import LocalAccount
from eth_typing import URI
from flashbots.flashbots import Flashbots
from flashbots.middleware import construct_flashbots_middleware
from flashbots.provider import FlashbotProvider
from flashbots.types import FlashbotsOpts
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.module import attach_modules

BUILDERS = [
    "flashbots",
    "rsync",
    "beaverbuild.org",
    "builder0x69",
    "Titan",
    "payload",
    "bobthebuilder",
]


class CustomFlashbots(Flashbots):
    def send_raw_bundle_munger(
        self,
        signed_bundled_transactions: list[HexBytes],
        target_block_number: int,
        opts: FlashbotsOpts | None = None,
    ) -> list[Any]:
        resp = super().send_raw_bundle_munger(
            signed_bundled_transactions, target_block_number, opts
        )
        resp[0]["builders"] = BUILDERS
        return resp


class FlashbotsWeb3(Web3):
    flashbots: CustomFlashbots


def flashbot(
    w3: Web3,
    signature_account: LocalAccount,
    endpoint_uri: URI | str | None = None,
) -> FlashbotsWeb3:
    flashbots_provider = FlashbotProvider(signature_account, endpoint_uri)
    flash_middleware = construct_flashbots_middleware(flashbots_provider)
    w3.middleware_onion.add(flash_middleware)

    # attach modules to add the new namespace commands
    attach_modules(w3, {"flashbots": (CustomFlashbots,)})

    return cast(FlashbotsWeb3, w3)
