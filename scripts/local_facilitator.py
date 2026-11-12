"""A self-hosted x402 V2 facilitator for the LOCAL validator only.

    python -m scripts.local_facilitator          # http://127.0.0.1:4022

The public facilitator (x402.org) settles on devnet and cannot see a local
validator. This one runs the same `exact` SVM verification and settlement from
the official `x402` package against http://127.0.0.1:8899, so the whole paid
request (402 challenge -> signed payment -> verify -> settle -> content) can be
exercised without devnet funds. It refuses any RPC that is not on this machine.

It advertises devnet's CAIP-2 id as the network name, because that is what the
seller and buyer are configured with; the transactions land on the local
validator. Everything run this way is labelled LOCALNET.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402 import x402Facilitator
from x402.mechanisms.svm.exact.register import register_exact_svm_facilitator
from x402.mechanisms.svm.signers import FacilitatorKeypairSigner
from x402.schemas.responses import SettleRequest, VerifyRequest

from app.chain.client import load_keypair, save_new_keypair
from app.config import DEVNET_CAIP2, REPO_ROOT

KEY_PATH = REPO_ROOT / ".keys" / "local-facilitator.json"
PORT = 4022


def create_app(rpc_url: str) -> FastAPI:
    if urlsplit(rpc_url).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"local facilitator: refusing {rpc_url}; it only serves a local validator")
    keypair = load_keypair(KEY_PATH) if KEY_PATH.exists() else save_new_keypair(KEY_PATH)
    facilitator = register_exact_svm_facilitator(
        x402Facilitator(),
        FacilitatorKeypairSigner(keypair, rpc_url=rpc_url),
        networks=DEVNET_CAIP2,
        # Legacy/v0 messages only: the local test validator rejects the newer v1 format.
        transaction_versions=[0],
    )
    app = FastAPI(title="x402 facilitator (LOCALNET only)", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/supported")
    async def supported():
        return facilitator.get_supported().model_dump(mode="json", by_alias=True, exclude_none=True)

    @app.post("/verify")
    async def verify(request: Request):
        body = VerifyRequest.model_validate(await request.json())
        result = await facilitator.verify(body.payment_payload, body.payment_requirements)
        return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))

    @app.post("/settle")
    async def settle(request: Request):
        body = SettleRequest.model_validate(await request.json())
        result = await facilitator.settle(body.payment_payload, body.payment_requirements)
        return JSONResponse(result.model_dump(mode="json", by_alias=True, exclude_none=True))

    print(f"x402 facilitator (LOCALNET only) fee payer {keypair.pubkey()} rpc {rpc_url}")
    return app


def main() -> int:
    rpc_url = os.environ.get("SOLANA_RPC_URL", "http://127.0.0.1:8899")
    uvicorn.run(create_app(rpc_url), host="127.0.0.1", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
