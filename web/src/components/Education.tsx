import { Check, Globe, ShieldCheck, SquareTerminal, X } from "lucide-react";
import { useState } from "react";
import { Dialog } from "./Dialog";

export function HowItWorksVisual({ compact = false }: { compact?: boolean }) {
  return (
    <section className={compact ? "edu-section compact" : "edu-section"} aria-label="How ETH Rescue works">
      <div className="edu-header">
        <h2>How it works</h2>
        <p className="muted">
          If you send ETH to a compromised wallet to pay for gas, a bot watching the wallet takes it before you can use
          it. ETH Rescue never sends ETH there. A separate wallet pays the fees, and the rescue goes to block builders
          privately, so the bot never sees it coming.
        </p>
      </div>

      <div className="compare-grid">
        <div className="compare-card bad">
          <div className="compare-head">
            <span className="compare-badge bad">
              <X size={14} aria-hidden="true" /> Normal transfer
            </span>
            <span className="compare-sub">Why gas gets stolen</span>
          </div>
          <div className="flow-steps">
            <div className="flow-step">
              <span className="flow-num">1</span>
              <div>
                <strong>You send ETH for gas</strong>
                <span>Broadcast publicly to Ethereum&apos;s mempool</span>
              </div>
            </div>
            <div className="flow-connector" aria-hidden="true">
              ↓
            </div>
            <div className="flow-step">
              <span className="flow-num">2</span>
              <div>
                <strong>Sweeper bot spots it</strong>
                <span>It moves the ETH out within seconds</span>
              </div>
            </div>
            <div className="flow-connector" aria-hidden="true">
              ↓
            </div>
            <div className="flow-step fail">
              <span className="flow-num">
                <X size={14} aria-label="Fails" />
              </span>
              <div>
                <strong>Gas ETH drained</strong>
                <span>Your tokens and NFTs are still stuck</span>
              </div>
            </div>
          </div>
        </div>

        <div className="compare-card good">
          <div className="compare-head">
            <span className="compare-badge good">
              <Check size={14} aria-hidden="true" /> ETH Rescue
            </span>
            <span className="compare-sub">EIP-7702, sent privately through Flashbots</span>
          </div>
          <div className="flow-steps">
            <div className="flow-step">
              <span className="flow-num">1</span>
              <div>
                <strong>Gas wallet sponsors the rescue</strong>
                <span>No ETH ever sits in the compromised wallet</span>
              </div>
            </div>
            <div className="flow-connector" aria-hidden="true">
              ↓
            </div>
            <div className="flow-step">
              <span className="flow-num">2</span>
              <div>
                <strong>Sent privately to Flashbots</strong>
                <span>Bots watching the public mempool can&apos;t see it</span>
              </div>
            </div>
            <div className="flow-connector" aria-hidden="true">
              ↓
            </div>
            <div className="flow-step pass">
              <span className="flow-num">
                <Check size={14} aria-label="Succeeds" />
              </span>
              <div>
                <strong>Assets and ETH reach your destination</strong>
                <span>Everything moves in the same block</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="trust-boundary">
        <div className="edu-subhead">
          <h3>Where your keys go</h3>
          <p className="muted">This website only sees public addresses. Keys are typed into the rescue tool on your computer.</p>
        </div>
        <div className="zone-grid">
          <div className="zone-card">
            <div className="zone-icon" aria-hidden="true">
              <Globe size={18} />
            </div>
            <div className="zone-body">
              <div className="zone-title">
                <strong>This website</strong>
                <span className="zone-pill">Public addresses only</span>
              </div>
              <p>
                Looks up balances and writes <code>plan.json</code>. Refuses anything that looks like a private key or
                seed phrase.
              </p>
            </div>
          </div>

          <div className="zone-card">
            <div className="zone-icon" aria-hidden="true">
              <SquareTerminal size={18} />
            </div>
            <div className="zone-body">
              <div className="zone-title">
                <strong>Your computer</strong>
                <span className="zone-pill">Compromised and gas wallet keys</span>
              </div>
              <p>
                The open-source eth-rescue tool asks for the keys in a hidden prompt, signs on your computer, simulates the
                rescue, and asks you once before sending.
              </p>
            </div>
          </div>

          <div className="zone-card destination">
            <div className="zone-icon" aria-hidden="true">
              <ShieldCheck size={18} />
            </div>
            <div className="zone-body">
              <div className="zone-title">
                <strong>Your destination wallet</strong>
                <span className="zone-pill ok">Key never needed</span>
              </div>
              <p>
                Keep it on a hardware wallet, a new app wallet, or a Safe multisig. Only its public <code>0x…</code>{" "}
                receiving address is ever used.
              </p>
            </div>
          </div>
        </div>
      </div>

      {!compact && (
        <div className="capability-strip">
          <div className="edu-subhead">
            <h3>What a rescue plan can move</h3>
          </div>
          <div className="capability-grid">
            <div className="cap-card">
              <strong>ERC-20 tokens</strong>
              <span>Auto-detects balances &amp; decimals</span>
            </div>
            <div className="cap-card">
              <strong>ERC-721 &amp; ERC-1155 NFTs</strong>
              <span>Transfers tokens directly to destination</span>
            </div>
            <div className="cap-card">
              <strong>Contract ownership</strong>
              <span>Calls transferOwnership on Ownable contracts</span>
            </div>
            <div className="cap-card">
              <strong>Transient listings</strong>
              <span>Delists ERC-721 &amp; rescues in one step</span>
            </div>
            <div className="cap-card">
              <strong>Custom contract calls</strong>
              <span>Any ABI function (CryptoPunks, staking, etc.)</span>
            </div>
            <div className="cap-card">
              <strong>Leftover ETH sweep</strong>
              <span>Always swept automatically at the end</span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

const PREP_ITEMS = [
  {
    id: "device",
    title: "Pick a clean fallback computer",
    desc: "If malware is on your main computer, typing your gas wallet key there hands the attacker that ETH too. A spare laptop or a trusted person's computer works.",
  },
  {
    id: "destination",
    title: "Create your destination wallet off-computer",
    desc: "Use a hardware wallet, a new app wallet with its own new seed phrase, or a Safe multisig set up on Ethereum mainnet. Only its public 0x… address ever touches a computer.",
  },
  {
    id: "backups",
    title: "Keep backups you can reach without your main computer",
    desc: "Write down the gas wallet key (paper or phone) and save plan.json on a USB drive, cloud drive, or in an email to yourself. plan.json holds no secrets.",
  },
] as const;

export function BreakGlassCards() {
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const doneCount = Object.values(checked).filter(Boolean).length;

  return (
    <div className="prep-guide">
      <div className="prep-header">
        <div>
          <h3>Assume your main computer is what gets compromised</h3>
          <p className="muted">Do these three things now, while there&apos;s no rush.</p>
        </div>
        <span className="prep-counter">
          {doneCount}/{PREP_ITEMS.length} ready
        </span>
      </div>
      <div className="prep-cards">
        {PREP_ITEMS.map((item) => {
          const isDone = Boolean(checked[item.id]);
          return (
            <label key={item.id} className={isDone ? "prep-card done" : "prep-card"}>
              <div className="prep-card-top">
                <input
                  type="checkbox"
                  checked={isDone}
                  onChange={(e) => setChecked((prev) => ({ ...prev, [item.id]: e.target.checked }))}
                />
              </div>
              <strong>{item.title}</strong>
              <p>{item.desc}</p>
            </label>
          );
        })}
      </div>
    </div>
  );
}

const DESTINATION_CHOICES = [
  { title: "Hardware wallet", desc: "The best choice. Its key never touches a computer." },
  {
    title: "New app wallet",
    desc: "A MetaMask, Rabby, or similar wallet with a brand-new seed phrase, created on a clean device.",
  },
  {
    title: "Safe multisig",
    desc: "Works if it is already set up on Ethereum mainnet. You'll need its signers to move assets out later.",
  },
  {
    title: "Never use",
    desc: "Another account from the leaked seed phrase, an exchange deposit address, or a Safe that only exists on another network.",
    avoid: true,
  },
];

export function DestinationGuide() {
  return (
    <div className="capability-strip">
      <div className="edu-subhead">
        <h3>Choosing a destination wallet</h3>
        <p className="muted">It receives everything rescued, so pick one the attacker has never had access to.</p>
      </div>
      <div className="capability-grid two-col">
        {DESTINATION_CHOICES.map((choice) => (
          <div key={choice.title} className={choice.avoid ? "cap-card avoid" : "cap-card"}>
            <strong>{choice.title}</strong>
            <span>{choice.desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const RUN_PHASES = [
  {
    num: "1",
    title: "Hidden key prompt",
    desc: "Asks for the compromised and gas wallet keys. Nothing shows as you paste. Paste once and press Enter.",
  },
  {
    num: "2",
    title: "Gas balance check",
    desc: "Shows how much ETH the gas wallet needs and waits until it has it.",
  },
  {
    num: "3",
    title: "Simulation",
    desc: "Tests the exact signed rescue privately before anything is sent.",
  },
  {
    num: "4",
    title: "Confirmation",
    desc: "Lists every wallet and action, then asks you once before sending.",
  },
  {
    num: "5",
    title: "Send and retry",
    desc: "Sends the rescue privately. Anything that fails is retried as a direct transaction.",
  },
];

export function RunTimeline() {
  return (
    <div className="run-timeline" aria-label="What happens when you run eth-rescue">
      <div className="timeline-label">What happens when you run the rescue</div>
      <ol className="timeline-grid">
        {RUN_PHASES.map((phase) => (
          <li key={phase.num} className="timeline-step">
            <span className="timeline-num">{phase.num}</span>
            <strong>{phase.title}</strong>
            <span>{phase.desc}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function EducationModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} title="How ETH Rescue works" closeLabel="Close guide" onClose={onClose}>
      <HowItWorksVisual />
      <DestinationGuide />
      <RunTimeline />
    </Dialog>
  );
}
