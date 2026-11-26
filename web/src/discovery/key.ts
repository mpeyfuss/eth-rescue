const STORAGE_KEY = "eth-rescue:alchemy-key";

// The site's own key is bundled into the page on purpose: it is restricted to this site's domain.
export const BUILT_IN_KEY: string | null = import.meta.env.VITE_ALCHEMY_API_KEY || null;

export function loadUserKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function saveUserKey(key: string | null) {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Without storage the key lasts until the page reloads.
  }
}
