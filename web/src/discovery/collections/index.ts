import { cryptoPunks } from "./cryptopunks";
import { superRareV1 } from "./superrareV1";
import type { CollectionScanner } from "./types";

export type { CollectionScanner } from "./types";

export const COLLECTION_SCANNERS: CollectionScanner[] = [cryptoPunks, superRareV1];
