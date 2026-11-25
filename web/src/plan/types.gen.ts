// Generated from schema/plan.v1.schema.json by `bun run gen:types`. Do not edit.

export type JsonValue = unknown;

export interface Plan {
  version: 1;
  network: "mainnet" | "sepolia";
  victim: string;
  safe: string;
  gas_wallet?: string | null;
  options?: Options;
  /**
   * @minItems 1
   */
  actions: [
    Erc20Action | Erc721Action | Erc1155Action | OwnershipAction | TransientDelistErc721Action | CustomAction,
    ...(Erc20Action | Erc721Action | Erc1155Action | OwnershipAction | TransientDelistErc721Action | CustomAction)[]
  ];
}
export interface Options {
  priority_fee_gwei?: string;
  max_blocks?: number;
  legacy_fallback?: boolean;
}
export interface Erc20Action {
  label?: string | null;
  gas?: number | null;
  type: "erc20";
  contract: string;
  amount: string;
}
export interface Erc721Action {
  label?: string | null;
  gas?: number | null;
  type: "erc721";
  contract: string;
  token_id: string;
}
export interface Erc1155Action {
  label?: string | null;
  gas?: number | null;
  type: "erc1155";
  contract: string;
  token_id: string;
  amount: string;
}
export interface OwnershipAction {
  label?: string | null;
  gas?: number | null;
  type: "ownership";
  contract: string;
}
export interface TransientDelistErc721Action {
  label?: string | null;
  type: "transient_delist_erc721";
  auction_house: string;
  contract: string;
  token_id: string;
}
export interface CustomAction {
  label?: string | null;
  type: "custom";
  target: string;
  signature: string;
  args: JsonValue[];
  gas: number;
}
