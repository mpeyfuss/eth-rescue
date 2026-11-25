// SPDX-License-Identifier: MIT
pragma solidity 0.8.31;

import {LibCall} from "solady-0.1.26/utils/LibCall.sol";
import {ECDSA} from "@openzeppelin-contracts-5.6.1/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin-contracts-5.6.1/utils/cryptography/EIP712.sol";
import {IERC165} from "@openzeppelin-contracts-5.6.1/utils/introspection/IERC165.sol";
import {IERC721Receiver} from "@openzeppelin-contracts-5.6.1/token/ERC721/IERC721Receiver.sol";
import {IERC1155Receiver} from "@openzeppelin-contracts-5.6.1/token/ERC1155/IERC1155Receiver.sol";

/// @title EthRescue
/// @notice Stateless singleton used as an EIP-7702 delegation target for rescue operations.
/// @dev Every rescue is authorized by an EIP-712 signature from the delegating account's own key, so
///      the singleton has no privileged configuration and is safe to share.
///
///      EIP-712: OpenZeppelin's cached domain separator only applies when `address(this)` is the
///      singleton itself. Under delegation `address(this)` is the delegating account, so the domain is
///      rebuilt per call and binds every signature to that account and chain. The name and version are
///      short strings, so EIP712's storage fallbacks are never read; this matters because a delegated
///      account's storage may hold another delegate's leftovers.
///
///      Gas: the contract never reads `gasleft()`. Each action is capped by its stipend so a hostile
///      target cannot starve later actions or the sweep, and only the signed rescuer can submit, so
///      the transaction gas limit is the client's responsibility. Under EIP-150 a call receives its
///      full stipend only while 64/63 of it remains; the client budgets for that and keeps every
///      transaction within the EIP-7825 cap so no EIP-8037 state-gas reservoir is involved.
/// @author mpeyfuss
contract EthRescue is EIP712, IERC165, IERC721Receiver, IERC1155Receiver {
    /////////////////////////////////////////////////////////////////////
    // TYPES
    /////////////////////////////////////////////////////////////////////

    struct Action {
        address target;
        uint256 gasStipend;
        bytes data;
    }

    /// @param rescuer The only account that may submit this request.
    /// @param safe Receives the native-currency sweep. Asset destinations live in action calldata.
    /// @param deadline Last valid `block.timestamp`. Replay before it is harmless: only `rescuer` can
    ///        submit, and only to `safe`.
    /// @param actionsHash `keccak256(abi.encode(actions))`.
    struct RescueRequest {
        address rescuer;
        address safe;
        uint256 deadline;
        bytes32 actionsHash;
    }

    /////////////////////////////////////////////////////////////////////
    // CONSTANTS
    /////////////////////////////////////////////////////////////////////

    bytes32 public constant RESCUE_REQUEST_TYPEHASH =
        keccak256("RescueRequest(address rescuer,address safe,uint256 deadline,bytes32 actionsHash)");

    /////////////////////////////////////////////////////////////////////
    // EVENTS
    /////////////////////////////////////////////////////////////////////

    /// @dev Reports EVM call success only; a non-reverting call may still have failed at the protocol level.
    event ActionResult(uint256 indexed index, bool success);

    /////////////////////////////////////////////////////////////////////
    // ERRORS
    /////////////////////////////////////////////////////////////////////

    error NotRescuer();
    error Expired();
    error ActionsMismatch();
    error InvalidSignature();
    error InvalidSafe();
    error InvalidAction(uint256 index);
    error EthTransferFailed();

    /////////////////////////////////////////////////////////////////////
    // FUNCTIONS
    /////////////////////////////////////////////////////////////////////

    constructor() EIP712("EthRescue", "1") {}

    receive() external payable {}

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == type(IERC165).interfaceId || interfaceId == type(IERC721Receiver).interfaceId
            || interfaceId == type(IERC1155Receiver).interfaceId;
    }

    function onERC721Received(address, address, uint256, bytes calldata) external pure returns (bytes4) {
        return IERC721Receiver.onERC721Received.selector;
    }

    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external pure returns (bytes4) {
        return IERC1155Receiver.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(address, address, uint256[] calldata, uint256[] calldata, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        return IERC1155Receiver.onERC1155BatchReceived.selector;
    }

    /// @notice EIP-712 digest the delegating account's key must sign for `request`.
    function rescueRequestDigest(RescueRequest calldata request) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    RESCUE_REQUEST_TYPEHASH, request.rescuer, request.safe, request.deadline, request.actionsHash
                )
            )
        );
    }

    /// @notice Runs every action as optional (failures never revert) and sweeps the full native balance.
    function rescue(RescueRequest calldata request, Action[] calldata actions, bytes calldata signature) external {
        _verifyRequest(request, actions, signature);

        uint256 numActions = actions.length;
        for (uint256 i; i < numActions; ++i) {
            Action calldata action = actions[i];
            if (action.target == address(0) || action.target == address(this) || action.gasStipend == 0) {
                revert InvalidAction(i);
            }
        }

        for (uint256 i; i < numActions; ++i) {
            Action calldata action = actions[i];
            (bool success,,) = LibCall.tryCall(action.target, 0, action.gasStipend, 0, action.data);
            emit ActionResult(i, success);
        }

        _sweepEth(request.safe);
    }

    /////////////////////////////////////////////////////////////////////
    // INTERNAL FUNCTIONS
    /////////////////////////////////////////////////////////////////////

    function _verifyRequest(RescueRequest calldata request, Action[] calldata actions, bytes calldata signature)
        private
        view
    {
        if (msg.sender != request.rescuer) revert NotRescuer();
        if (block.timestamp > request.deadline) revert Expired();
        address safe = request.safe;
        if (safe == address(0) || safe == request.rescuer || safe == address(this)) revert InvalidSafe();
        if (keccak256(abi.encode(actions)) != request.actionsHash) revert ActionsMismatch();
        // Reverts with OpenZeppelin's ECDSA errors for malformed, wrong-length or high-s signatures.
        if (ECDSA.recoverCalldata(rescueRequestDigest(request), signature) != address(this)) revert InvalidSignature();
    }

    function _sweepEth(address safe) private {
        uint256 amount = address(this).balance;
        if (amount != 0) {
            (bool success,) = safe.call{value: amount}("");
            if (!success) revert EthTransferFailed();
        }
    }
}
