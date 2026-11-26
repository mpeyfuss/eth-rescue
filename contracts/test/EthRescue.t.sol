// SPDX-License-Identifier: MIT
pragma solidity 0.8.38;

import {Test} from "forge-std-1.16.2/Test.sol";
import {ERC20} from "solady-0.1.26/tokens/ERC20.sol";
import {ERC721} from "solady-0.1.26/tokens/ERC721.sol";
import {ERC1155} from "solady-0.1.26/tokens/ERC1155.sol";
import {EthRescue} from "src/EthRescue.sol";
import {ECDSA} from "@openzeppelin-contracts-5.6.1/utils/cryptography/ECDSA.sol";

contract CallRecorder {
    address public caller;
    uint256 public calls;

    function record() external {
        caller = msg.sender;
        ++calls;
    }
}

contract RevertingTarget {
    error Reverted();

    function fail() external pure {
        revert Reverted();
    }
}

contract FalseReturningTarget {
    function returnFalse() external pure returns (bool) {
        return false;
    }
}

contract NoReturnTarget {
    uint256 public calls;

    function run() external {
        ++calls;
    }
}

contract GasBurningTarget {
    function burn() external pure {
        assembly {
            for {} 1 {} {}
        }
    }
}

contract ReturnDataBomb {
    function explode() external pure {
        bytes memory data = new bytes(32_000);
        assembly {
            return(add(data, 0x20), mload(data))
        }
    }
}

contract RefundTarget {
    address public caller;

    function refund() external {
        caller = msg.sender;
        (bool success,) = msg.sender.call{value: address(this).balance}("");
        require(success);
    }

    receive() external payable {}
}

contract RejectingSafe {
    receive() external payable {
        revert();
    }
}

contract ReentrantTarget {
    bytes4 public revertSelector;

    /// @dev Names itself as rescuer to get past the sender check; the missing victim signature must stop it.
    function attemptRescue() external {
        EthRescue.Action[] memory actions = new EthRescue.Action[](0);
        EthRescue.RescueRequest memory request = EthRescue.RescueRequest({
            rescuer: address(this),
            safe: address(0xdead),
            deadline: block.timestamp,
            actionsHash: keccak256(abi.encode(actions))
        });
        try EthRescue(payable(msg.sender)).rescue(request, actions, "") {}
        catch (bytes memory reason) {
            if (reason.length >= 4) {
                bytes4 selector;
                assembly {
                    selector := mload(add(reason, 0x20))
                }
                revertSelector = selector;
            }
        }
    }
}

contract FixtureERC20 is ERC20 {
    function name() public pure override returns (string memory) {
        return "Fixture ERC20";
    }

    function symbol() public pure override returns (string memory) {
        return "F20";
    }

    function mint(address account, uint256 amount) external {
        _mint(account, amount);
    }
}

contract FixtureERC721 is ERC721 {
    function name() public pure override returns (string memory) {
        return "Fixture ERC721";
    }

    function symbol() public pure override returns (string memory) {
        return "F721";
    }

    function tokenURI(uint256) public pure override returns (string memory) {
        return "";
    }

    function mint(address account, uint256 tokenId) external {
        _mint(account, tokenId);
    }
}

contract FixtureERC1155 is ERC1155 {
    function uri(uint256) public pure override returns (string memory) {
        return "";
    }

    function mint(address account, uint256 tokenId, uint256 amount) external {
        _mint(account, tokenId, amount, "");
    }
}

contract FixtureOwnable {
    address public owner;

    constructor(address initialOwner) {
        owner = initialOwner;
    }

    function transferOwnership(address newOwner) external {
        require(msg.sender == owner);
        owner = newOwner;
    }
}

contract EthRescueTest is Test {
    uint256 private constant VICTIM_KEY = 0xA11CE;
    uint256 private constant OTHER_KEY = 0xB0B;
    uint256 private constant SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;

    address private victim;
    address private rescuer;
    address private safe;
    EthRescue private implementation;
    EthRescue private delegated;

    function setUp() public {
        victim = vm.addr(VICTIM_KEY);
        rescuer = makeAddr("rescuer");
        safe = makeAddr("safe");
        implementation = new EthRescue();
        delegated = EthRescue(payable(victim));
        vm.signAndAttachDelegation(address(implementation), VICTIM_KEY);
        _rescue(_emptyActions());
    }

    // ---------------------------------------------------------------
    // Singleton and EIP-712
    // ---------------------------------------------------------------

    function testDigestMatchesEip712Encoding() public view {
        EthRescue.RescueRequest memory request = _request(_emptyActions());

        assertEq(delegated.rescueRequestDigest(request), _digest(victim, request));
    }

    function testDigestTracksChainIdAfterDeployment() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        vm.chainId(vm.getChainId() + 1);

        assertEq(delegated.rescueRequestDigest(request), _digest(victim, request));
    }

    function testEip712DomainReportsDelegatingAccount() public view {
        (bytes1 fields, string memory name, string memory version, uint256 chainId, address verifyingContract,,) =
            delegated.eip712Domain();

        assertEq(fields, hex"0f");
        assertEq(name, "EthRescue");
        assertEq(version, "1");
        assertEq(chainId, block.chainid);
        assertEq(verifyingContract, victim);
    }

    function testDomainIgnoresLeftoverStorageInDelegatingAccount() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        for (uint256 slot; slot < 8; ++slot) {
            vm.store(victim, bytes32(slot), bytes32(type(uint256).max));
        }

        assertEq(delegated.rescueRequestDigest(request), _digest(victim, request));
        (, string memory name,,,,,) = delegated.eip712Domain();
        assertEq(name, "EthRescue");
    }

    function testDomainIsBoundToDelegatingAccount() public view {
        EthRescue.RescueRequest memory request = _request(_emptyActions());

        assertNotEq(implementation.rescueRequestDigest(request), delegated.rescueRequestDigest(request));
    }

    function testOnlyBoundRescuerCanSubmit(address hacker) public {
        vm.assume(hacker != rescuer);
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory signature = _sign(VICTIM_KEY, victim, request);

        vm.prank(hacker);
        vm.expectRevert(EthRescue.NotRescuer.selector);
        delegated.rescue(request, _emptyActions(), signature);
    }

    function testRejectsSignatureFromAnotherKey() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());

        vm.expectRevert(EthRescue.InvalidSignature.selector);
        _submit(request, _emptyActions(), _sign(OTHER_KEY, victim, request));
    }

    function testRejectsSignatureForAnotherDelegatedAccount() public {
        address other = vm.addr(OTHER_KEY);
        vm.signAndAttachDelegation(address(implementation), OTHER_KEY);
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory otherSignature = _sign(OTHER_KEY, other, request);

        vm.expectRevert(EthRescue.InvalidSignature.selector);
        _submit(request, _emptyActions(), otherSignature);
    }

    function testRejectsSignatureFromAnotherChain() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory signature = _sign(VICTIM_KEY, victim, request);
        vm.chainId(vm.getChainId() + 1);

        vm.expectRevert(EthRescue.InvalidSignature.selector);
        _submit(request, _emptyActions(), signature);
    }

    function testRejectsSignatureOnImplementation() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory signature = _sign(VICTIM_KEY, address(implementation), request);

        vm.prank(rescuer);
        vm.expectRevert(EthRescue.InvalidSignature.selector);
        implementation.rescue(request, _emptyActions(), signature);
    }

    function testRejectsHighSAndMalformedSignatures() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(VICTIM_KEY, _digest(victim, request));
        bytes32 highS = bytes32(SECP256K1_N - uint256(s));

        vm.expectRevert(abi.encodeWithSelector(ECDSA.ECDSAInvalidSignatureS.selector, highS));
        _submit(request, _emptyActions(), abi.encodePacked(r, highS, v == 27 ? uint8(28) : uint8(27)));

        vm.expectRevert(abi.encodeWithSelector(ECDSA.ECDSAInvalidSignatureLength.selector, 0));
        _submit(request, _emptyActions(), "");

        vm.expectRevert(abi.encodeWithSelector(ECDSA.ECDSAInvalidSignatureLength.selector, 64));
        _submit(request, _emptyActions(), abi.encodePacked(r, s));

        vm.expectRevert(ECDSA.ECDSAInvalidSignature.selector);
        _submit(request, _emptyActions(), abi.encodePacked(bytes32(0), bytes32(0), uint8(27)));
    }

    function testRejectsExpiredRequest() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        vm.warp(request.deadline + 1);

        vm.expectRevert(EthRescue.Expired.selector);
        _submit(request, _emptyActions());
    }

    function testAcceptsRequestAtDeadline() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        vm.warp(request.deadline);
        vm.deal(victim, 1 ether);

        _submit(request, _emptyActions());

        assertEq(safe.balance, 1 ether);
    }

    function testRejectsActionsOtherThanSigned() public {
        CallRecorder recorder = new CallRecorder();
        EthRescue.RescueRequest memory request = _request(_emptyActions());

        vm.expectRevert(EthRescue.ActionsMismatch.selector);
        _submit(request, _singleAction(address(recorder), 100_000, abi.encodeCall(recorder.record, ())));
    }

    function testRejectsSafeOtherThanSigned() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory signature = _sign(VICTIM_KEY, victim, request);
        request.safe = makeAddr("attacker-safe");

        vm.expectRevert(EthRescue.InvalidSignature.selector);
        _submit(request, _emptyActions(), signature);
    }

    function testRejectsInvalidSafe() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        address[3] memory invalid = [address(0), rescuer, victim];
        for (uint256 i; i < invalid.length; ++i) {
            request.safe = invalid[i];
            vm.expectRevert(EthRescue.InvalidSafe.selector);
            _submit(request, _emptyActions());
        }
    }

    function testReplayBeforeDeadlineOnlyRepeatsTheSignedSweep() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        bytes memory signature = _sign(VICTIM_KEY, victim, request);

        vm.deal(victim, 1 ether);
        _submit(request, _emptyActions(), signature);
        vm.deal(victim, 2 ether);
        _submit(request, _emptyActions(), signature);

        assertEq(safe.balance, 3 ether);
    }

    function testTargetCannotReenterRescue() public {
        ReentrantTarget target = new ReentrantTarget();

        _rescue(_singleAction(address(target), 200_000, abi.encodeCall(target.attemptRescue, ())));

        assertEq(target.revertSelector(), ECDSA.ECDSAInvalidSignatureLength.selector);
    }

    // ---------------------------------------------------------------
    // Gas
    // ---------------------------------------------------------------

    function testGasBurningTargetCannotBlockLaterAction() public {
        GasBurningTarget burner = new GasBurningTarget();
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](2);
        actions[0] = _action(address(burner), 30_000, abi.encodeCall(burner.burn, ()));
        actions[1] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));
        EthRescue.RescueRequest memory request = _request(actions);
        bytes memory signature = _sign(VICTIM_KEY, victim, request);

        vm.prank(rescuer);
        delegated.rescue{gas: 500_000}(request, actions, signature);

        assertEq(recorder.calls(), 1);
    }

    function testReturnDataBombCannotBlockLaterAction() public {
        ReturnDataBomb bomb = new ReturnDataBomb();
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](2);
        actions[0] = _action(address(bomb), 1_000_000, abi.encodeCall(bomb.explode, ()));
        actions[1] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));

        _rescue(actions);

        assertEq(recorder.calls(), 1);
    }

    // ---------------------------------------------------------------
    // Actions and sweep
    // ---------------------------------------------------------------

    function testSupportsTokenReceiverInterfaces() public view {
        assertTrue(delegated.supportsInterface(0x01ffc9a7)); // ERC165
        assertTrue(delegated.supportsInterface(0x150b7a02)); // ERC721Receiver
        assertTrue(delegated.supportsInterface(0x4e2312e0)); // ERC1155Receiver
        assertFalse(delegated.supportsInterface(0xffffffff));
    }

    function testDelegatedVictimReceivesSafeTransferredTokens() public {
        address holder = makeAddr("holder");
        FixtureERC721 erc721 = new FixtureERC721();
        FixtureERC1155 erc1155 = new FixtureERC1155();
        erc721.mint(holder, 1);
        erc1155.mint(holder, 1, 3);
        erc1155.mint(holder, 2, 4);
        erc1155.mint(holder, 3, 5);

        vm.prank(holder);
        erc721.safeTransferFrom(holder, victim, 1, "erc721");

        vm.prank(holder);
        erc1155.safeTransferFrom(holder, victim, 1, 3, "erc1155");

        uint256[] memory tokenIds = new uint256[](2);
        tokenIds[0] = 2;
        tokenIds[1] = 3;
        uint256[] memory amounts = new uint256[](2);
        amounts[0] = 4;
        amounts[1] = 5;
        vm.prank(holder);
        erc1155.safeBatchTransferFrom(holder, victim, tokenIds, amounts, "batch");

        assertEq(erc721.ownerOf(1), victim);
        assertEq(erc1155.balanceOf(victim, 1), 3);
        assertEq(erc1155.balanceOf(victim, 2), 4);
        assertEq(erc1155.balanceOf(victim, 3), 5);
    }

    function testActionCallsOriginateFromVictim() public {
        CallRecorder target = new CallRecorder();

        _rescue(_singleAction(address(target), 100_000, abi.encodeCall(target.record, ())));

        assertEq(target.caller(), victim);
        assertEq(target.calls(), 1);
    }

    function testRescueTransfersCommonAssetTypesAndOwnership() public {
        FixtureERC20 erc20 = new FixtureERC20();
        FixtureERC721 erc721 = new FixtureERC721();
        FixtureERC1155 erc1155 = new FixtureERC1155();
        FixtureOwnable ownable = new FixtureOwnable(victim);
        erc20.mint(victim, 1_000);
        erc721.mint(victim, 1);
        erc1155.mint(victim, 1, 25);

        EthRescue.Action[] memory actions = new EthRescue.Action[](4);
        actions[0] = _action(address(erc20), 100_000, abi.encodeCall(erc20.transfer, (safe, 1_000)));
        actions[1] = _action(address(erc721), 100_000, abi.encodeCall(erc721.transferFrom, (victim, safe, 1)));
        actions[2] =
            _action(address(erc1155), 150_000, abi.encodeCall(erc1155.safeTransferFrom, (victim, safe, 1, 25, "")));
        actions[3] = _action(address(ownable), 100_000, abi.encodeCall(ownable.transferOwnership, (safe)));

        _rescue(actions);

        assertEq(erc20.balanceOf(victim), 0);
        assertEq(erc20.balanceOf(safe), 1_000);
        assertEq(erc721.ownerOf(1), safe);
        assertEq(erc1155.balanceOf(victim, 1), 0);
        assertEq(erc1155.balanceOf(safe, 1), 25);
        assertEq(ownable.owner(), safe);
    }

    function testRevertingActionDoesNotStopLaterActions() public {
        RevertingTarget revertingTarget = new RevertingTarget();
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](2);
        actions[0] = _action(address(revertingTarget), 100_000, abi.encodeCall(revertingTarget.fail, ()));
        actions[1] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));

        _rescue(actions);

        assertEq(recorder.calls(), 1);
    }

    function testEmitsActionResultPerAction() public {
        RevertingTarget revertingTarget = new RevertingTarget();
        FalseReturningTarget falseReturning = new FalseReturningTarget();
        NoReturnTarget noReturn = new NoReturnTarget();
        EthRescue.Action[] memory actions = new EthRescue.Action[](3);
        actions[0] = _action(address(revertingTarget), 100_000, abi.encodeCall(revertingTarget.fail, ()));
        actions[1] = _action(address(falseReturning), 100_000, abi.encodeCall(falseReturning.returnFalse, ()));
        actions[2] = _action(address(noReturn), 100_000, abi.encodeCall(noReturn.run, ()));

        vm.expectEmit(true, false, false, true, victim);
        emit EthRescue.ActionResult(0, false);
        vm.expectEmit(true, false, false, true, victim);
        emit EthRescue.ActionResult(1, true); // EVM success only; protocol failure is the client's state check
        vm.expectEmit(true, false, false, true, victim);
        emit EthRescue.ActionResult(2, true);
        _rescue(actions);

        assertEq(noReturn.calls(), 1);
    }

    function testReturnsEachActionResult() public {
        RevertingTarget revertingTarget = new RevertingTarget();
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](3);
        actions[0] = _action(address(revertingTarget), 100_000, abi.encodeCall(revertingTarget.fail, ()));
        actions[1] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));
        actions[2] = _action(makeAddr("eoa"), 100_000, abi.encodeCall(recorder.record, ()));
        EthRescue.RescueRequest memory request = _request(actions);
        bytes memory signature = _sign(VICTIM_KEY, victim, request);

        vm.prank(rescuer);
        bool[] memory results = delegated.rescue(request, actions, signature);

        assertEq(results.length, 3);
        assertFalse(results[0]);
        assertTrue(results[1]);
        assertFalse(results[2]);
    }

    function testCodelessTargetIsReportedAsFailure() public {
        address eoa = makeAddr("eoa");

        vm.expectEmit(true, false, false, true, victim);
        emit EthRescue.ActionResult(0, false);
        _rescue(_singleAction(eoa, 100_000, ""));
    }

    function testInvalidActionShapesRevertBeforeExecution() public {
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](2);
        actions[0] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));
        actions[1] = _action(address(0), 100_000, "");

        vm.expectRevert(abi.encodeWithSelector(EthRescue.InvalidAction.selector, 1));
        _rescue(actions);

        actions[1].target = victim;
        vm.expectRevert(abi.encodeWithSelector(EthRescue.InvalidAction.selector, 1));
        _rescue(actions);

        actions[1].target = address(recorder);
        actions[1].gasStipend = 0;
        vm.expectRevert(abi.encodeWithSelector(EthRescue.InvalidAction.selector, 1));
        _rescue(actions);

        assertEq(recorder.calls(), 0);
    }

    function testVictimAcceptsRefundAndSweepsItAfterActions() public {
        RefundTarget target = new RefundTarget();
        vm.deal(address(target), 2 ether);

        _rescue(_singleAction(address(target), 200_000, abi.encodeCall(target.refund, ())));

        assertEq(target.caller(), victim);
        assertEq(victim.balance, 0);
        assertEq(safe.balance, 2 ether);
    }

    function testDelegatedVictimCanReceivePlainEthBeforeRescue() public {
        address sender = makeAddr("sender");
        vm.deal(sender, 1 ether);

        vm.prank(sender);
        (bool success,) = victim.call{value: 1 ether}("");

        assertTrue(success);
        assertEq(victim.balance, 1 ether);
        _rescue(_emptyActions());
        assertEq(safe.balance, 1 ether);
    }

    function testZeroBalanceDoesNotCallRejectingSafe() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        request.safe = address(new RejectingSafe());

        _submit(request, _emptyActions());
    }

    function testRejectingSafeRevertsPositiveSweep() public {
        EthRescue.RescueRequest memory request = _request(_emptyActions());
        request.safe = address(new RejectingSafe());
        vm.deal(victim, 1 ether);

        vm.expectRevert(EthRescue.EthTransferFailed.selector);
        _submit(request, _emptyActions());

        assertEq(victim.balance, 1 ether);
    }

    function testFuzzSweepsFullBalance(uint96 amount) public {
        vm.deal(victim, amount);

        _rescue(_emptyActions());

        assertEq(victim.balance, 0);
        assertEq(safe.balance, amount);
    }

    function testFuzzExecutesEveryValidAction(uint8 rawCount) public {
        uint256 count = bound(rawCount, 1, 128);
        CallRecorder recorder = new CallRecorder();
        EthRescue.Action[] memory actions = new EthRescue.Action[](count);
        for (uint256 i; i < count; ++i) {
            actions[i] = _action(address(recorder), 100_000, abi.encodeCall(recorder.record, ()));
        }

        _rescue(actions);

        assertEq(recorder.calls(), count);
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    function _rescue(EthRescue.Action[] memory actions) private {
        _submit(_request(actions), actions);
    }

    function _submit(EthRescue.RescueRequest memory request, EthRescue.Action[] memory actions) private {
        _submit(request, actions, _sign(VICTIM_KEY, victim, request));
    }

    function _submit(EthRescue.RescueRequest memory request, EthRescue.Action[] memory actions, bytes memory signature)
        private
    {
        vm.prank(request.rescuer);
        delegated.rescue(request, actions, signature);
    }

    function _request(EthRescue.Action[] memory actions) private view returns (EthRescue.RescueRequest memory) {
        return EthRescue.RescueRequest({
            rescuer: rescuer,
            safe: safe,
            deadline: block.timestamp + 1 hours,
            actionsHash: keccak256(abi.encode(actions))
        });
    }

    /// @dev Independent of the contract so tests can sign without making a call that `expectRevert` would catch.
    function _digest(address account, EthRescue.RescueRequest memory request) private view returns (bytes32) {
        bytes32 domainSeparator = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256("EthRescue"),
                keccak256("1"),
                vm.getChainId(),
                account
            )
        );
        bytes32 structHash = keccak256(
            abi.encode(
                keccak256("RescueRequest(address rescuer,address safe,uint256 deadline,bytes32 actionsHash)"),
                request.rescuer,
                request.safe,
                request.deadline,
                request.actionsHash
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator, structHash));
    }

    function _sign(uint256 key, address account, EthRescue.RescueRequest memory request)
        private
        view
        returns (bytes memory)
    {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, _digest(account, request));
        return abi.encodePacked(r, s, v);
    }

    function _emptyActions() private pure returns (EthRescue.Action[] memory) {
        return new EthRescue.Action[](0);
    }

    function _singleAction(address target, uint256 gasStipend, bytes memory data)
        private
        pure
        returns (EthRescue.Action[] memory actions)
    {
        actions = new EthRescue.Action[](1);
        actions[0] = _action(target, gasStipend, data);
    }

    function _action(address target, uint256 gasStipend, bytes memory data)
        private
        pure
        returns (EthRescue.Action memory)
    {
        return EthRescue.Action({target: target, gasStipend: gasStipend, data: data});
    }
}
