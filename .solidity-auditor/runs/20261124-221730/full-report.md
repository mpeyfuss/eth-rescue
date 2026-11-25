# 🔐 Security Review — eth-rescue

---

## Scope

|  |  |
| --- | --- |
| **Mode** | filename |
| **Files reviewed** | `contracts/src/EthRescue.sol` |
| **Confidence threshold (1-100)** | 75 |

---

## Findings

[75] **1. A 1 wei deposit makes every rescue revert when SAFE rejects ETH**

`EthRescue._sweepEth` · Confidence: 75

**Description**
Any sender can add 1 wei to the victim account, so _sweepEth reverts every rescue when SAFE rejects ETH, and the attacker keeps the assets.

**Fix**

```diff
     function _sweepEth() private {
         uint256 amount = address(this).balance;
         if (amount != 0) {
             (bool success,) = SAFE.call{value: amount}("");
-            if (!success) revert EthTransferFailed();
+            success; // a failed ETH sweep must not undo the token actions
         }
     }
```

---

[75] **2. Rescue skips an action with no revert when the outer gas is short**

`EthRescue.rescue` · Confidence: 75

**Description**
The rescue function checks no gas reserve, so a short gas limit skips an action with no revert or reverts the batch, and the attacker keeps the asset.

**Fix (Option A — validate gas before the call)**:

```diff
+    error InsufficientGas(uint256 index);
 ...
             if (action.target == address(0) || action.target == address(this) || action.gasStipend == 0) {
                 revert InvalidAction(i);
             }
 
+            if (gasleft() < action.gasStipend * 64 / 63 + 5_000) revert InsufficientGas(i);
             (bool success,,) = LibCall.tryCall(action.target, 0, action.gasStipend, 0, action.data);
             if (!success && action.required) revert RequiredActionFailed(i);
```

**Fix (Option B — detect a capped call after it fails)**:

```diff
             (bool success,,) = LibCall.tryCall(action.target, 0, action.gasStipend, 0, action.data);
-            if (!success && action.required) revert RequiredActionFailed(i);
+            if (!success) {
+                if (action.required) revert RequiredActionFailed(i);
+                if (gasleft() <= action.gasStipend / 63) revert InsufficientGas(i);
+            }
```

---

Findings List

| # | Confidence | Title |
|---|---|---|
| 1 | [75] | A 1 wei deposit makes every rescue revert when SAFE rejects ETH |
| 2 | [75] | Rescue skips an action with no revert when the outer gas is short |

---

## Leads

_Vulnerability trails with concrete code smells where the full exploit path could not be completed in one analysis pass. These are not false positives — they are high-signal leads for manual review. Not scored._

- **A shared deployment gives its RESCUER control of every delegated account** — `EthRescue.constructor` — Code smells: `RESCUER` and `SAFE` are constructor immutables; contracts/README.md lists shared verified deployments — A user who delegates to a listed deployment lets that deployment's RESCUER make calls from the user account. We did not check the RESCUER of the listed deployments.
- **The delegated account rejects callbacks that a plain EOA accepts** — `EthRescue.receive` — Code smells: no `fallback`; the delegation stays after the rescue; ERC-777 and calls with value and calldata revert — A call with an unknown selector to the delegated account reverts. We did not find a rescue target that sends such a callback.
- **Actions cannot send ETH, so assets that need a fee cannot be rescued** — `EthRescue.rescue` — Code smells: `tryCall` always gets `value = 0`; `rescue` is not payable; `Action` has no value field — The rescuer cannot recover an asset that needs an ETH fee, such as a bridge-out or a paid claim. We did not find such an asset in the current templates.
- **The attacker can make the 7702 authorization invalid before the rescue** — `EthRescue.rescue` — Code smells: EIP-7702 skips an authorization with a wrong nonce and does not revert; the attacker holds the victim key — The attacker can change the victim nonce or delegation first, so the rescue call does nothing or runs the attacker code. The contract cannot detect this, and we did not confirm a loss beyond gas.
- **The required flag accepts a codeless target or a false return** — `EthRescue.rescue` — Code smells: `maxCopy` is 0 so the return data is never read; no `action.target.code.length` check; `required` tests only the CALL success flag — A required action to an address with no code, or an ERC-20 transfer that returns false, counts as a success. The README documents this and the client sets `required` to false, so we found no current loss.

---

> ⚠️ This review was performed by an AI assistant. AI analysis can never verify the complete absence of vulnerabilities and no guarantee of security is given. Team security reviews, bug bounty programs, and on-chain monitoring are strongly recommended. For a consultation regarding your projects' security, visit [https://www.pashov.com](https://www.pashov.com)
