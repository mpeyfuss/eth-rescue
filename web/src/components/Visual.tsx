import {
  Check,
  CircleHelp,
  Code,
  Coins,
  Copy,
  ExternalLink,
  Gem,
  Image,
  KeyRound,
  Layers,
  Smile,
  Tag,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import type { ActionType, Network } from "../plan/draft";
import { Dialog } from "./Dialog";
import { addressColor, explorerAddressUrl, explorerTokenUrl, shortAddress } from "../plan/format";

export function AddressBadge({
  address,
  network = "mainnet",
  tokenId,
  link = false,
  copyable = false,
}: {
  address: string;
  network?: Network;
  tokenId?: string;
  link?: boolean;
  copyable?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const trimmed = address.trim();
  if (!trimmed) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(trimmed);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  const href = tokenId ? explorerTokenUrl(network, trimmed, tokenId) : explorerAddressUrl(network, trimmed);

  return (
    <span className="addr-pill" title={trimmed}>
      <span className="addr-dot" style={{ background: addressColor(trimmed) }} aria-hidden="true" />
      <span className="mono">{shortAddress(trimmed)}</span>
      {copyable && (
        <button
          type="button"
          className="addr-action"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            copy();
          }}
          aria-label={`Copy ${trimmed}`}
          title="Copy full address"
        >
          {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
        </button>
      )}
      {link && (
        <a
          className="addr-action"
          href={href}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
          aria-label={`Verify ${shortAddress(trimmed)} on Etherscan`}
          title="Verify on Etherscan"
        >
          <ExternalLink size={13} aria-hidden="true" />
        </a>
      )}
    </span>
  );
}

const TYPE_META: Record<ActionType, { short: string; tone: string }> = {
  erc20: { short: "ERC-20", tone: "blue" },
  erc721: { short: "ERC-721", tone: "violet" },
  erc1155: { short: "ERC-1155", tone: "indigo" },
  ownership: { short: "Ownership", tone: "emerald" },
  transient_delist_erc721: { short: "Delist + Transfer", tone: "amber" },
  cryptopunk: { short: "CryptoPunk", tone: "violet" },
  superrare_v1: { short: "SuperRare V1", tone: "violet" },
  custom: { short: "Custom call", tone: "orange" },
};

const ACTION_ICONS = {
  erc20: Coins,
  erc721: Image,
  erc1155: Layers,
  ownership: KeyRound,
  transient_delist_erc721: Tag,
  cryptopunk: Smile,
  superrare_v1: Gem,
  custom: Code,
} satisfies Record<ActionType, unknown>;

export function ActionIcon({ type }: { type: ActionType }) {
  const Icon = ACTION_ICONS[type];
  return <Icon size={16} strokeWidth={1.75} aria-hidden="true" />;
}

export function TypeBadge({ type }: { type: ActionType }) {
  const meta = TYPE_META[type];
  return (
    <span className={`type-badge tone-${meta.tone}`}>
      <ActionIcon type={type} />
      <span>{meta.short}</span>
    </span>
  );
}

export function WhyTip({ label = "Why?", children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="why-btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <CircleHelp size={14} aria-hidden="true" />
        {label}
      </button>
      <Dialog open={open} title={label} narrow onClose={() => setOpen(false)}>
        <div className="why-body">{children}</div>
      </Dialog>
    </>
  );
}
