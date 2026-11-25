export type Os = "macos" | "linux" | "windows";

// @latest so a cached older eth-rescue (with a different plan format) is never reused.
export const CLI = "uvx eth-rescue@latest";
export const PLAN_FILE = "plan.json";

export const COMMANDS = {
  check: `${CLI} --help`,
  gasWallet: `${CLI} gas-wallet new`,
  validate: `${CLI} validate ${PLAN_FILE}`,
  run: `${CLI} run ${PLAN_FILE}`,
};

export const UV_DOCS = "https://docs.astral.sh/uv/getting-started/installation/";

interface OsGuide {
  label: string;
  terminal: string;
  installUv: string;
}

export const OS_GUIDES: Record<Os, OsGuide> = {
  macos: {
    label: "macOS",
    terminal: "the Terminal app (Applications → Utilities → Terminal)",
    installUv: "curl -LsSf https://astral.sh/uv/install.sh | sh",
  },
  linux: {
    label: "Linux",
    terminal: "a terminal",
    installUv: "curl -LsSf https://astral.sh/uv/install.sh | sh",
  },
  windows: {
    label: "Windows",
    terminal: "PowerShell (Start menu → PowerShell)",
    installUv: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
  },
};

export function detectOs(userAgent: string): Os {
  if (/windows/i.test(userAgent)) return "windows";
  if (/mac os|macintosh/i.test(userAgent)) return "macos";
  return "linux";
}

export function assistantPrompt(os: Os): string {
  const guide = OS_GUIDES[os];
  return `I'm using eth-rescue, an open-source command-line tool that moves assets out of a compromised Ethereum wallet with Flashbots bundles. I built my rescue plan (${PLAN_FILE}) with the eth-rescue plan builder website. Please walk me through installing and running it on ${guide.label}, one step at a time, and wait for me to tell you what happened before moving on. I'm not a developer, so tell me exactly where to type each command.

Use only these steps and commands:
1. Open ${guide.terminal}.
2. Install uv, the Python tool runner from Astral (${UV_DOCS}):
   ${guide.installUv}
   Then close the terminal and open a new one.
3. Check that eth-rescue runs. The first run downloads eth-rescue and a compatible Python automatically:
   ${COMMANDS.check}
4. Optional: create a fresh gas wallet to pay for the rescue. It prints a new address and private key once:
   ${COMMANDS.gasWallet}
5. Go to the folder that contains ${PLAN_FILE}, then run:
   ${COMMANDS.validate}
   ${COMMANDS.run}
   The run command asks for private keys in a hidden prompt, simulates the rescue, tells me how much ETH the gas wallet needs, and asks once before sending.

Safety rules you must follow:
- Never ask me for a private key or seed phrase, and never tell me to put one in this chat, in a command, or in a file. eth-rescue asks for keys itself in a hidden prompt, and I will only type them there.
- If I paste a private key or seed phrase into this chat anyway, tell me to stop and to treat that wallet as compromised.
- When the gas-wallet command prints a private key, tell me to save it somewhere safe and not to share it with you.
- Don't suggest other packages, scripts, websites, browser extensions or wallet tools, and don't change these commands. The package name is exactly "eth-rescue"; similarly named packages could be scams.
- If a command fails, help me understand the error without changing what we are installing.`;
}
