// SPDX-License-Identifier: MIT
pragma solidity 0.8.38;

import {Script, console} from "forge-std-1.16.2/Script.sol";
import {EthRescue} from "../src/EthRescue.sol";

interface ICreateX {
    function deployCreate2(bytes32 salt, bytes memory initCode) external payable returns (address newContract);
}

/// @notice Deploys EthRescue through CreateX at the same address on every chain, whoever sends it.
/// @dev The salt's first 20 bytes are zero and byte 21 is 0x00, so CreateX guards it as
///      `keccak256(abi.encode(SALT))`: no sender and no chain id. The address then depends only on the
///      init code, which `foundry.toml` pins (compiler, EVM version, no metadata hash).
///
///      Dry run:  forge script script/Deploy.s.sol --rpc-url <rpc>
///      Deploy:   forge script script/Deploy.s.sol --rpc-url <rpc> --account <keystore> --broadcast
///
///      Running it where EthRescue already exists deploys nothing. The printed runtime codehash
///      differs per chain (EIP-712 caches the chain id in an immutable); pin it in `networks.py`.
contract Deploy is Script {
    ICreateX internal constant CREATEX = ICreateX(0xba5Ed099633D3B313e4D5F7bdc1305d3c28ba5Ed);
    /// @dev Runtime codehash of the canonical CreateX build (same on every chain). Only that factory
    ///      guarantees the address below holds exactly this init code.
    bytes32 internal constant CREATEX_CODEHASH = 0xbd8a7ea8cfca7b4e5f5041d7d4b17bc317c5ce42cfbc42066a00cf26b43eb53f;
    bytes32 internal constant SALT = bytes32(abi.encodePacked(bytes20(0), bytes1(0x00), bytes11("EthRescueV1")));

    error CreateXMissing();
    error UnexpectedCreateX(bytes32 codehash);
    error UnexpectedAddress(address deployed, address expected);

    function run() external returns (address deployed) {
        if (address(CREATEX).code.length == 0) revert CreateXMissing();
        if (address(CREATEX).codehash != CREATEX_CODEHASH) revert UnexpectedCreateX(address(CREATEX).codehash);

        bytes memory initCode = type(EthRescue).creationCode;
        address expected = predictedAddress(initCode);
        console.log("Chain id:      ", block.chainid);
        console.log("Init code hash:");
        console.logBytes32(keccak256(initCode));
        console.log("EthRescue:     ", expected);

        if (expected.code.length == 0) {
            vm.broadcast();
            deployed = CREATEX.deployCreate2(SALT, initCode);
            if (deployed != expected) revert UnexpectedAddress(deployed, expected);
            console.log("Deployed.");
        } else {
            deployed = expected;
            console.log("Already deployed; nothing sent.");
        }

        console.log("Runtime codehash (eth_rescue_codehash for this chain):");
        console.logBytes32(deployed.codehash);
    }

    function predictedAddress(bytes memory initCode) public pure returns (address) {
        bytes32 guardedSalt = keccak256(abi.encode(SALT));
        return vm.computeCreate2Address(guardedSalt, keccak256(initCode), address(CREATEX));
    }
}
