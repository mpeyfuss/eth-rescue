import { ArrowRight, Check, Fuel, MonitorX, ShieldCheck, TriangleAlert } from "lucide-react";
import type { Mode } from "../App";
import type { WalletSnapshot } from "../discovery/onchain";
import { COMMANDS } from "../guide";
import { addressProblem, sameAddress } from "../plan/address";
import { type Draft, type DraftEvent, type DraftField, type Network, NETWORKS } from "../plan/draft";
import { CommandLine } from "./Command";
import { TextField } from "./Field";
import { AddressBadge, WhyTip } from "./Visual";

export interface WalletSnapshots {
  compromised?: WalletSnapshot | "loading";
  destination?: WalletSnapshot | "loading";
  gas_wallet?: WalletSnapshot | "loading";
}

interface Props {
  mode: Mode;
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  errorFor: (path: string, value: string) => string | undefined;
  snapshots: WalletSnapshots;
  onBack?: () => void;
  onContinue?: () => void;
}

const GAS_READY_WEI = 15_000_000_000_000_000n; // 0.015 ETH

export function SetupSection({
  mode,
  draft,
  dispatch,
  errorFor,
  snapshots,
  onBack,
  onContinue,
}: Props) {
  const set = (field: DraftField) => (value: string | boolean) => dispatch({ kind: "set", field, value });
  const ethRescueAddr = NETWORKS.find((n) => n.value === draft.network)?.ethRescue ?? "";

  const compromisedValid = Boolean(draft.compromised.trim()) && !addressProblem(draft.compromised.trim());
  const destinationValid = Boolean(draft.destination.trim()) && !addressProblem(draft.destination.trim());
  const gasValid = Boolean(draft.gas_wallet.trim()) && !addressProblem(draft.gas_wallet.trim());
  const walletsReady = compromisedValid && destinationValid && !errorFor("compromised", draft.compromised) && !errorFor("destination", draft.destination);

  return (
    <section className="card">
      <h2>{mode === "prepare" ? "2. Wallets" : "1. Wallets"}</h2>

      {mode === "emergency" && (
        <div className="clean-check" role="note">
          <MonitorX className="clean-check-icon" size={22} aria-hidden="true" />
          <div>
            <strong>Assume this computer is compromised too.</strong>
            <p>
              Whatever took your key may still be on the machine you used, reading what you type and copy. On a
              different computer or phone you trust:
            </p>
            <ul>
              <li>Create the destination wallet with a new seed phrase. Never type it on the old machine.</li>
              <li>Run the rescue.</li>
            </ul>
            <p className="clean-check-aside">This page only needs public addresses, so building the plan here is fine.</p>
          </div>
        </div>
      )}

      <LiveWalletDiagram
        network={draft.network}
        compromised={compromisedValid ? draft.compromised.trim() : ""}
        destination={destinationValid ? draft.destination.trim() : ""}
        gasWallet={gasValid ? draft.gas_wallet.trim() : ""}
        snapshots={snapshots}
        sameCompromisedDestination={compromisedValid && destinationValid && sameAddress(draft.compromised, draft.destination)}
      />

      {NETWORKS.length > 1 && (
        <div className="field">
          <label htmlFor="network">Network</label>
          <select id="network" value={draft.network} onChange={(event) => set("network")(event.target.value as Network)}>
            {NETWORKS.map((network) => (
              <option key={network.value} value={network.value}>
                {network.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="wallet-field-block">
        <TextField
          label="Compromised wallet"
          value={draft.compromised}
          onChange={set("compromised")}
          error={errorFor("compromised", draft.compromised)}
          placeholder="0x…"
          mono
        />
        <WalletPreflightStatus role="compromised" snapshot={snapshots.compromised} ethRescueAddr={ethRescueAddr} />
      </div>

      <div className="wallet-field-block">
        <div className="field-label-aux">
          <WhyTip label="Using a Safe or smart wallet?">
            <p>That works if all three are true:</p>
            <ul>
              <li>
                It is already set up on Ethereum mainnet. A Safe created only on Base, Arbitrum, or another network
                does not exist here yet.
              </li>
              <li>It accepts ERC-1155 tokens. Safe does by default.</li>
              <li>You can reach its signers to move anything out afterwards.</li>
            </ul>
            <p>A regular wallet is the simplest choice.</p>
          </WhyTip>
        </div>
        <TextField
          label="Destination wallet"
          value={draft.destination}
          onChange={set("destination")}
          error={errorFor("destination", draft.destination)}
          hint="A wallet the attacker has never had access to, such as a hardware wallet or a new account."
          placeholder="0x…"
          mono
        />
        <WalletPreflightStatus role="destination" snapshot={snapshots.destination} ethRescueAddr={ethRescueAddr} />
      </div>

      <div className="wallet-field-block">
        <div className="field-label-aux">
          <WhyTip label="Why a separate gas wallet?">
            Sweeper bots watch compromised wallets and steal ETH sent there before you can spend it. A separate gas
            wallet sponsors the private Flashbots bundle so ETH never has to sit in the compromised wallet.
          </WhyTip>
        </div>
        <TextField
          label="Gas wallet (optional)"
          value={draft.gas_wallet}
          onChange={set("gas_wallet")}
          error={errorFor("gas_wallet", draft.gas_wallet)}
          hint="Pays the rescue fees. Leave blank to pick it when you run the rescue."
          placeholder="0x…"
          mono
        />
        <WalletPreflightStatus
          role="gas_wallet"
          snapshot={snapshots.gas_wallet}
          ethRescueAddr={ethRescueAddr}
          showBalance={mode === "prepare"}
        />
        <details className="inline-details">
          <summary>Need a fresh gas wallet?</summary>
          <p className="muted">
            Run this in your terminal. It prints a new private key once, so save it somewhere safe:
          </p>
          <CommandLine command={COMMANDS.gasWallet} />
        </details>
      </div>


      {(onBack || onContinue) && (
        <div className="step-footer">
          <div>
            {onBack && (
              <button type="button" onClick={onBack}>
                ← Back
              </button>
            )}
          </div>
          {onContinue && (
            <button type="button" className={walletsReady ? "primary" : undefined} onClick={onContinue}>
              Next: Rescue plan →
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function WalletPreflightStatus({
  role,
  snapshot,
  ethRescueAddr,
  showBalance = true,
}: {
  role: "compromised" | "destination" | "gas_wallet";
  snapshot: WalletSnapshot | "loading" | undefined;
  ethRescueAddr: string;
  // During a rescue the CLI asks for the exact funding, so a balance note here only adds noise.
  showBalance?: boolean;
}) {
  if (!snapshot) return null;
  if (role === "gas_wallet" && !showBalance && (snapshot === "loading" || snapshot.kind !== "contract")) return null;
  if (snapshot === "loading") {
    return <p className="onchain-pill loading">Checking on-chain status…</p>;
  }

  if (role === "compromised") {
    const unknownDelegate =
      snapshot.kind === "delegated" &&
      snapshot.delegateTo &&
      (!ethRescueAddr || !sameAddress(snapshot.delegateTo, ethRescueAddr));
    return (
      <div className="onchain-status-group">
        <p className="onchain-pill">
          <strong>On-chain:</strong> {snapshot.ethFormatted}
          {snapshot.ethBalance > 0n ? " (will be swept to destination automatically)" : ""}
        </p>
        {unknownDelegate && (
          <p className="onchain-pill warn" role="status">
            <strong>This wallet is delegated</strong> to <code>{snapshot.delegateTo}</code>, probably by a sweeper bot.
            The rescue replaces that delegation.
          </p>
        )}
        {snapshot.kind === "contract" && (
          <p className="onchain-pill danger" role="alert">
            This address is a smart contract. ETH Rescue only rescues regular wallets with a private key or seed phrase
            (like MetaMask or Ledger), not multisigs or smart contract wallets.
          </p>
        )}
      </div>
    );
  }

  if (role === "destination") {
    return <DestinationStatus snapshot={snapshot} />;
  }

  // gas_wallet
  const funded = snapshot.ethBalance >= GAS_READY_WEI;
  return (
    <div className="onchain-status-group">
      {snapshot.kind === "contract" && (
        <p className="onchain-pill danger" role="alert">
          Contract code detected: the gas wallet must be a plain EOA account with no code.
        </p>
      )}
      {showBalance && (
        <p className={funded ? "onchain-pill ok" : "onchain-pill warn"}>
          <strong>On-chain:</strong> {snapshot.ethFormatted}
          {funded
            ? ", likely enough. The rescue tool checks the exact amount."
            : ". Send it about 0.02–0.05 ETH. The rescue tool tells you the exact amount when you run it."}
        </p>
      )}
    </div>
  );
}

function DestinationStatus({ snapshot }: { snapshot: WalletSnapshot }) {
  if (snapshot.kind === "contract") {
    return (
      <div className="onchain-status-group">
        <p className="onchain-pill ok">
          <strong>On-chain:</strong> Smart contract wallet (e.g. a Safe) · {snapshot.ethFormatted}
        </p>
        <p className="onchain-pill warn" role="status">
          Safe multisigs work. For any other smart wallet, confirm it can receive ERC-1155 tokens and that you can move
          NFTs out of it.
        </p>
      </div>
    );
  }

  if (snapshot.kind === "delegated") {
    return (
      <p className="onchain-pill ok">
        <strong>On-chain:</strong> Smart account (EIP-7702) · {snapshot.ethFormatted}
      </p>
    );
  }

  if (snapshot.ethBalance === 0n && snapshot.nonce === 0) {
    return (
      <p className="onchain-pill warn" role="status">
        <strong>New address:</strong> no activity on Ethereum mainnet yet. That&apos;s expected for a new hardware or
        app wallet. If this is a Safe or other smart wallet, it isn&apos;t set up on Ethereum mainnet yet. Don&apos;t
        use it until it is, or the rescued assets could get stuck.
      </p>
    );
  }

  return (
    <p className="onchain-pill ok">
      <strong>On-chain:</strong> Regular wallet · {snapshot.ethFormatted}
    </p>
  );
}

function LiveWalletDiagram({
  network,
  compromised,
  destination,
  gasWallet,
  snapshots,
  sameCompromisedDestination,
}: {
  network: Network;
  compromised: string;
  destination: string;
  gasWallet: string;
  snapshots: WalletSnapshots;
  sameCompromisedDestination: boolean;
}) {
  const gasSnap = snapshots.gas_wallet && snapshots.gas_wallet !== "loading" ? snapshots.gas_wallet : null;
  const compromisedSnap = snapshots.compromised && snapshots.compromised !== "loading" ? snapshots.compromised : null;
  const destinationSnap = snapshots.destination && snapshots.destination !== "loading" ? snapshots.destination : null;

  return (
    <div className="wallet-guide" aria-label="How the three wallets work and current status">
      <div className="wallet-flow">
        <div className={`wallet-node ${gasWallet ? "filled" : ""}`}>
          <div>
            <div className="wallet-node-header">
              <Fuel size={18} strokeWidth={1.75} aria-hidden="true" />
              <strong>Gas wallet</strong>
              {gasWallet && <span className="node-check" aria-label="Set"><Check size={13} aria-hidden="true" /></span>}
            </div>
            {gasWallet ? (
              <div className="node-live">
                <AddressBadge address={gasWallet} network={network} link />
                {gasSnap && <span className="node-balance">{gasSnap.ethFormatted}</span>}
              </div>
            ) : (
              <p>Pays fees so ETH never sits in the compromised wallet.</p>
            )}
          </div>
          <span className="wallet-badge">Key typed in the terminal</span>
        </div>

        <div className="wallet-arrow" aria-hidden="true">
          <span>Pays fees</span>
          <ArrowRight size={20} aria-hidden="true" />
        </div>

        <div className={`wallet-node compromised ${compromised ? "filled" : ""}`}>
          <div>
            <div className="wallet-node-header">
              <TriangleAlert size={18} strokeWidth={1.75} aria-hidden="true" />
              <strong>Compromised</strong>
              {compromised && <span className="node-check" aria-label="Set"><Check size={13} aria-hidden="true" /></span>}
            </div>
            {compromised ? (
              <div className="node-live">
                <AddressBadge address={compromised} network={network} link />
                {compromisedSnap && <span className="node-balance">{compromisedSnap.ethFormatted} to sweep</span>}
              </div>
            ) : (
              <p>Signs permission to move its assets and ETH out.</p>
            )}
          </div>
          <span className="wallet-badge">Key typed in the terminal</span>
        </div>

        <div className="wallet-arrow" aria-hidden="true">
          <span>Moves assets</span>
          <ArrowRight size={20} aria-hidden="true" />
        </div>

        <div className={`wallet-node destination ${destination && !sameCompromisedDestination ? "filled" : ""} ${sameCompromisedDestination ? "node-error" : ""}`}>
          <div>
            <div className="wallet-node-header">
              <ShieldCheck size={18} strokeWidth={1.75} aria-hidden="true" />
              <strong>Destination</strong>
              {destination && !sameCompromisedDestination && <span className="node-check ok" aria-label="Set"><Check size={13} aria-hidden="true" /></span>}
            </div>
            {destination ? (
              <div className="node-live">
                <AddressBadge address={destination} network={network} link />
                {sameCompromisedDestination ? (
                  <span className="node-balance err">Must differ from compromised</span>
                ) : (
                  destinationSnap && <span className="node-balance">{destinationSnap.ethFormatted}</span>
                )}
              </div>
            ) : (
              <p>A wallet the attacker can&apos;t touch. Receives everything.</p>
            )}
          </div>
          <span className="wallet-badge ok">Key never needed</span>
        </div>
      </div>
    </div>
  );
}
