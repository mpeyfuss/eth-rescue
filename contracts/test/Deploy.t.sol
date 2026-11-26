// SPDX-License-Identifier: MIT
pragma solidity 0.8.38;

import {Test} from "forge-std-1.16.2/Test.sol";
import {Deploy} from "../script/Deploy.s.sol";

contract DeployTest is Test {
    address internal constant CREATEX = 0xba5Ed099633D3B313e4D5F7bdc1305d3c28ba5Ed;

    function testRefusesWhenCreateXIsMissing() public {
        Deploy script = new Deploy();
        vm.expectRevert(Deploy.CreateXMissing.selector);
        script.run();
    }

    function testRefusesAnythingButTheCanonicalCreateX() public {
        bytes memory impostor = hex"6000";
        vm.etch(CREATEX, impostor);
        Deploy script = new Deploy();
        vm.expectRevert(abi.encodeWithSelector(Deploy.UnexpectedCreateX.selector, keccak256(impostor)));
        script.run();
    }
}
