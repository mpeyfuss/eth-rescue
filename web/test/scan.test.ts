import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { scanWallet as scan } from "../src/discovery/scan";
import type { CollectionScanner } from "../src/discovery/collections";
import { collectionCandidate } from "../src/discovery/collections/candidate";
import type { ScanRequest } from "../src/discovery/types";
import { fixture, COMPROMISED } from "./discovery-fixtures";

const owners = mock(async (_network: string, _key: string | null, contracts: string[]) =>
  new Map(contracts.map((contract) => [contract.toLowerCase(), COMPROMISED.toLowerCase()])),
);
const scanWallet = (request: ScanRequest) => scan(request, owners, []);

type Route = (url: URL, body: unknown) => unknown;
const realFetch = globalThis.fetch;
let requests: string[] = [];

function serve(route: Route) {
  globalThis.fetch = mock(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input));
    requests.push(`${url.host}${url.pathname}`);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const result = route(url, body);
    if (result instanceof Response) return result;
    return Response.json(result);
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  requests = [];
  owners.mockClear();
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

const transient = async () => ({
  users: await fixture("transient-users"),
  auctions: await fixture("transient-auctions"),
  contracts: await fixture("transient-contracts"),
});

function transientRoute(data: Awaited<ReturnType<typeof transient>>): Route {
  return (url) => {
    const name = url.pathname.split("/").pop() as keyof typeof data;
    return data[name];
  };
}

test("without an Alchemy key only Transient is queried", async () => {
  serve(transientRoute(await transient()));
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null });
  expect(result.errors).toEqual([]);
  expect(result.candidates.map((candidate) => candidate.group)).toEqual(["listings", "listings", "contracts", "contracts"]);
  expect(requests.every((request) => request.startsWith("api.transient.xyz"))).toBe(true);
  expect(owners).toHaveBeenCalledTimes(1);
});

test("Transient results follow pagination", async () => {
  const data = await transient();
  const [first, second] = data.auctions.results;
  serve((url) => {
    if (!url.pathname.endsWith("/auctions")) return transientRoute(data)(url, undefined);
    if (url.searchParams.get("offset")) return { next: null, results: [second] };
    return { next: `${url.origin}${url.pathname}?offset=100`, results: [first] };
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null });
  expect(result.candidates.filter((candidate) => candidate.group === "listings")).toHaveLength(2);
});

test("a wallet unknown to Transient finds nothing there", async () => {
  serve(() => ({ next: null, results: [] }));
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null });
  expect(result).toEqual({ candidates: [], errors: [], warnings: [] });
  expect(owners).not.toHaveBeenCalled();
});

test("one failing source does not hide the others", async () => {
  const data = await transient();
  const nfts = await fixture("alchemy-nfts");
  serve((url, body) => {
    if (url.host === "api.transient.xyz") return transientRoute(data)(url, body);
    if (url.pathname.includes("/nft/v3/")) return nfts;
    return new Response("rate limited", { status: 429 });
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: "test-key" });
  expect(result.errors).toEqual([{ source: "Tokens (Alchemy)", message: "eth-mainnet.g.alchemy.com returned HTTP 429" }]);
  expect(new Set(result.candidates.map((candidate) => candidate.group))).toEqual(new Set(["nfts", "listings", "contracts"]));
});

test("token metadata is fetched for held tokens only, in one batch", async () => {
  const balances = await fixture("alchemy-token-balances");
  let batch: { params: string[] }[] = [];
  serve((url, body) => {
    if (url.host === "api.transient.xyz") return { next: null, results: [] };
    if (url.pathname.includes("/nft/v3/")) return { ownedNfts: [], pageKey: null };
    if (Array.isArray(body)) {
      batch = body;
      return body.map((call: { id: number }) => ({ id: call.id, result: { name: "Token", symbol: "TKN", decimals: 0 } }));
    }
    return { id: 1, result: balances };
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: "test-key" });
  expect(batch.map((call) => call.params[0])).toEqual([
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0x1111111111111111111111111111111111111111",
  ]);
  expect(result.candidates.map((candidate) => candidate.detail.split(" · ")[0])).toEqual(["5000000", "7"]);
  expect(requests).toContain("eth-mainnet.g.alchemy.com/v2/test-key");
});

test("a token metadata outage keeps the balances, unnamed", async () => {
  const balances = await fixture("alchemy-token-balances");
  serve((url, body) => {
    if (url.host === "api.transient.xyz") return { next: null, results: [] };
    if (url.pathname.includes("/nft/v3/")) return { ownedNfts: [], pageKey: null };
    if (Array.isArray(body)) return new Response("rate limited", { status: 429 });
    return { id: 1, result: balances };
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: "test-key" });
  expect(result.errors).toEqual([]);
  expect(result.candidates.map((candidate) => candidate.detail.split(" · ")[0])).toEqual([
    "5000000 base units",
    "7 base units",
  ]);
  expect(result.warnings.map((w) => w.message)[0]).toStartWith("could not read token names and decimals");
});

test("an owner() RPC outage keeps the listings and marks the contracts", async () => {
  serve(transientRoute(await transient()));
  const result = await scan(
    { network: "mainnet", compromised: COMPROMISED, alchemyKey: null },
    async () => {
      throw new Error("rpc down");
    },
    [],
  );
  expect(result.errors).toEqual([]);
  expect(result.candidates.filter((c) => c.group === "listings").length).toBeGreaterThan(0);
  expect(result.candidates.filter((c) => c.group === "contracts").every((c) => c.blockedReason)).toBe(true);
  expect(result.warnings.map((w) => w.message)).toEqual(["could not read contract owners: rpc down"]);
});

test("a pagination link to another site is not followed", async () => {
  const data = await transient();
  serve((url) => {
    if (!url.pathname.endsWith("/auctions")) return transientRoute(data)(url, undefined);
    return { next: "https://evil.example/auctions?offset=100", results: data.auctions.results };
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null });
  expect(result.errors[0].message).toContain("another site");
  expect(requests.some((request) => request.startsWith("evil.example"))).toBe(false);
});

test("hitting the page limit is reported as incomplete", async () => {
  const data = await transient();
  serve((url) => {
    if (!url.pathname.endsWith("/auctions")) return transientRoute(data)(url, undefined);
    return { next: `${url.origin}${url.pathname}?offset=${Math.random()}`, results: [] };
  });
  const result = await scanWallet({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null });
  expect(result.warnings.map((w) => w.message)).toContain("only the first 0 auctions were read; there are more");
});

const PUNKS = "0xb47e3cd837dDF8e4c57F05d70Ab865de6e193BBB";

test("collection scanners add their own candidates and claim their contract from generic sources", async () => {
  serve((url) => {
    if (url.pathname.endsWith("/getNFTsForOwner")) {
      return {
        ownedNfts: [{ contract: { address: PUNKS, name: "CRYPTOPUNKS" }, tokenId: "78", tokenType: "ERC721" }],
      };
    }
    if (url.pathname.endsWith("/getTokenBalances") || url.host.includes("alchemy")) return { tokenBalances: [] };
    return { results: [], next: null };
  });
  const punks: CollectionScanner = {
    type: "cryptopunk",
    name: "CryptoPunks",
    scan: async ({ network }) => [collectionCandidate(network, "cryptopunk", 78)],
  };
  const result = await scan({ network: "mainnet", compromised: COMPROMISED, alchemyKey: "test-key" }, owners, [punks]);

  expect(result.candidates.map((c) => c.action)).toEqual([{ type: "cryptopunk", token_id: "78", label: "CryptoPunk #78" }]);
});

test("a failing collection scanner is reported on its own", async () => {
  serve(transientRoute({ users: { results: [], next: null }, auctions: { results: [], next: null }, contracts: { results: [], next: null } }));
  const broken: CollectionScanner = {
    type: "superrare_v1",
    name: "SuperRare V1",
    scan: async () => {
      throw new Error("execution reverted");
    },
  };
  const result = await scan({ network: "mainnet", compromised: COMPROMISED, alchemyKey: null }, owners, [broken]);

  expect(result.errors).toEqual([{ source: "SuperRare V1", message: "execution reverted" }]);
});
