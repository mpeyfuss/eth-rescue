import { expect, test } from "bun:test";
import { parseEip7702Delegate } from "../src/discovery/onchain";
import { formatEth, formatTokenAmount, parseHumanAmount, shortAddress } from "../src/plan/format";

test("shortAddress formats 42-char addresses cleanly", () => {
  expect(shortAddress("0xd068c6A6db349A8Ce9C7aD3706391e53417abF61")).toBe("0xd068…bF61");
});

test("formatTokenAmount and parseHumanAmount convert accurately without floating-point loss", () => {
  expect(formatTokenAmount("1250500000", 6)).toBe("1,250.5");
  expect(formatTokenAmount("1000000000000000000", 18)).toBe("1");
  expect(parseHumanAmount("1250.5", 6)).toBe("1250500000");
  expect(parseHumanAmount("1,250.5", 6)).toBe("1250500000");
  expect(parseHumanAmount("0.0000001", 6)).toBeNull();
  expect(parseHumanAmount("0", 18)).toBeNull();
});

test("formatEth formats wei values cleanly", () => {
  expect(formatEth(0n)).toBe("0 ETH");
  expect(formatEth(50_000_000_000_000n)).toBe("< 0.0001 ETH");
  expect(formatEth(42_500_000_000_000_000n)).toBe("0.0425 ETH");
  expect(formatEth(2_000_000_000_000_000_000n)).toBe("2 ETH");
});

test("parseEip7702Delegate detects EIP-7702 delegation bytecode", () => {
  const delegate = "0xdC7eC38149c572a29Ac89813eda47140Ac8cD969";
  expect(parseEip7702Delegate(`0xef0100${delegate.slice(2).toLowerCase()}`)).toBe(delegate);
  expect(parseEip7702Delegate("0x")).toBeNull();
  expect(parseEip7702Delegate("0x6080604052")).toBeNull();
});
