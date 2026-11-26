import { expect, test } from "bun:test";
import { cryptoPunks } from "../src/discovery/collections/cryptopunks";
import { superRareV1 } from "../src/discovery/collections/superrareV1";
import type { PublicClient } from "../src/discovery/onchain";
import { COMPROMISED } from "./discovery-fixtures";

const OTHER = "0x000000000000000000000000000000000000dEaD";
const request = { network: "mainnet" as const, compromised: COMPROMISED, alchemyKey: null };

function fakeClient(reads: Record<string, unknown>, owners: string[] = []) {
  const calls: { functionName: string; count?: number }[] = [];
  const client = {
    readContract: async ({ functionName }: { functionName: string }) => {
      calls.push({ functionName });
      return reads[functionName];
    },
    multicall: async ({ contracts }: { contracts: unknown[] }) => {
      calls.push({ functionName: "multicall", count: contracts.length });
      return contracts.map((_, index) => owners[index] ?? OTHER);
    },
  };
  return { client: client as unknown as PublicClient, calls };
}

test("cryptopunks finds every punk the wallet holds", async () => {
  const owners: string[] = [];
  owners[3] = COMPROMISED.toLowerCase();
  owners[9999] = COMPROMISED;
  const { client, calls } = fakeClient({ balanceOf: 2n }, owners);
  const warnings: string[] = [];

  const candidates = await cryptoPunks.scan(request, client, (text) => warnings.push(text));

  expect(candidates.map((c) => c.action)).toEqual([
    { type: "cryptopunk", token_id: "3", label: "CryptoPunk #3" },
    { type: "cryptopunk", token_id: "9999", label: "CryptoPunk #9999" },
  ]);
  expect(calls).toEqual([{ functionName: "balanceOf" }, { functionName: "multicall", count: 10_000 }]);
  expect(warnings).toEqual([]);
});

test("cryptopunks skips the full lookup when the wallet holds none", async () => {
  const { client, calls } = fakeClient({ balanceOf: 0n });
  expect(await cryptoPunks.scan(request, client, () => {})).toEqual([]);
  expect(calls).toEqual([{ functionName: "balanceOf" }]);
});

test("cryptopunks warns when the lookup finds fewer punks than the balance", async () => {
  const owners: string[] = [];
  owners[7] = COMPROMISED;
  const { client } = fakeClient({ balanceOf: 2n }, owners);
  const warnings: string[] = [];
  await cryptoPunks.scan(request, client, (text) => warnings.push(text));
  expect(warnings).toEqual(["the contract reports 2 punks but 1 were found"]);
});

test("superrare v1 lists the wallet's tokens from tokensOf", async () => {
  const { client } = fakeClient({ tokensOf: [1n, 43n] });
  const candidates = await superRareV1.scan(request, client, () => {});
  expect(candidates.map((c) => c.action)).toEqual([
    { type: "superrare_v1", token_id: "1", label: "SuperRare V1 #1" },
    { type: "superrare_v1", token_id: "43", label: "SuperRare V1 #43" },
  ]);
  expect(candidates.every((c) => c.preselect && c.group === "nfts")).toBe(true);
});
