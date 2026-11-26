import { expect, test } from "bun:test";
import vectors from "../../tests/fixtures/abi_vectors.json";
import { encodeCustomCall } from "../src/plan/custom";

interface Vector {
  signature: string;
  args: unknown[];
  calldata?: string;
  error?: string;
}

// The same cases tests/test_calldata.py runs against the CLI encoder.
for (const vector of vectors as Vector[]) {
  const name = `${vector.signature} ${JSON.stringify(vector.args).slice(0, 60)}`;
  if (vector.calldata) {
    test(`encodes like the CLI: ${name}`, () => {
      expect(encodeCustomCall(vector.signature, vector.args)).toBe(vector.calldata as `0x${string}`);
    });
  } else {
    test(`rejects like the CLI (${vector.error}): ${name}`, () => {
      expect(() => encodeCustomCall(vector.signature, vector.args)).toThrow();
    });
  }
}

test("unquoted integers beyond 2^53 are rejected rather than rounded", () => {
  expect(() => encodeCustomCall("f(uint256[])", [[2 ** 60]])).toThrow(/not an exact integer/);
  expect(encodeCustomCall("f(uint256[])", [["1152921504606846976"]])).toContain("1000000000000000");
});

test("non-canonical signatures name the canonical form", () => {
  expect(() => encodeCustomCall("f(uint)", ["1"])).toThrow("use f(uint256)");
});
