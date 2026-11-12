# QuickNode Stream

**State: the stream has not been created yet.** It needs a QuickNode account and the vault deployed on devnet. Everything on the receiving side is built and tested, and the filter is tested under Node against the backend's parser (`backend/tests/test_quicknode_filter.py`).

## Configuration

| Setting | Value |
|---|---|
| Network | Solana devnet |
| Dataset | `block` |
| Start | latest block (the free plan follows the chain tip) |
| Filter | `quicknode/filter.js`, generated from `filter.template.js` |
| Destination | Webhook: `POST https://<your-tunnel>/webhooks/quicknode` |
| Security token | copy from the stream's Settings tab into `.env` as `QUICKNODE_STREAM_SECURITY_TOKEN` |
| Compression | either; the receiver handles gzip |

## Why a stream

The policy engine sees only actions that go through it. A stolen agent key does not go through it. The stream reports what actually happened onchain to the vault and to every agent hot wallet, so the backend can compare it with what it allowed. Money that left a hot wallet with no matching allowed action is an anomaly, and an anomaly freezes the agent onchain through the guardian key.

## Filter logic

[`filter.template.js`](filter.template.js). For each block, keep a transaction if the vault account or any agent hot wallet appears as the owner of a token balance in it. For each kept transaction emit:

```json
{ "signature": "...", "slot": 123, "blockTime": 1700000000, "err": null,
  "preTokenBalances": [ ... ], "postTokenBalances": [ ... ] }
```

wrapped as `{ "transactions": [ ... ] }`. A block with no matches returns `null`, so nothing is delivered.

The balances are copied from the transaction's meta without interpretation. All the judgement (who lost how much, to whom, was it expected) happens in the backend, in Python, where it is unit-tested.

Generate the filter with the current wallets:

```bash
.venv/Scripts/python -m scripts.quicknode_filter
```

Add `--base64` to print it encoded for the Streams REST API. Regenerate it and update the stream when you create a new agent; a wallet that is not in the list is not watched.

## Destination

The webhook receiver listens on `127.0.0.1:8788` and serves only `POST /webhooks/quicknode`. Expose that port, and only that port, with a tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8788
```

Never tunnel port 8787 (the operator API).

## Verification of each delivery

`backend/app/quicknode/verify.py`, in this order:

1. `X-QN-Signature` equals `hex(HMAC-SHA256(token, X-QN-Nonce + X-QN-Timestamp + body))`, computed over the un-gzipped body, compared in constant time.
2. `X-QN-Timestamp` is within five minutes of the receiver's clock.
3. `X-QN-Nonce` has not been used. It is remembered only after the signature passed.

With no token configured every delivery is refused. Bodies are capped at 5 MB before and after decompression.

One thing to confirm with the first real delivery: QuickNode's guide does not state the timestamp's unit. The receiver accepts epoch seconds and epoch milliseconds.

## On a local validator

QuickNode cannot stream a validator on your machine. There, `backend/app/quicknode/local_relay.py` watches the same token accounts over RPC, builds the same payload, signs it the same way and posts it to the same webhook. It is labelled `LOCAL RELAY (not QuickNode)` in the UI and in `/config`, and refuses to run against a non-local RPC.
