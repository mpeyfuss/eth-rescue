import { getAddress } from "viem";

const DIR = new URL("./fixtures/", import.meta.url).pathname;

// The seller in the recorded Transient fixtures.
export const COMPROMISED = getAddress("0x05658017b00b118d1c06743f5f41b7815f7e9e9b");
export const DESTINATION = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";

// transient-*.json are recorded API responses (profiles scrubbed). alchemy-*.json follow
// Alchemy's documented response shapes; they were written by hand, not recorded.
export function fixture<T = any>(name: string): Promise<T> {
  return Bun.file(`${DIR}${name}.json`).json();
}

export function planWith(actions: unknown[]) {
  return { version: 1, network: "mainnet", compromised: COMPROMISED, destination: DESTINATION, actions };
}
