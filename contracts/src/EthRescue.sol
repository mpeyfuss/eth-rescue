// SPDX-License-Identifier: MIT
pragma solidity 0.8.38;

import {ECDSA} from "@openzeppelin-contracts-5.6.1/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin-contracts-5.6.1/utils/cryptography/EIP712.sol";
import {IERC165} from "@openzeppelin-contracts-5.6.1/utils/introspection/IERC165.sol";
import {IERC721Receiver} from "@openzeppelin-contracts-5.6.1/token/ERC721/IERC721Receiver.sol";
import {IERC1155Receiver} from "@openzeppelin-contracts-5.6.1/token/ERC1155/IERC1155Receiver.sol";
import {LibCall} from "solady-0.1.26/utils/LibCall.sol";

/// @title EthRescue
/// @notice Stateless singleton used as an EIP-7702 delegation target for rescue operations.
/// @dev Every rescue is authorized by an EIP-712 signature from the delegating account's own key, so
///      the singleton has no privileged configuration and is safe to share.
///
///      EIP-712: OpenZeppelin's cached domain separator only applies when `address(this)` is the
///      singleton itself. Under delegation `address(this)` is the delegating account, so the domain is
///      rebuilt per call and binds every signature to that account and chain.
///
///      Gas: the contract never checks `gasleft()` and records failed actions instead of reverting, so
///      `eth_estimateGas` can return a limit at which actions silently run out of gas. Clients should
///      budget instead: per action, its stipend plus 1/63 (EIP-150 forwards the full stipend only
///      while 64/63 of it remains), call overhead and calldata; plus fixed costs and the sweep.
/// @author mpeyfuss
/// @custom:version 1
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
    /// @param destination Receives the native-currency sweep. Asset destinations live in action calldata.
    /// @param deadline Last valid `block.timestamp`. Replay before it is harmless: only `rescuer` can
    ///        submit, and only to `destination`.
    /// @param actionsHash `keccak256(abi.encode(actions))`.
    struct RescueRequest {
        address rescuer;
        address destination;
        uint256 deadline;
        bytes32 actionsHash;
    }

    /////////////////////////////////////////////////////////////////////
    // CONSTANTS
    /////////////////////////////////////////////////////////////////////

    bytes32 public constant RESCUE_REQUEST_TYPEHASH =
        keccak256("RescueRequest(address rescuer,address destination,uint256 deadline,bytes32 actionsHash)");

    /////////////////////////////////////////////////////////////////////
    // EVENTS
    /////////////////////////////////////////////////////////////////////

    /// @dev Reports EVM call success only; a non-reverting call may still have failed at the protocol level.
    ///      A target without code is reported as a failure and is not called.
    event ActionResult(uint256 indexed index, bool success);

    /////////////////////////////////////////////////////////////////////
    // ERRORS
    /////////////////////////////////////////////////////////////////////

    error NotRescuer();
    error Expired();
    error ActionsMismatch();
    error InvalidSignature();
    error InvalidDestination();
    error InvalidAction(uint256 index);
    error EthTransferFailed();

    /////////////////////////////////////////////////////////////////////
    // FUNCTIONS
    /////////////////////////////////////////////////////////////////////

    /// @dev The version is a plain integer (1, 2, 3, ...) and goes up with every new deployment. Under
    ///      delegation the verifying contract is the account, not the singleton, so the version is what
    ///      keeps one deployment's signatures from validating under another. Bump the version tag above
    ///      and `EIP712_DOMAIN_VERSION` in `eth_rescue/contract.py` with it.
    ///      Keep name and version under 32 bytes. Longer strings make `eip712Domain()` read them from
    ///      storage, which under delegation is the account's, not the singleton's.
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
                    RESCUE_REQUEST_TYPEHASH, request.rescuer, request.destination, request.deadline, request.actionsHash
                )
            )
        );
    }

    /// @notice Runs every action as optional (failures never revert) and sweeps the full native balance.
    /// @return results Each action's `ActionResult` success flag. On-chain the caller is the rescuer EOA, so
    ///         nothing reads this; it exists for off-chain `eth_call` dry runs, which return data but not
    ///         logs. Clients use it to predict per-action failures before signing, including actions that
    ///         only succeed after an earlier one in the same batch (e.g. delist, then transfer).
    function rescue(RescueRequest calldata request, Action[] calldata actions, bytes calldata signature)
        external
        returns (bool[] memory results)
    {
        _verifyRequest(request, actions, signature);

        uint256 numActions = actions.length;
        for (uint256 i; i < numActions; ++i) {
            Action calldata action = actions[i];
            if (action.target == address(0) || action.target == address(this) || action.gasStipend == 0) {
                revert InvalidAction(i);
            }
        }

        results = new bool[](numActions);
        for (uint256 i; i < numActions; ++i) {
            bool success = _call(actions[i]);
            results[i] = success;
            emit ActionResult(i, success);
        }

        _sweepEth(request.destination);
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
        address destination = request.destination;
        if (destination == address(0) || destination == request.rescuer || destination == address(this)) revert InvalidDestination();
        if (keccak256(abi.encode(actions)) != request.actionsHash) revert ActionsMismatch();
        // Reverts with OpenZeppelin's ECDSA errors for malformed, wrong-length or high-s signatures.
        if (ECDSA.recoverCalldata(rescueRequestDigest(request), signature) != address(this)) revert InvalidSignature();
    }

    /// @dev A target without code is reported as a failure: a codeless call always "succeeds" and proves
    ///      nothing. No return data is copied (`maxCopy` 0), which bounds hostile return data.
    function _call(Action calldata action) private returns (bool success) {
        if (action.target.code.length == 0) return false;
        (success,,) = LibCall.tryCall(action.target, 0, action.gasStipend, 0, action.data);
    }

    function _sweepEth(address destination) private {
        uint256 amount = address(this).balance;
        if (amount != 0) {
            (bool success,) = destination.call{value: amount}("");
            if (!success) revert EthTransferFailed();
        }
    }
}
