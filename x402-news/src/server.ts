/**
 * x402 V2 paid news endpoint on Solana devnet.
 *
 *   npm start        (reads ../.env)
 *
 * Refuses to start without a payee: this service never serves news unpaid.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { HTTPFacilitatorClient } from "@x402/core/server";
import { paymentMiddleware, x402ResourceServer } from "@x402/express";
import { ExactSvmScheme } from "@x402/svm/exact/server";
import dotenv from "dotenv";

import { createApp } from "./app.ts";
import { Feed, parseRecordedFeed } from "./feed.ts";

const here = dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: resolve(here, "../../.env"), quiet: true });

const DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1";

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    console.error(`x402-news: ${name} is not set (see .env.example)`);
    process.exit(1);
  }
  return value;
}

const feedMode = process.env.FEED_MODE?.trim() || "recorded";
if (feedMode !== "recorded") {
  console.error(
    `x402-news: FEED_MODE=${feedMode} is not available. Only "recorded" is implemented; ` +
      "live X search is a stretch goal and needs paid X API credits.",
  );
  process.exit(1);
}

const network = (process.env.SOLANA_NETWORK_CAIP2?.trim() || DEVNET) as `${string}:${string}`;
const facilitatorUrl = process.env.X402_FACILITATOR_URL?.trim() || "https://x402.org/facilitator";
const payTo = required("X402_PAY_TO");
const price = process.env.X402_NEWS_PRICE?.trim() || "$0.001";
const port = Number.parseInt(process.env.X402_NEWS_PORT?.trim() || "4021", 10);
const adminToken = process.env.NEWS_ADMIN_TOKEN?.trim() ?? "";

const entries = parseRecordedFeed(readFileSync(resolve(here, "../feed/recorded.json"), "utf8"));
const feed = new Feed(entries, {
  now: Date.now,
  substitutions: { ATTACKER_ADDRESS: required("DEMO_ATTACKER_ADDRESS") },
});

const facilitator = new HTTPFacilitatorClient({ url: facilitatorUrl });
const resourceServer = new x402ResourceServer(facilitator).register(network, new ExactSvmScheme());
const paywall = paymentMiddleware(
  {
    "GET /news": {
      accepts: [{ scheme: "exact", price, network, payTo }],
      description: "Recent posts and headlines for a market topic",
      mimeType: "application/json",
    },
  },
  resourceServer,
);

const app = createApp({ feed, feedMode: "recorded", paywall, adminToken });

// Loopback only: the admin routes must never be reachable from another machine.
app.listen(port, "127.0.0.1", () => {
  console.log(`x402-news listening on http://127.0.0.1:${port}`);
  console.log(`  feed        RECORDED FEED (${entries.length} posts, ${entries.filter((e) => e.planted).length} planted attacks)`);
  console.log(`  price       ${price} USDC per GET /news on ${network}`);
  console.log(`  pay to      ${payTo}`);
  console.log(`  facilitator ${facilitatorUrl}`);
  console.log(`  admin       ${adminToken ? "enabled (local + token)" : "disabled"}`);
});
