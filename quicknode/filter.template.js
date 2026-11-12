// QuickNode Streams filter for Agent Base.
//
// Network: Solana devnet.  Dataset: block.
//
// Keeps only transactions in which a watched owner's token balance appears:
// the vault's own token account, and every agent hot wallet. For each one it
// emits exactly what the backend needs to compute money movement, copied from
// the transaction's meta without interpretation:
//
//   { "transactions": [ { signature, slot, blockTime, err,
//                         preTokenBalances, postTokenBalances } ] }
//
// Returning null for a block with no matches means nothing is delivered for it.
//
// WATCHED is filled in by `python -m scripts.quicknode_filter`. Regenerate and
// update the stream when you add an agent, so its hot wallet is watched too.

const WATCHED = new Set(__WATCHED__);

function touchesWatched(meta) {
  const balances = (meta.preTokenBalances || []).concat(meta.postTokenBalances || []);
  for (const balance of balances) {
    if (balance && WATCHED.has(balance.owner)) return true;
  }
  return false;
}

function main(stream) {
  const blocks = Array.isArray(stream.data) ? stream.data : [stream.data];
  const transactions = [];

  for (const block of blocks) {
    if (!block || !block.transactions) continue;
    for (const tx of block.transactions) {
      if (!tx.meta || !touchesWatched(tx.meta)) continue;
      transactions.push({
        signature: tx.transaction.signatures[0],
        slot: block.parentSlot !== undefined ? block.parentSlot + 1 : null,
        blockTime: block.blockTime !== undefined ? block.blockTime : null,
        err: tx.meta.err || null,
        preTokenBalances: tx.meta.preTokenBalances || [],
        postTokenBalances: tx.meta.postTokenBalances || [],
      });
    }
  }

  return transactions.length ? { transactions } : null;
}
