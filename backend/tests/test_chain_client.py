"""SolanaClient.send must tell "the chain refused this" apart from "the RPC did not answer".

A rate-limited or unreachable RPC is not a refusal: reporting it as one would let a
network error stand in for an onchain check. Transport errors are retried, and if
they persist they surface as RpcUnavailable, which is not a ChainError.
"""

import asyncio
from types import SimpleNamespace

import pytest
from solana.exceptions import SolanaRpcException
from solana.rpc.core import RPCException
from solders.hash import Hash
from solders.keypair import Keypair
from solders.signature import Signature
from solders.system_program import TransferParams, transfer

from app.chain.client import ChainError, RpcUnavailable, SolanaClient


def transport_error() -> SolanaRpcException:
    class GetSignatureStatuses:  # solana-py names the request body's class in its message
        pass

    # the arguments solana-py's own decorator passes: (error, function, provider, request body)
    return SolanaRpcException(RuntimeError("429 Too Many Requests"), lambda: None, object(), GetSignatureStatuses())


class FakeRpc:
    def __init__(self, send_failures=(), confirm_failures=()):
        self.send_failures = list(send_failures)
        self.confirm_failures = list(confirm_failures)
        self.sends = 0
        self.confirms = 0

    async def get_latest_blockhash(self):
        return SimpleNamespace(value=SimpleNamespace(blockhash=Hash.default(), last_valid_block_height=1))

    async def send_transaction(self, tx):
        self.sends += 1
        if self.send_failures:
            raise self.send_failures.pop(0)
        return SimpleNamespace(value=Signature.default())

    async def confirm_transaction(self, signature, commitment=None, last_valid_block_height=None):
        self.confirms += 1
        if self.confirm_failures:
            raise self.confirm_failures.pop(0)
        return SimpleNamespace(value=[SimpleNamespace(err=None)])

    async def close(self):
        pass


def client_with(rpc: FakeRpc) -> SolanaClient:
    client = SolanaClient("http://127.0.0.1:1", retry_delays=(0, 0, 0))
    client.rpc = rpc
    return client


def send(client: SolanaClient) -> str:
    payer = Keypair()
    instruction = transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))
    return asyncio.run(client.send([instruction], payer))


def test_a_transport_error_is_retried_and_the_send_succeeds():
    rpc = FakeRpc(send_failures=[transport_error()], confirm_failures=[transport_error()])
    assert send(client_with(rpc)) == str(Signature.default())
    assert rpc.sends == 2 and rpc.confirms == 2


def test_a_persistent_transport_error_is_not_reported_as_a_refusal():
    rpc = FakeRpc(send_failures=[transport_error() for _ in range(10)])
    with pytest.raises(RpcUnavailable) as caught:
        send(client_with(rpc))
    assert not isinstance(caught.value, ChainError)
    assert rpc.sends == 4  # the first try and three retries


def test_a_rejection_from_the_chain_is_a_chain_error_and_is_not_retried():
    rpc = FakeRpc(send_failures=[RPCException(SimpleNamespace(message="custom program error", data=None))])
    with pytest.raises(ChainError):
        send(client_with(rpc))
    assert rpc.sends == 1
